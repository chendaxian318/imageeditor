"""Versioned persistence and safe recovery for conversion work sessions."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

from image_ops import open_image

from .conversion_jobs import ConversionInput, ConversionQueue
from .conversion_runner import (
    MAX_CONVERSION_WORKERS,
    ConversionRunner,
    ConversionTaskRecord,
    ConversionTaskStatus,
)
from .recipes import TransformRecipe

CONVERSION_SESSION_SCHEMA_VERSION = 1
DEFAULT_MAX_CONVERSION_SESSION_BYTES = 16 * 1024 * 1024

_SESSION_FIELDS = {
    "schema_version",
    "saved_at",
    "output_directory",
    "max_workers",
    "recipe",
    "queue",
    "tasks",
}
_INPUT_FIELDS = {"path", "enabled", "asset", "revision", "metadata"}
_ASSET_FIELDS = {"id", "uri", "media_type", "display_name", "metadata"}
_REVISION_FIELDS = {
    "id",
    "asset_id",
    "number",
    "dirty",
    "project_uri",
    "metadata",
}
_TASK_FIELDS = {
    "index",
    "input_path",
    "planned_output",
    "status",
    "error",
    "attempts",
    "started_at",
    "finished_at",
    "input_bytes",
    "output_bytes",
    "output",
}


class ConversionSessionError(ValueError):
    """A conversion session is malformed, unsupported, or unsafe to restore."""


class ConversionSessionTooLargeError(ConversionSessionError):
    """A conversion session exceeds the configured byte limit."""


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize_path(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate.absolute()


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path)).casefold()


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConversionSessionError(f"{label} 不能为空")
    return value.strip()


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConversionSessionError(f"{label} 必须是字符串或 null")
    return value


def _strict_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConversionSessionError(f"{label} 必须是布尔值")
    return value


def _strict_int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConversionSessionError(f"{label} 必须是大于等于 {minimum} 的整数")
    return value


def _reject_unknown(payload: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise ConversionSessionError(f"{label} 包含未知字段：{'、'.join(sorted(unknown))}")


def _require_fields(payload: dict[str, Any], required: set[str], label: str) -> None:
    missing = required - set(payload)
    if missing:
        raise ConversionSessionError(f"{label} 缺少字段：{'、'.join(sorted(missing))}")


def _freeze_metadata(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(values))


def _json_safe(value: object, label: str = "metadata") -> object:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConversionSessionError(f"{label} 不能包含 NaN 或 Infinity")
        return value
    if isinstance(value, Mapping):
        converted: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConversionSessionError(f"{label} 的对象键必须是字符串")
            converted[key] = _json_safe(item, f"{label}.{key}")
        return converted
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, label) for item in value]
    raise ConversionSessionError(f"{label} 包含不可序列化类型：{type(value).__name__}")


def _metadata_from_json(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ConversionSessionError(f"{label} 必须是对象")
    return _freeze_metadata(value)


def _parse_datetime(value: object, label: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ConversionSessionError(f"{label} 必须是 ISO 8601 时间字符串")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ConversionSessionError(f"{label} 不是有效的 ISO 8601 时间") from error
    if parsed.tzinfo is None:
        raise ConversionSessionError(f"{label} 必须包含时区")
    return parsed


def _reject_json_constant(value: str) -> NoReturn:
    raise ConversionSessionError(f"会话 JSON 包含非标准数值：{value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConversionSessionError(f"会话 JSON 包含重复字段：{key}")
        result[key] = value
    return result


def _strict_json_object(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ConversionSessionError("转换会话必须是严格 UTF-8 编码") from error
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except ConversionSessionError:
        raise
    except json.JSONDecodeError as error:
        raise ConversionSessionError(f"转换会话 JSON 损坏：{error.msg}") from error
    if not isinstance(payload, dict):
        raise ConversionSessionError("转换会话 JSON 顶层必须是对象")
    return payload


@dataclass(frozen=True, slots=True)
class SessionAssetIdentity:
    """Serializable subset of either an AssetRecord or an AssetHandoff."""

    id: str
    uri: str | None = None
    media_type: str = "image"
    display_name: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "asset id"))
        object.__setattr__(self, "uri", _optional_text(self.uri, "asset uri"))
        object.__setattr__(self, "media_type", _required_text(self.media_type, "asset media_type"))
        object.__setattr__(
            self,
            "display_name",
            _optional_text(self.display_name, "asset display_name"),
        )
        if not isinstance(self.metadata, Mapping):
            raise ConversionSessionError("asset metadata 必须是映射")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class SessionRevisionIdentity:
    """Serializable subset of either an AssetRevision or a RevisionHandoff."""

    id: str
    asset_id: str
    number: int | None = None
    dirty: bool = False
    project_uri: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "revision id"))
        object.__setattr__(self, "asset_id", _required_text(self.asset_id, "revision asset_id"))
        if self.number is not None:
            object.__setattr__(
                self,
                "number",
                _strict_int(self.number, "revision number", minimum=0),
            )
        object.__setattr__(self, "dirty", _strict_bool(self.dirty, "revision dirty"))
        object.__setattr__(
            self,
            "project_uri",
            _optional_text(self.project_uri, "revision project_uri"),
        )
        if not isinstance(self.metadata, Mapping):
            raise ConversionSessionError("revision metadata 必须是映射")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


def _asset_identity(reference: object | None) -> SessionAssetIdentity | None:
    if reference is None:
        return None
    if isinstance(reference, SessionAssetIdentity):
        return reference
    current_revision = getattr(reference, "current_revision", None)
    uri = getattr(reference, "uri", None)
    if uri is None and current_revision is not None:
        uri = getattr(current_revision, "content_uri", None)
    media_type = getattr(reference, "media_type", None)
    if media_type is None and current_revision is not None:
        media_type = getattr(current_revision, "media_type", None)
    display_name = getattr(reference, "display_name", None)
    if display_name is None:
        display_name = getattr(reference, "name", None)
    metadata = getattr(reference, "metadata", {})
    if not isinstance(metadata, Mapping):
        raise ConversionSessionError("asset metadata 必须是映射")
    return SessionAssetIdentity(
        id=_required_text(getattr(reference, "id", None), "asset id"),
        uri=_optional_text(uri, "asset uri"),
        media_type=(
            "image" if media_type is None else _required_text(media_type, "asset media_type")
        ),
        display_name=_optional_text(display_name, "asset display_name"),
        metadata=metadata,
    )


def _revision_identity(reference: object | None) -> SessionRevisionIdentity | None:
    if reference is None:
        return None
    if isinstance(reference, SessionRevisionIdentity):
        return reference
    number = getattr(reference, "number", None)
    if number is None:
        number = getattr(reference, "sequence", None)
    project_uri = getattr(reference, "project_uri", None)
    if project_uri is None:
        project_uri = getattr(reference, "content_uri", None)
    metadata = getattr(reference, "metadata", {})
    if not isinstance(metadata, Mapping):
        raise ConversionSessionError("revision metadata 必须是映射")
    return SessionRevisionIdentity(
        id=_required_text(getattr(reference, "id", None), "revision id"),
        asset_id=_required_text(getattr(reference, "asset_id", None), "revision asset_id"),
        number=None if number is None else _strict_int(number, "revision number"),
        dirty=_strict_bool(getattr(reference, "dirty", False), "revision dirty"),
        project_uri=_optional_text(project_uri, "revision project_uri"),
        metadata=metadata,
    )


@dataclass(frozen=True, slots=True)
class ConversionSession:
    """Immutable snapshot required to resume or selectively retry conversion work."""

    queue_items: tuple[ConversionInput, ...]
    recipe: TransformRecipe
    output_directory: Path
    tasks: tuple[ConversionTaskRecord, ...]
    max_workers: int = 2
    saved_at: datetime = field(default_factory=_now)
    schema_version: int = CONVERSION_SESSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != CONVERSION_SESSION_SCHEMA_VERSION
        ):
            raise ConversionSessionError(f"不支持的转换会话版本：{self.schema_version}")
        if (
            isinstance(self.max_workers, bool)
            or not isinstance(self.max_workers, int)
            or not 1 <= self.max_workers <= MAX_CONVERSION_WORKERS
        ):
            raise ConversionSessionError(f"max_workers 必须位于 1 到 {MAX_CONVERSION_WORKERS} 之间")
        if self.saved_at.tzinfo is None:
            raise ConversionSessionError("saved_at 必须包含时区")
        items = tuple(self.queue_items)
        restored_queue = ConversionQueue.from_ordered(items)
        if len(restored_queue) != len(items):
            raise ConversionSessionError("转换会话队列包含重复路径")
        canonical_inputs = {_path_key(item.path): item for item in items}
        output_directory = _normalize_path(self.output_directory)
        recipe = TransformRecipe.from_json(self.recipe.to_json())
        tasks: list[ConversionTaskRecord] = []
        for task in self.tasks:
            if not isinstance(task, ConversionTaskRecord):
                raise ConversionSessionError("会话任务必须是 ConversionTaskRecord")
            if not isinstance(task.status, ConversionTaskStatus):
                raise ConversionSessionError("会话任务 status 必须是 ConversionTaskStatus")
            _strict_int(task.index, "任务 index", minimum=1)
            _strict_int(task.attempts, "任务 attempts")
            _strict_int(task.input_bytes, "任务 input_bytes")
            _strict_int(task.output_bytes, "任务 output_bytes")
            canonical_input = canonical_inputs.get(_path_key(task.input.path))
            if canonical_input is None:
                raise ConversionSessionError("会话任务引用了队列之外的输入")
            planned_output = _normalize_path(task.planned_output)
            if not planned_output.is_relative_to(output_directory):
                raise ConversionSessionError("任务 planned_output 位于输出目录之外")
            output = None if task.output is None else _normalize_path(task.output)
            if output is not None and output != planned_output:
                raise ConversionSessionError("任务 output 必须与 planned_output 相同")
            for value, label in (
                (task.started_at, "started_at"),
                (task.finished_at, "finished_at"),
            ):
                if value is not None and value.tzinfo is None:
                    raise ConversionSessionError(f"任务 {label} 必须包含时区")
            tasks.append(
                replace(
                    task,
                    input=canonical_input,
                    planned_output=planned_output,
                    output=output,
                )
            )
        expected_indices = list(range(1, len(tasks) + 1))
        if [task.index for task in tasks] != expected_indices:
            raise ConversionSessionError("会话任务 index 必须连续并按顺序排列")

        object.__setattr__(self, "queue_items", items)
        object.__setattr__(self, "recipe", recipe)
        object.__setattr__(self, "output_directory", output_directory)
        object.__setattr__(self, "tasks", tuple(tasks))

    @classmethod
    def capture(
        cls,
        queue: ConversionQueue,
        runner: ConversionRunner,
    ) -> ConversionSession:
        if not isinstance(queue, ConversionQueue):
            raise TypeError("queue 必须是 ConversionQueue")
        if not isinstance(runner, ConversionRunner):
            raise TypeError("runner 必须是 ConversionRunner")
        return cls(
            queue_items=queue.items,
            recipe=runner.recipe,
            output_directory=runner.output_directory,
            tasks=runner.tasks,
            max_workers=runner.max_workers,
        )

    def create_queue(self) -> ConversionQueue:
        return ConversionQueue.from_ordered(self.queue_items)

    def merge_tasks(
        self,
        updates: tuple[ConversionTaskRecord, ...] | list[ConversionTaskRecord],
    ) -> ConversionSession:
        """Merge a selective retry result without losing untouched successes."""

        by_index = {task.index: task for task in self.tasks}
        seen: set[int] = set()
        for task in updates:
            if not isinstance(task, ConversionTaskRecord):
                raise TypeError("任务更新必须是 ConversionTaskRecord")
            if task.index in seen:
                raise ConversionSessionError(f"任务更新 index 重复：{task.index}")
            seen.add(task.index)
            original = by_index.get(task.index)
            if original is None:
                raise ConversionSessionError(f"任务更新 index 不存在：{task.index}")
            if _path_key(task.input.path) != _path_key(original.input.path):
                raise ConversionSessionError("任务更新不能替换输入文件")
            if _normalize_path(task.planned_output) != original.planned_output:
                raise ConversionSessionError("任务更新不能改变 planned_output")
            by_index[task.index] = task
        merged = tuple(by_index[task.index] for task in self.tasks)
        return replace(self, tasks=merged, saved_at=_now())

    def retry_runner(
        self,
        *,
        max_workers: int | None = None,
    ) -> ConversionRunner:
        return ConversionRunner.retry_tasks(
            self.tasks,
            self.recipe,
            self.output_directory,
            max_workers=self.max_workers if max_workers is None else max_workers,
        )

    def resume_runner(
        self,
        *,
        max_workers: int | None = None,
    ) -> ConversionRunner:
        return ConversionRunner.retry_tasks(
            self.tasks,
            self.recipe,
            self.output_directory,
            max_workers=self.max_workers if max_workers is None else max_workers,
            statuses=(
                ConversionTaskStatus.PENDING,
                ConversionTaskStatus.FAILED,
                ConversionTaskStatus.CANCELLED,
            ),
        )


def _asset_to_json(reference: object | None) -> dict[str, object] | None:
    identity = _asset_identity(reference)
    if identity is None:
        return None
    return {
        "id": identity.id,
        "uri": identity.uri,
        "media_type": identity.media_type,
        "display_name": identity.display_name,
        "metadata": _json_safe(identity.metadata, "asset metadata"),
    }


def _revision_to_json(reference: object | None) -> dict[str, object] | None:
    identity = _revision_identity(reference)
    if identity is None:
        return None
    return {
        "id": identity.id,
        "asset_id": identity.asset_id,
        "number": identity.number,
        "dirty": identity.dirty,
        "project_uri": identity.project_uri,
        "metadata": _json_safe(identity.metadata, "revision metadata"),
    }


def _input_to_json(item: ConversionInput) -> dict[str, object]:
    return {
        "path": str(item.path),
        "enabled": item.enabled,
        "asset": _asset_to_json(item.asset),
        "revision": _revision_to_json(item.revision),
        "metadata": _json_safe(item.metadata, "input metadata"),
    }


def _task_to_json(task: ConversionTaskRecord) -> dict[str, object]:
    return {
        "index": task.index,
        "input_path": str(task.input.path),
        "planned_output": str(task.planned_output),
        "status": task.status.value,
        "error": task.error,
        "attempts": task.attempts,
        "started_at": None if task.started_at is None else task.started_at.isoformat(),
        "finished_at": None if task.finished_at is None else task.finished_at.isoformat(),
        "input_bytes": task.input_bytes,
        "output_bytes": task.output_bytes,
        "output": None if task.output is None else str(task.output),
    }


def _session_to_json(session: ConversionSession) -> dict[str, object]:
    return {
        "schema_version": session.schema_version,
        "saved_at": session.saved_at.isoformat(),
        "output_directory": str(session.output_directory),
        "max_workers": session.max_workers,
        "recipe": session.recipe.to_json(),
        "queue": [_input_to_json(item) for item in session.queue_items],
        "tasks": [_task_to_json(task) for task in session.tasks],
    }


def _asset_from_json(value: object) -> SessionAssetIdentity | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConversionSessionError("queue asset 必须是对象或 null")
    _reject_unknown(value, _ASSET_FIELDS, "queue asset")
    _require_fields(value, _ASSET_FIELDS, "queue asset")
    return SessionAssetIdentity(
        id=_required_text(value["id"], "asset id"),
        uri=_optional_text(value["uri"], "asset uri"),
        media_type=_required_text(value["media_type"], "asset media_type"),
        display_name=_optional_text(value["display_name"], "asset display_name"),
        metadata=_metadata_from_json(value["metadata"], "asset metadata"),
    )


def _revision_from_json(value: object) -> SessionRevisionIdentity | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConversionSessionError("queue revision 必须是对象或 null")
    _reject_unknown(value, _REVISION_FIELDS, "queue revision")
    _require_fields(value, _REVISION_FIELDS, "queue revision")
    number = value["number"]
    return SessionRevisionIdentity(
        id=_required_text(value["id"], "revision id"),
        asset_id=_required_text(value["asset_id"], "revision asset_id"),
        number=None if number is None else _strict_int(number, "revision number"),
        dirty=_strict_bool(value["dirty"], "revision dirty"),
        project_uri=_optional_text(value["project_uri"], "revision project_uri"),
        metadata=_metadata_from_json(value["metadata"], "revision metadata"),
    )


def _input_from_json(value: object) -> ConversionInput:
    if not isinstance(value, dict):
        raise ConversionSessionError("queue 项必须是对象")
    _reject_unknown(value, _INPUT_FIELDS, "queue 项")
    _require_fields(value, _INPUT_FIELDS, "queue 项")
    path = _normalize_path(_required_text(value["path"], "queue path"))
    return ConversionInput(
        path=path,
        enabled=_strict_bool(value["enabled"], "queue enabled"),
        asset=_asset_from_json(value["asset"]),
        revision=_revision_from_json(value["revision"]),
        metadata=_metadata_from_json(value["metadata"], "input metadata"),
    )


def _task_from_json(
    value: object,
    inputs_by_path: Mapping[str, ConversionInput],
) -> ConversionTaskRecord:
    if not isinstance(value, dict):
        raise ConversionSessionError("task 必须是对象")
    _reject_unknown(value, _TASK_FIELDS, "task")
    _require_fields(value, _TASK_FIELDS, "task")
    input_path = _normalize_path(_required_text(value["input_path"], "task input_path"))
    item = inputs_by_path.get(_path_key(input_path))
    if item is None:
        raise ConversionSessionError("task input_path 不在 queue 中")
    status_value = _required_text(value["status"], "task status")
    try:
        status = ConversionTaskStatus(status_value)
    except ValueError as error:
        raise ConversionSessionError(f"未知 task status：{status_value}") from error
    error_text = _optional_text(value["error"], "task error")
    output_value = value["output"]
    return ConversionTaskRecord(
        index=_strict_int(value["index"], "task index", minimum=1),
        input=item,
        planned_output=_normalize_path(
            _required_text(value["planned_output"], "task planned_output")
        ),
        status=status,
        error=error_text,
        attempts=_strict_int(value["attempts"], "task attempts"),
        started_at=_parse_datetime(value["started_at"], "task started_at", optional=True),
        finished_at=_parse_datetime(value["finished_at"], "task finished_at", optional=True),
        input_bytes=_strict_int(value["input_bytes"], "task input_bytes"),
        output_bytes=_strict_int(value["output_bytes"], "task output_bytes"),
        output=(
            None
            if output_value is None
            else _normalize_path(_required_text(output_value, "task output"))
        ),
    )


def _normalize_recovered_task(task: ConversionTaskRecord) -> ConversionTaskRecord:
    if task.status is ConversionTaskStatus.RUNNING:
        return replace(
            task,
            status=ConversionTaskStatus.PENDING,
            error=None,
            started_at=None,
            finished_at=None,
            output=None,
            output_bytes=0,
        )
    if task.status is ConversionTaskStatus.PENDING:
        return replace(
            task,
            error=None,
            started_at=None,
            finished_at=None,
            output=None,
            output_bytes=0,
        )
    if task.status is not ConversionTaskStatus.SUCCEEDED:
        return task
    output = task.output
    try:
        if output is None:
            raise FileNotFoundError("成功任务缺少 output 路径")
        actual_bytes = output.stat().st_size
        if task.output_bytes > 0 and actual_bytes != task.output_bytes:
            raise OSError(f"输出字节数已变化：记录 {task.output_bytes}，实际 {actual_bytes}")
        image = open_image(output)
        image.close()
    except Exception as error:
        return replace(
            task,
            status=ConversionTaskStatus.FAILED,
            error=f"恢复验证失败：{type(error).__name__}: {error}",
            finished_at=_now(),
        )
    return task


@dataclass(frozen=True, slots=True)
class ConversionSessionStore:
    """Strict UTF-8, bounded and atomic conversion-session storage."""

    max_file_bytes: int = DEFAULT_MAX_CONVERSION_SESSION_BYTES

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_file_bytes, bool)
            or not isinstance(self.max_file_bytes, int)
            or self.max_file_bytes < 1
        ):
            raise ValueError("转换会话大小上限必须是正整数")

    def save(
        self,
        session: ConversionSession,
        path: str | os.PathLike[str],
    ) -> Path:
        if not isinstance(session, ConversionSession):
            raise TypeError("session 必须是 ConversionSession")
        try:
            raw = (
                json.dumps(
                    _session_to_json(session),
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            if isinstance(error, ConversionSessionError):
                raise
            raise ConversionSessionError(f"转换会话无法序列化：{error}") from error
        if len(raw) > self.max_file_bytes:
            raise ConversionSessionTooLargeError(f"转换会话超过 {self.max_file_bytes} 字节安全上限")

        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
        return destination

    def load(self, path: str | os.PathLike[str]) -> ConversionSession:
        source = Path(path)
        with source.open("rb") as stream:
            raw = stream.read(self.max_file_bytes + 1)
        if len(raw) > self.max_file_bytes:
            raise ConversionSessionTooLargeError(f"转换会话超过 {self.max_file_bytes} 字节安全上限")
        payload = _strict_json_object(raw)
        _reject_unknown(payload, _SESSION_FIELDS, "转换会话")
        _require_fields(payload, _SESSION_FIELDS, "转换会话")
        version = _strict_int(payload["schema_version"], "schema_version", minimum=1)
        if version != CONVERSION_SESSION_SCHEMA_VERSION:
            raise ConversionSessionError(f"不支持的转换会话版本：{version}")
        raw_queue = payload["queue"]
        raw_tasks = payload["tasks"]
        if not isinstance(raw_queue, list):
            raise ConversionSessionError("queue 必须是数组")
        if not isinstance(raw_tasks, list):
            raise ConversionSessionError("tasks 必须是数组")
        queue_items = tuple(_input_from_json(item) for item in raw_queue)
        if len(ConversionQueue.from_ordered(queue_items)) != len(queue_items):
            raise ConversionSessionError("queue 包含重复路径")
        inputs_by_path = {_path_key(item.path): item for item in queue_items}
        tasks = tuple(_task_from_json(item, inputs_by_path) for item in raw_tasks)
        try:
            recipe = TransformRecipe.from_json(payload["recipe"])
        except (KeyError, TypeError, ValueError) as error:
            raise ConversionSessionError(f"会话配方无效：{error}") from error
        saved_at = _parse_datetime(payload["saved_at"], "saved_at")
        assert saved_at is not None
        session = ConversionSession(
            queue_items=queue_items,
            recipe=recipe,
            output_directory=_normalize_path(
                _required_text(payload["output_directory"], "output_directory")
            ),
            tasks=tasks,
            max_workers=_strict_int(payload["max_workers"], "max_workers", minimum=1),
            saved_at=saved_at,
            schema_version=version,
        )
        return replace(
            session,
            tasks=tuple(_normalize_recovered_task(task) for task in session.tasks),
        )
