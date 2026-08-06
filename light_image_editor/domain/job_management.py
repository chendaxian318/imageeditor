"""Immutable local snapshots for asynchronous provider jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from .ai_requests import JobError, JobStatus
from .assets import AssetRecord


def _utc_now() -> datetime:
    return datetime.now(UTC)


class JobRequestKind(StrEnum):
    GENERATION = "generation"
    EDIT = "edit"


@dataclass(frozen=True, slots=True)
class ManagedJobSnapshot:
    """Durable local view of one provider submission.

    ``job_id`` is owned by the local manager. ``provider_job_id`` is absent until
    the provider acknowledges the request, which is the interval where a crash can
    make the remote submission state unknowable.
    """

    job_id: str
    request_kind: JobRequestKind
    request_id: str
    provider_id: str
    idempotency_key: str
    request_fingerprint: str
    status: JobStatus
    provider_job_id: str | None = None
    outputs: tuple[AssetRecord, ...] = ()
    error: JobError | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    version: int = 1

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("本地任务编号不能为空")
        if not self.request_id.strip():
            raise ValueError("请求编号不能为空")
        if not self.provider_id.strip():
            raise ValueError("服务提供方编号不能为空")
        if not self.idempotency_key.strip():
            raise ValueError("幂等键不能为空")
        if len(self.request_fingerprint) != 64:
            raise ValueError("请求指纹格式无效")
        try:
            int(self.request_fingerprint, 16)
        except ValueError as error:
            raise ValueError("请求指纹格式无效") from error
        if self.provider_job_id is not None and not self.provider_job_id.strip():
            raise ValueError("提供方任务编号不能为空")
        outputs = tuple(self.outputs)
        object.__setattr__(self, "outputs", outputs)
        if self.status is JobStatus.SUCCEEDED and not outputs:
            raise ValueError("成功任务必须包含输出资源")
        if self.status is not JobStatus.SUCCEEDED and outputs:
            raise ValueError("只有成功任务可以包含输出资源")
        if self.status in {JobStatus.FAILED, JobStatus.UNKNOWN} and self.error is None:
            raise ValueError("失败或状态未知的任务必须包含错误信息")
        if self.status not in {JobStatus.FAILED, JobStatus.UNKNOWN} and self.error is not None:
            raise ValueError("当前任务状态不能包含错误信息")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("任务时间必须包含时区")
        if self.updated_at < self.created_at:
            raise ValueError("任务更新时间不能早于创建时间")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("任务版本必须是正整数")
