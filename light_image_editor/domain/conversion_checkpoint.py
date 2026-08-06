"""Coalescing background checkpoints for conversion sessions."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .conversion_session import ConversionSession, ConversionSessionStore


@dataclass(frozen=True, slots=True)
class ConversionCheckpointFailure:
    """One failed background persistence attempt."""

    generation: int
    path: Path
    error: BaseException


ConversionCheckpointErrorCallback = Callable[[ConversionCheckpointFailure], object]


class ConversionSessionCheckpointWriter:
    """Persist the newest conversion snapshot on one serial worker thread.

    At most one save is active and one snapshot is pending. Repeated ``submit``
    calls replace that pending snapshot, so a slow disk cannot create an
    unbounded checkpoint backlog. Because all saves use the same worker, an old
    snapshot can never finish after (and overwrite) a newer one.

    ``flush`` and ``close`` return ``True`` only when the newest snapshot known
    at the start of the call, or a later snapshot that supersedes it, has been
    saved successfully. A timeout or terminal save failure returns ``False``.
    Details of the most recent save failure remain available through
    ``last_failure`` and ``last_error``.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        store: ConversionSessionStore | None = None,
        on_error: ConversionCheckpointErrorCallback | None = None,
    ) -> None:
        if on_error is not None and not callable(on_error):
            raise TypeError("on_error 必须可调用或为 None")
        self._path = Path(path)
        self._store = store if store is not None else ConversionSessionStore()
        self._on_error = on_error
        self._condition = threading.Condition()
        self._pending: tuple[int, ConversionSession | None] | None = None
        self._active_generation: int | None = None
        self._submitted_generation = 0
        self._settled_generation = 0
        self._persisted_generation = 0
        self._last_failure: ConversionCheckpointFailure | None = None
        self._closing = False
        self._worker_stopped = False
        self._worker = threading.Thread(
            target=self._run,
            name="conversion-session-checkpoint",
            daemon=True,
        )
        self._worker.start()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def submitted_generation(self) -> int:
        with self._condition:
            return self._submitted_generation

    @property
    def persisted_generation(self) -> int:
        with self._condition:
            return self._persisted_generation

    @property
    def last_failure(self) -> ConversionCheckpointFailure | None:
        with self._condition:
            return self._last_failure

    @property
    def last_error(self) -> BaseException | None:
        failure = self.last_failure
        return None if failure is None else failure.error

    @property
    def closed(self) -> bool:
        """Whether closing has been requested and new submissions are rejected."""

        with self._condition:
            return self._closing

    def submit(self, session: ConversionSession) -> int:
        """Queue ``session`` and return its monotonically increasing generation."""

        if not isinstance(session, ConversionSession):
            raise TypeError("session 必须是 ConversionSession")
        with self._condition:
            if self._closing:
                raise RuntimeError("检查点写入器已关闭")
            self._submitted_generation += 1
            generation = self._submitted_generation
            self._pending = (generation, session)
            self._condition.notify()
            return generation

    def clear(self) -> int:
        """Serialize removal after any active save and supersede a pending save."""

        with self._condition:
            if self._closing:
                raise RuntimeError("检查点写入器已关闭")
            self._submitted_generation += 1
            generation = self._submitted_generation
            self._pending = (generation, None)
            self._condition.notify()
            return generation

    def flush(self, timeout: float | None = None) -> bool:
        """Wait for the latest submitted snapshot to be durably attempted."""

        deadline = _deadline(timeout)
        with self._condition:
            target = self._submitted_generation
            if threading.current_thread() is self._worker:
                return self._persisted_generation >= target
        return self._wait_for_persistence(target, deadline)

    def close(self, timeout: float | None = None) -> bool:
        """Reject new work, drain the newest snapshot, and stop the worker.

        The method is idempotent. If a bounded call times out, another ``close``
        call may wait for the already-requested shutdown to finish.
        """

        deadline = _deadline(timeout)
        with self._condition:
            self._closing = True
            target = self._submitted_generation
            self._condition.notify_all()
            if threading.current_thread() is self._worker:
                return self._persisted_generation >= target

        persisted = self._wait_for_persistence(target, deadline)
        self._worker.join(_remaining(deadline))
        return persisted and not self._worker.is_alive()

    def _wait_for_persistence(self, target: int, deadline: float | None) -> bool:
        if target == 0:
            return True
        with self._condition:
            while self._persisted_generation < target:
                unresolved = self._active_generation is not None or self._pending is not None
                if self._worker_stopped:
                    return False
                if self._settled_generation >= target and not unresolved:
                    return False
                remaining = _remaining(deadline)
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    while self._pending is None and not self._closing:
                        self._condition.wait()
                    if self._pending is None:
                        return
                    generation, session = self._pending
                    self._pending = None
                    self._active_generation = generation

                failure: ConversionCheckpointFailure | None = None
                try:
                    if session is None:
                        self._path.unlink(missing_ok=True)
                    else:
                        self._store.save(session, self._path)
                except BaseException as error:
                    failure = ConversionCheckpointFailure(
                        generation=generation,
                        path=self._path,
                        error=error.with_traceback(None),
                    )

                with self._condition:
                    self._active_generation = None
                    self._settled_generation = max(self._settled_generation, generation)
                    if failure is None:
                        self._persisted_generation = max(
                            self._persisted_generation,
                            generation,
                        )
                    else:
                        self._last_failure = failure
                    self._condition.notify_all()

                if failure is not None and self._on_error is not None:
                    try:
                        self._on_error(failure)
                    except BaseException:
                        # Error reporting must not stop persistence of a newer snapshot.
                        pass
        finally:
            with self._condition:
                self._active_generation = None
                self._worker_stopped = True
                self._condition.notify_all()


def _deadline(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("timeout 必须是数字或 None")
    if timeout < 0:
        raise ValueError("timeout 不能为负数")
    return time.monotonic() + timeout


def _remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())
