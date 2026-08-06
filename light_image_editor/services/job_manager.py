"""Durable asynchronous execution for provider-neutral AI requests."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from light_image_editor.domain import (
    AIRequest,
    AssetRecord,
    AssetRevision,
    AssetStorageKind,
    ContentEditRequest,
    GenerationRequest,
    JobError,
    JobRequestKind,
    JobSnapshot,
    JobStatus,
    ManagedJobSnapshot,
    Provenance,
)
from light_image_editor.providers import AIProvider

JOB_MANAGER_SCHEMA_VERSION = 1
DEFAULT_JOB_STORE_MAX_BYTES = 32 * 1024 * 1024


class JobManagerError(RuntimeError):
    """Base error for the local asynchronous job service."""


class JobManagerClosedError(JobManagerError):
    pass


class JobManagerJobNotFoundError(JobManagerError):
    pass


class JobManagerIdempotencyConflictError(JobManagerError):
    pass


class JobManagerPersistenceError(JobManagerError):
    pass


class JobManager:
    """Persist before submission, then isolate every provider call on workers.

    The manager deliberately never replays an interrupted submission. On restart,
    local ``submitting`` and ``running`` records become terminal ``unknown`` records;
    callers may inspect them, but only a future explicit recovery workflow may decide
    how to reconcile them with a provider.
    """

    def __init__(
        self,
        provider: AIProvider,
        store_path: str | Path,
        *,
        max_workers: int = 2,
        max_store_bytes: int = DEFAULT_JOB_STORE_MAX_BYTES,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("后台任务并发数必须是正整数")
        if (
            isinstance(max_store_bytes, bool)
            or not isinstance(max_store_bytes, int)
            or max_store_bytes < 1
        ):
            raise ValueError("任务记录容量上限必须是正整数")
        if not isinstance(provider, AIProvider):
            raise TypeError("provider 必须实现 AIProvider 协议")

        self._provider = provider
        self._provider_id = provider.manifest.provider_id
        self._store_path = Path(store_path).expanduser().resolve(strict=False)
        self._max_store_bytes = max_store_bytes
        self._condition = threading.Condition(threading.RLock())
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="image-job",
        )
        self._jobs: dict[str, ManagedJobSnapshot] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        self._futures: dict[str, Future[None]] = {}
        self._cancel_requested: set[str] = set()
        self._closed = False
        self._load_store()

    @property
    def store_path(self) -> Path:
        return self._store_path

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def submit_generation(self, request: GenerationRequest) -> ManagedJobSnapshot:
        if not isinstance(request, GenerationRequest):
            raise TypeError("生成任务必须使用 GenerationRequest")
        return self._submit(request, JobRequestKind.GENERATION)

    def submit_edit(self, request: ContentEditRequest) -> ManagedJobSnapshot:
        if not isinstance(request, ContentEditRequest):
            raise TypeError("改图任务必须使用 ContentEditRequest")
        return self._submit(request, JobRequestKind.EDIT)

    def get_job(self, job_id: str) -> ManagedJobSnapshot:
        with self._condition:
            return self._get_locked(job_id)

    snapshot = get_job

    def list_jobs(self) -> tuple[ManagedJobSnapshot, ...]:
        with self._condition:
            return tuple(self._jobs.values())

    def wait(self, job_id: str, timeout: float | None = None) -> ManagedJobSnapshot:
        """Wait for local worker activity, returning remote queued/running snapshots too."""

        if timeout is not None and timeout < 0:
            raise ValueError("等待时间不能为负数")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                snapshot = self._get_locked(job_id)
                future = self._futures.get(job_id)
                if snapshot.status.terminal or future is None or future.done():
                    return snapshot
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("等待图像任务完成超时")
                self._condition.wait(remaining)

    def wait_for_update(
        self,
        job_id: str,
        *,
        after_version: int,
        timeout: float | None = None,
    ) -> ManagedJobSnapshot:
        """Block until a newer immutable snapshot is available."""

        if isinstance(after_version, bool) or after_version < 0:
            raise ValueError("任务版本不能为负数")
        if timeout is not None and timeout < 0:
            raise ValueError("等待时间不能为负数")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                snapshot = self._get_locked(job_id)
                if snapshot.version > after_version:
                    return snapshot
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("等待图像任务状态更新超时")
                self._condition.wait(remaining)

    def cancel(self, job_id: str) -> ManagedJobSnapshot:
        """Request cancellation without invoking provider code on the caller thread."""

        with self._condition:
            self._require_open_locked()
            current = self._get_locked(job_id)
            if current.status.terminal:
                return current
            self._cancel_requested.add(job_id)
            cancelled = self._replace_locked(current, status=JobStatus.CANCELLED)
            active = self._futures.get(job_id)
            if active is not None and active.cancel():
                self._condition.notify_all()
                return cancelled
            if active is None or active.done():
                if cancelled.provider_job_id is not None:
                    self._schedule_locked(job_id, self._run_cancel, cancelled.provider_job_id)
            self._condition.notify_all()
            return cancelled

    def refresh(self, job_id: str) -> ManagedJobSnapshot:
        """Schedule a provider snapshot query; no provider code runs on the caller."""

        with self._condition:
            self._require_open_locked()
            current = self._get_locked(job_id)
            if current.status.terminal or current.provider_job_id is None:
                return current
            active = self._futures.get(job_id)
            if active is None or active.done():
                self._schedule_locked(job_id, self._run_refresh, current.provider_job_id)
            return current

    def shutdown(self, *, wait: bool = True) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def __enter__(self) -> JobManager:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.shutdown()

    def _submit(
        self,
        request: AIRequest,
        request_kind: JobRequestKind,
    ) -> ManagedJobSnapshot:
        key = request.idempotency_key
        if key is None:  # Defensive: requests normalize this in __post_init__.
            raise ValueError("幂等键不能为空")
        fingerprint = request.fingerprint()
        with self._condition:
            self._require_open_locked()
            idempotency = (self._provider_id, key)
            replay = self._idempotency.get(idempotency)
            if replay is not None:
                previous_fingerprint, job_id = replay
                if previous_fingerprint != fingerprint:
                    raise JobManagerIdempotencyConflictError(
                        "该幂等键已用于另一项任务，系统已拒绝重复提交"
                    )
                return self._jobs[job_id]

            now = datetime.now(UTC)
            snapshot = ManagedJobSnapshot(
                job_id=f"local_job_{uuid.uuid4().hex}",
                request_kind=request_kind,
                request_id=request.request_id,
                provider_id=self._provider_id,
                idempotency_key=key,
                request_fingerprint=fingerprint,
                status=JobStatus.SUBMITTING,
                created_at=now,
                updated_at=now,
            )
            self._jobs[snapshot.job_id] = snapshot
            self._idempotency[idempotency] = (fingerprint, snapshot.job_id)
            try:
                # This atomic replace must succeed before the provider worker exists.
                self._persist_locked()
                self._schedule_locked(snapshot.job_id, self._run_submit, request, request_kind)
            except BaseException:
                self._jobs.pop(snapshot.job_id, None)
                self._idempotency.pop(idempotency, None)
                raise
            return snapshot

    def _schedule_locked(
        self,
        job_id: str,
        function: Any,
        *args: object,
    ) -> None:
        try:
            future = self._executor.submit(function, job_id, *args)
        except RuntimeError as error:
            current = self._jobs[job_id]
            self._replace_locked(
                current,
                status=JobStatus.FAILED,
                error=JobError(
                    code="worker_unavailable",
                    message="后台任务服务不可用，任务未提交且不会自动重试",
                ),
            )
            raise JobManagerClosedError("后台任务服务已经关闭") from error
        self._futures[job_id] = future
        future.add_done_callback(partial(self._future_done, job_id))

    def _future_done(self, job_id: str, future: Future[None]) -> None:
        with self._condition:
            if self._futures.get(job_id) is future:
                self._futures.pop(job_id, None)
            self._condition.notify_all()

    def _run_submit(
        self,
        job_id: str,
        request: AIRequest,
        request_kind: JobRequestKind,
    ) -> None:
        with self._condition:
            current = self._jobs[job_id]
            if current.status is JobStatus.CANCELLED:
                return
            self._replace_locked(current, status=JobStatus.RUNNING)
        try:
            if request_kind is JobRequestKind.GENERATION:
                assert isinstance(request, GenerationRequest)
                provider_snapshot = self._provider.submit_generation(request)
            else:
                assert isinstance(request, ContentEditRequest)
                provider_snapshot = self._provider.submit_edit(request)
            if self._cancel_was_requested(job_id) and not provider_snapshot.status.terminal:
                provider_snapshot = self._provider.cancel(provider_snapshot.job_id)
        except Exception as error:
            self._record_provider_failure(job_id, "submit", error)
            return
        self._record_provider_snapshot(job_id, provider_snapshot)

    def _run_cancel(self, job_id: str, provider_job_id: str) -> None:
        try:
            provider_snapshot = self._provider.cancel(provider_job_id)
        except Exception as error:
            self._record_provider_failure(job_id, "cancel", error, unknown=True)
            return
        self._record_provider_snapshot(job_id, provider_snapshot)

    def _run_refresh(self, job_id: str, provider_job_id: str) -> None:
        try:
            provider_snapshot = self._provider.get_job(provider_job_id)
        except Exception as error:
            self._record_provider_failure(job_id, "query", error, unknown=True)
            return
        self._record_provider_snapshot(job_id, provider_snapshot)

    def _record_provider_snapshot(
        self,
        job_id: str,
        provider_snapshot: JobSnapshot,
    ) -> None:
        with self._condition:
            current = self._jobs[job_id]
            if provider_snapshot.provider_id != current.provider_id:
                self._replace_locked(
                    current,
                    status=JobStatus.FAILED,
                    error=JobError(
                        code="provider_identity_mismatch",
                        message="图像服务返回了不匹配的提供方身份，任务已停止",
                    ),
                )
                return
            if provider_snapshot.request_id != current.request_id:
                self._replace_locked(
                    current,
                    status=JobStatus.FAILED,
                    error=JobError(
                        code="provider_request_mismatch",
                        message="图像服务返回了不匹配的请求身份，任务已停止",
                    ),
                )
                return
            if current.status is JobStatus.CANCELLED:
                if provider_snapshot.status is JobStatus.UNKNOWN:
                    self._replace_locked(
                        current,
                        status=JobStatus.UNKNOWN,
                        provider_job_id=provider_snapshot.job_id,
                        error=(
                            provider_snapshot.error
                            or JobError(
                                code="provider_state_unknown",
                                message=(
                                    "取消请求已发出，但图像服务无法确认最终状态；"
                                    "系统不会自动重新提交"
                                ),
                            )
                        ),
                    )
                elif current.provider_job_id is None:
                    self._replace_locked(
                        current,
                        status=JobStatus.CANCELLED,
                        provider_job_id=provider_snapshot.job_id,
                    )
                return
            error = provider_snapshot.error
            if provider_snapshot.status is JobStatus.UNKNOWN and error is None:
                error = JobError(
                    code="provider_state_unknown",
                    message="图像服务无法确认任务状态，系统不会自动重新提交",
                )
            self._replace_locked(
                current,
                status=provider_snapshot.status,
                provider_job_id=provider_snapshot.job_id,
                outputs=provider_snapshot.outputs,
                error=error,
            )

    def _record_provider_failure(
        self,
        job_id: str,
        operation: str,
        _error: Exception,
        *,
        unknown: bool = False,
    ) -> None:
        messages = {
            "submit": "图像服务提交失败，任务已停止且不会自动重试",
            "cancel": "图像服务取消结果未知，系统不会自动提交或重试该任务",
            "query": "图像服务状态查询失败，当前任务状态未知",
        }
        with self._condition:
            current = self._jobs[job_id]
            if current.status is JobStatus.CANCELLED and operation == "submit":
                return
            self._replace_locked(
                current,
                status=JobStatus.UNKNOWN if unknown else JobStatus.FAILED,
                error=JobError(
                    code=f"provider_{operation}_failed",
                    message=messages[operation],
                    retryable=False,
                ),
            )

    def _cancel_was_requested(self, job_id: str) -> bool:
        with self._condition:
            return job_id in self._cancel_requested

    def _replace_locked(
        self,
        current: ManagedJobSnapshot,
        *,
        status: JobStatus,
        provider_job_id: str | None = None,
        outputs: tuple[AssetRecord, ...] = (),
        error: JobError | None = None,
    ) -> ManagedJobSnapshot:
        updated = replace(
            current,
            status=status,
            provider_job_id=(
                current.provider_job_id if provider_job_id is None else provider_job_id
            ),
            outputs=outputs,
            error=error,
            updated_at=datetime.now(UTC),
            version=current.version + 1,
        )
        self._jobs[current.job_id] = updated
        self._persist_locked()
        self._condition.notify_all()
        return updated

    def _get_locked(self, job_id: str) -> ManagedJobSnapshot:
        try:
            return self._jobs[job_id]
        except KeyError as error:
            raise JobManagerJobNotFoundError(f"找不到本地图像任务：{job_id}") from error

    def _require_open_locked(self) -> None:
        if self._closed:
            raise JobManagerClosedError("后台任务服务已经关闭")

    def _load_store(self) -> None:
        if not self._store_path.exists():
            return
        try:
            if self._store_path.stat().st_size > self._max_store_bytes:
                raise JobManagerPersistenceError("任务记录文件超过安全容量上限")
            payload = json.loads(self._store_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"schema_version", "jobs"}:
                raise ValueError("任务记录根结构无效")
            if payload["schema_version"] != JOB_MANAGER_SCHEMA_VERSION:
                raise ValueError("任务记录版本不受支持")
            raw_jobs = payload["jobs"]
            if not isinstance(raw_jobs, list):
                raise ValueError("任务记录列表无效")
            recovered = False
            for raw_job in raw_jobs:
                snapshot = _managed_snapshot_from_json(raw_job)
                if snapshot.job_id in self._jobs:
                    raise ValueError("任务记录包含重复编号")
                idempotency = (snapshot.provider_id, snapshot.idempotency_key)
                if idempotency in self._idempotency:
                    raise ValueError("任务记录包含重复幂等键")
                if snapshot.status in {JobStatus.SUBMITTING, JobStatus.RUNNING}:
                    snapshot = replace(
                        snapshot,
                        status=JobStatus.UNKNOWN,
                        outputs=(),
                        error=JobError(
                            code="interrupted_submission_unknown",
                            message=(
                                "上次退出时任务仍在提交或执行，状态未知；"
                                "为避免重复扣费，系统不会自动重新提交"
                            ),
                        ),
                        updated_at=datetime.now(UTC),
                        version=snapshot.version + 1,
                    )
                    recovered = True
                self._jobs[snapshot.job_id] = snapshot
                self._idempotency[idempotency] = (
                    snapshot.request_fingerprint,
                    snapshot.job_id,
                )
            if recovered:
                with self._condition:
                    self._persist_locked()
        except JobManagerPersistenceError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise JobManagerPersistenceError(
                "无法读取任务记录，文件格式无效、内容已损坏或当前不可访问"
            ) from error

    def _persist_locked(self) -> None:
        payload = {
            "schema_version": JOB_MANAGER_SCHEMA_VERSION,
            "jobs": [_managed_snapshot_to_json(snapshot) for snapshot in self._jobs.values()],
        }
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            if len(encoded) > self._max_store_bytes:
                raise JobManagerPersistenceError("任务记录文件超过安全容量上限")
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self._store_path.name}.",
                suffix=".tmp",
                dir=self._store_path.parent,
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self._store_path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        except JobManagerPersistenceError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise JobManagerPersistenceError("无法保存任务记录，请检查存储位置是否可用") from error


def _managed_snapshot_to_json(snapshot: ManagedJobSnapshot) -> dict[str, object]:
    return {
        "job_id": snapshot.job_id,
        "request_kind": snapshot.request_kind.value,
        "request_id": snapshot.request_id,
        "provider_id": snapshot.provider_id,
        "idempotency_key": snapshot.idempotency_key,
        "request_fingerprint": snapshot.request_fingerprint,
        "status": snapshot.status.value,
        "provider_job_id": snapshot.provider_job_id,
        "outputs": [_asset_to_json(asset) for asset in snapshot.outputs],
        "error": _error_to_json(snapshot.error),
        "created_at": snapshot.created_at.isoformat(),
        "updated_at": snapshot.updated_at.isoformat(),
        "version": snapshot.version,
    }


def _managed_snapshot_from_json(raw: object) -> ManagedJobSnapshot:
    payload = _mapping(raw, "任务记录")
    _exact_keys(
        payload,
        {
            "job_id",
            "request_kind",
            "request_id",
            "provider_id",
            "idempotency_key",
            "request_fingerprint",
            "status",
            "provider_job_id",
            "outputs",
            "error",
            "created_at",
            "updated_at",
            "version",
        },
        "任务记录",
    )
    outputs = payload["outputs"]
    if not isinstance(outputs, list):
        raise ValueError("任务输出列表无效")
    provider_job_id = payload["provider_job_id"]
    return ManagedJobSnapshot(
        job_id=str(payload["job_id"]),
        request_kind=JobRequestKind(str(payload["request_kind"])),
        request_id=str(payload["request_id"]),
        provider_id=str(payload["provider_id"]),
        idempotency_key=str(payload["idempotency_key"]),
        request_fingerprint=str(payload["request_fingerprint"]),
        status=JobStatus(str(payload["status"])),
        provider_job_id=None if provider_job_id is None else str(provider_job_id),
        outputs=tuple(_asset_from_json(value) for value in outputs),
        error=_error_from_json(payload["error"]),
        created_at=_datetime(payload["created_at"], "任务创建时间"),
        updated_at=_datetime(payload["updated_at"], "任务更新时间"),
        version=_integer(payload["version"], "任务版本"),
    )


def _error_to_json(error: JobError | None) -> dict[str, object] | None:
    if error is None:
        return None
    return {"code": error.code, "message": error.message, "retryable": error.retryable}


def _error_from_json(raw: object) -> JobError | None:
    if raw is None:
        return None
    payload = _mapping(raw, "任务错误")
    _exact_keys(payload, {"code", "message", "retryable"}, "任务错误")
    retryable = payload["retryable"]
    if not isinstance(retryable, bool):
        raise ValueError("任务错误的 retryable 必须是布尔值")
    return JobError(
        code=str(payload["code"]),
        message=str(payload["message"]),
        retryable=retryable,
    )


def _asset_to_json(asset: AssetRecord) -> dict[str, object]:
    return {
        "asset_id": asset.asset_id,
        "name": asset.name,
        "created_at": asset.created_at.isoformat(),
        "revisions": [_revision_to_json(revision) for revision in asset.revisions],
    }


def _asset_from_json(raw: object) -> AssetRecord:
    payload = _mapping(raw, "输出资源")
    _exact_keys(payload, {"asset_id", "name", "created_at", "revisions"}, "输出资源")
    revisions = payload["revisions"]
    if not isinstance(revisions, list):
        raise ValueError("输出资源版本列表无效")
    return AssetRecord(
        asset_id=str(payload["asset_id"]),
        name=str(payload["name"]),
        revisions=tuple(_revision_from_json(value) for value in revisions),
        created_at=_datetime(payload["created_at"], "资源创建时间"),
    )


def _revision_to_json(revision: AssetRevision) -> dict[str, object]:
    return {
        "asset_id": revision.asset_id,
        "sequence": revision.sequence,
        "content_uri": revision.content_uri,
        "media_type": revision.media_type,
        "sha256": revision.sha256,
        "provenance": _provenance_to_json(revision.provenance),
        "revision_id": revision.revision_id,
        "byte_size": revision.byte_size,
        "width": revision.width,
        "height": revision.height,
        "created_at": revision.created_at.isoformat(),
        "parent_revision_id": revision.parent_revision_id,
        "storage_kind": revision.storage_kind.value,
    }


def _revision_from_json(raw: object) -> AssetRevision:
    payload = _mapping(raw, "资源版本")
    _exact_keys(
        payload,
        {
            "asset_id",
            "sequence",
            "content_uri",
            "media_type",
            "sha256",
            "provenance",
            "revision_id",
            "byte_size",
            "width",
            "height",
            "created_at",
            "parent_revision_id",
            "storage_kind",
        },
        "资源版本",
    )
    return AssetRevision(
        asset_id=str(payload["asset_id"]),
        sequence=_integer(payload["sequence"], "资源版本序号"),
        content_uri=str(payload["content_uri"]),
        media_type=str(payload["media_type"]),
        sha256=str(payload["sha256"]),
        provenance=_provenance_from_json(payload["provenance"]),
        revision_id=str(payload["revision_id"]),
        byte_size=_optional_integer(payload["byte_size"], "资源字节数"),
        width=_optional_integer(payload["width"], "资源宽度"),
        height=_optional_integer(payload["height"], "资源高度"),
        created_at=_datetime(payload["created_at"], "资源版本创建时间"),
        parent_revision_id=(
            None if payload["parent_revision_id"] is None else str(payload["parent_revision_id"])
        ),
        storage_kind=AssetStorageKind(str(payload["storage_kind"])),
    )


def _provenance_to_json(provenance: Provenance) -> dict[str, object]:
    return {
        "origin": provenance.origin,
        "actor": provenance.actor,
        "created_at": provenance.created_at.isoformat(),
        "source_revision_ids": list(provenance.source_revision_ids),
        "request_id": provenance.request_id,
        "provider_id": provenance.provider_id,
        "model": provenance.model,
        "parameters": dict(provenance.parameters),
    }


def _provenance_from_json(raw: object) -> Provenance:
    payload = _mapping(raw, "来源信息")
    _exact_keys(
        payload,
        {
            "origin",
            "actor",
            "created_at",
            "source_revision_ids",
            "request_id",
            "provider_id",
            "model",
            "parameters",
        },
        "来源信息",
    )
    source_ids = payload["source_revision_ids"]
    parameters = payload["parameters"]
    if not isinstance(source_ids, list) or not all(isinstance(value, str) for value in source_ids):
        raise ValueError("来源版本编号列表无效")
    if not isinstance(parameters, dict):
        raise ValueError("来源参数无效")
    return Provenance(
        origin=str(payload["origin"]),
        actor=str(payload["actor"]),
        created_at=_datetime(payload["created_at"], "来源创建时间"),
        source_revision_ids=tuple(source_ids),
        request_id=None if payload["request_id"] is None else str(payload["request_id"]),
        provider_id=None if payload["provider_id"] is None else str(payload["provider_id"]),
        model=None if payload["model"] is None else str(payload["model"]),
        parameters=parameters,
    )


def _mapping(raw: object, label: str) -> Mapping[str, Any]:
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{label}必须是对象")
    return raw


def _exact_keys(payload: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(payload) != expected:
        raise ValueError(f"{label}字段不完整或包含未知字段")


def _datetime(raw: object, label: str) -> datetime:
    if not isinstance(raw, str):
        raise ValueError(f"{label}无效")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError(f"{label}必须包含时区")
    return parsed


def _integer(raw: object, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{label}必须是整数")
    return raw


def _optional_integer(raw: object, label: str) -> int | None:
    return None if raw is None else _integer(raw, label)
