from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from PIL import Image

from light_image_editor.domain.conversion_checkpoint import (
    ConversionCheckpointFailure,
    ConversionSessionCheckpointWriter,
)
from light_image_editor.domain.conversion_jobs import ConversionQueue
from light_image_editor.domain.conversion_runner import ConversionRunner
from light_image_editor.domain.conversion_session import (
    ConversionSession,
    ConversionSessionStore,
)
from light_image_editor.domain.recipes import ExportProfile, TransformRecipe


def _session(root: Path) -> ConversionSession:
    source = root / "source.png"
    image = Image.new("RGB", (8, 6), "purple")
    image.save(source, "PNG")
    image.close()
    queue = ConversionQueue()
    queue.add_file(source)
    recipe = TransformRecipe(
        name="检查点",
        export_profile=ExportProfile(
            output_format="PNG",
            rename_template="{stem}_out",
            conflict_policy="error",
        ),
    )
    runner = ConversionRunner(queue, recipe, root / "output")
    return replace(
        ConversionSession.capture(queue, runner),
        saved_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
    )


class _BlockingStore:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[ConversionSession] = []
        self._lock = threading.Lock()
        self._actual = ConversionSessionStore()

    def save(self, session: ConversionSession, path: str | Path) -> Path:
        with self._lock:
            first_call = not self.calls
            self.calls.append(session)
        if first_call:
            self.started.set()
            if not self.release.wait(5.0):
                raise TimeoutError("测试未释放首个写入")
        return self._actual.save(session, path)


class _FailingStore:
    def __init__(self, error: OSError) -> None:
        self.error = error

    def save(self, session: ConversionSession, path: str | Path) -> Path:
        del session, path
        raise self.error


class ConversionSessionCheckpointWriterTests(unittest.TestCase):
    def test_high_frequency_submits_coalesce_to_latest_pending_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _session(root)
            second = replace(first, saved_at=first.saved_at + timedelta(seconds=1))
            latest = replace(first, saved_at=first.saved_at + timedelta(seconds=2))
            store = _BlockingStore()
            path = root / "checkpoint.json"
            writer = ConversionSessionCheckpointWriter(
                path,
                store=cast(ConversionSessionStore, store),
            )

            self.assertEqual(writer.submit(first), 1)
            self.assertTrue(store.started.wait(2.0))
            self.assertEqual(writer.submit(second), 2)
            self.assertEqual(writer.submit(latest), 3)
            store.release.set()

            self.assertTrue(writer.close(timeout=5.0))
            self.assertEqual(
                [item.saved_at for item in store.calls], [first.saved_at, latest.saved_at]
            )
            self.assertEqual(writer.persisted_generation, 3)
            restored = ConversionSessionStore().load(path)
            self.assertEqual(restored.saved_at, latest.saved_at)

    def test_flush_waits_until_the_latest_snapshot_is_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _session(root)
            latest = replace(first, saved_at=first.saved_at + timedelta(minutes=1))
            path = root / "checkpoint.json"
            writer = ConversionSessionCheckpointWriter(path)

            writer.submit(first)
            latest_generation = writer.submit(latest)

            self.assertTrue(writer.flush(timeout=5.0))
            self.assertGreaterEqual(writer.persisted_generation, latest_generation)
            self.assertEqual(ConversionSessionStore().load(path).saved_at, latest.saved_at)
            self.assertTrue(writer.close(timeout=5.0))

    def test_clear_runs_after_active_save_and_removes_stale_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = _BlockingStore()
            path = root / "checkpoint.json"
            writer = ConversionSessionCheckpointWriter(
                path,
                store=cast(ConversionSessionStore, store),
            )

            writer.submit(_session(root))
            self.assertTrue(store.started.wait(2.0))
            clear_generation = writer.clear()
            store.release.set()

            self.assertTrue(writer.close(timeout=5.0))
            self.assertGreaterEqual(writer.persisted_generation, clear_generation)
            self.assertFalse(path.exists())

    def test_failure_is_available_through_property_and_callback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected_error = OSError("磁盘不可写")
            callbacks: list[ConversionCheckpointFailure] = []
            callback_called = threading.Event()

            def on_error(failure: ConversionCheckpointFailure) -> None:
                callbacks.append(failure)
                callback_called.set()

            writer = ConversionSessionCheckpointWriter(
                root / "checkpoint.json",
                store=cast(ConversionSessionStore, _FailingStore(expected_error)),
                on_error=on_error,
            )
            generation = writer.submit(_session(root))

            self.assertFalse(writer.flush(timeout=5.0))
            self.assertTrue(callback_called.wait(2.0))
            self.assertIs(writer.last_error, expected_error)
            failure = writer.last_failure
            self.assertIsNotNone(failure)
            assert failure is not None
            self.assertEqual(failure.generation, generation)
            self.assertEqual(callbacks, [failure])
            self.assertFalse(writer.close(timeout=5.0))

    def test_error_callback_can_request_close_without_deadlocking_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            callback_finished = threading.Event()
            close_results: list[bool] = []
            writer: ConversionSessionCheckpointWriter

            def on_error(failure: ConversionCheckpointFailure) -> None:
                del failure
                close_results.append(writer.close())
                callback_finished.set()

            writer = ConversionSessionCheckpointWriter(
                root / "checkpoint.json",
                store=cast(ConversionSessionStore, _FailingStore(OSError("失败"))),
                on_error=on_error,
            )
            writer.submit(_session(root))

            self.assertTrue(callback_finished.wait(2.0))
            self.assertEqual(close_results, [False])
            self.assertFalse(writer.close(timeout=5.0))

    def test_close_is_idempotent_and_rejects_later_submissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = ConversionSessionCheckpointWriter(root / "checkpoint.json")

            self.assertTrue(writer.close(timeout=5.0))
            self.assertTrue(writer.close(timeout=5.0))
            self.assertTrue(writer.closed)
            self.assertTrue(writer.flush(timeout=0.0))
            with self.assertRaises(RuntimeError):
                writer.submit(_session(root))


if __name__ == "__main__":
    unittest.main()
