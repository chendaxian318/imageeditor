"""Persistent, bounded and failure-isolating batch processing."""

from __future__ import annotations

import csv
import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from PIL import Image

from image_ops import (
    ConflictPolicy,
    ExportOptions,
    extension_for_format,
    open_image,
    prepare_image,
    save_image,
)

from .document import metadata_from_image
from .formats import ANIMATION_FORMATS, decode_animation, export_animation

BATCH_SCHEMA_VERSION = 1
PRESET_SCHEMA_VERSION = 1
DEFAULT_MEMORY_LIMIT_MB = 512
DEFAULT_CONCURRENCY = 2
_TEMPLATE_FIELD = re.compile(r"\{(stem|name|ext|index|date|datetime)(?::([^}]+))?\}")
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class BatchRecipe:
    output_format: str = "JPEG"
    quality: int = 85
    max_width: int | None = None
    max_height: int | None = None
    watermark_text: str = ""
    watermark_position: str = "右下"
    watermark_opacity: int = 35
    watermark_size_percent: int = 5
    strip_metadata: bool = False
    rename_template: str = "{stem}_{index:04d}"
    conflict_policy: str = ConflictPolicy.UNIQUE.value
    preserve_animation: bool = True
    allow_in_place: bool = False

    def validate(self) -> None:
        if not 1 <= self.quality <= 100:
            raise ValueError("质量必须位于 1 到 100")
        if self.max_width is not None and self.max_width <= 0:
            raise ValueError("最大宽度必须大于 0")
        if self.max_height is not None and self.max_height <= 0:
            raise ValueError("最大高度必须大于 0")
        if self.conflict_policy not in {item.value for item in ConflictPolicy}:
            raise ValueError("未知的同名冲突策略")
        render_name(self.rename_template, Path("sample.jpg"), 1)

    def export_options(self) -> ExportOptions:
        return ExportOptions(
            output_format=self.output_format,
            quality=self.quality,
            max_width=self.max_width,
            max_height=self.max_height,
            watermark_text=self.watermark_text,
            watermark_position=self.watermark_position,
            watermark_opacity=self.watermark_opacity,
            watermark_size_percent=self.watermark_size_percent,
            preserve_metadata=not self.strip_metadata,
        )


@dataclass(frozen=True)
class BatchPreset:
    name: str
    recipe: BatchRecipe
    schema_version: int = PRESET_SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "recipe": asdict(self.recipe),
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> BatchPreset:
        version = int(payload.get("schema_version", 0))
        if version != PRESET_SCHEMA_VERSION:
            raise ValueError(f"不支持的批处理预设版本：{version}")
        preset = cls(
            name=str(payload.get("name", "未命名预设")), recipe=BatchRecipe(**payload["recipe"])
        )
        preset.recipe.validate()
        return preset

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        _atomic_json(target, self.to_json())
        return target

    @classmethod
    def load(cls, path: str | Path) -> BatchPreset:
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class BatchTask:
    source: str
    status: str = TaskStatus.PENDING.value
    output: str | None = None
    attempts: int = 0
    error: str | None = None
    input_bytes: int = 0
    output_bytes: int = 0
    reserved_bytes: int = 0


@dataclass
class BatchManifest:
    output_dir: str
    recipe: BatchRecipe
    tasks: list[BatchTask]
    concurrency: int = DEFAULT_CONCURRENCY
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB
    source_root: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: int = BATCH_SCHEMA_VERSION
    same_directory_detected: bool = False
    peak_reserved_bytes: int = 0

    @classmethod
    def create(
        cls,
        sources: Iterable[str | Path],
        output_dir: str | Path,
        recipe: BatchRecipe,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
        source_root: str | Path | None = None,
    ) -> BatchManifest:
        recipe.validate()
        output = Path(output_dir).resolve()
        paths = [Path(item).resolve() for item in sources]
        return cls(
            output_dir=str(output),
            recipe=recipe,
            tasks=[BatchTask(source=str(path)) for path in paths],
            concurrency=max(1, min(16, int(concurrency))),
            memory_limit_mb=max(32, int(memory_limit_mb)),
            source_root=str(Path(source_root).resolve()) if source_root else None,
            same_directory_detected=any(path.parent == output for path in paths),
        )

    @property
    def complete(self) -> bool:
        return all(
            task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            for task in self.tasks
        )

    def counts(self) -> dict[str, int]:
        return {
            status.value: sum(task.status == status.value for task in self.tasks)
            for status in TaskStatus
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "created_utc": self.created_utc,
            "updated_utc": self.updated_utc,
            "output_dir": self.output_dir,
            "source_root": self.source_root,
            "concurrency": self.concurrency,
            "memory_limit_mb": self.memory_limit_mb,
            "same_directory_detected": self.same_directory_detected,
            "peak_reserved_bytes": self.peak_reserved_bytes,
            "recipe": asdict(self.recipe),
            "tasks": [asdict(task) for task in self.tasks],
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> BatchManifest:
        version = int(payload.get("schema_version", 0))
        if version != BATCH_SCHEMA_VERSION:
            raise ValueError(f"不支持的批处理任务版本：{version}")
        tasks = [BatchTask(**item) for item in payload.get("tasks", [])]
        for task in tasks:
            if task.status == TaskStatus.RUNNING.value:
                task.status = TaskStatus.PENDING.value
                task.error = "上次运行中断，已恢复为待处理"
        return cls(
            output_dir=payload["output_dir"],
            recipe=BatchRecipe(**payload["recipe"]),
            tasks=tasks,
            concurrency=int(payload.get("concurrency", DEFAULT_CONCURRENCY)),
            memory_limit_mb=int(payload.get("memory_limit_mb", DEFAULT_MEMORY_LIMIT_MB)),
            source_root=payload.get("source_root"),
            id=payload.get("id", uuid.uuid4().hex),
            created_utc=payload.get("created_utc", datetime.now(UTC).isoformat()),
            updated_utc=payload.get("updated_utc", datetime.now(UTC).isoformat()),
            same_directory_detected=bool(payload.get("same_directory_detected", False)),
            peak_reserved_bytes=int(payload.get("peak_reserved_bytes", 0)),
        )

    def save(self, path: str | Path) -> Path:
        self.updated_utc = datetime.now(UTC).isoformat()
        target = Path(path)
        _atomic_json(target, self.to_json())
        return target

    @classmethod
    def load(cls, path: str | Path) -> BatchManifest:
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    def retry_failed(self) -> int:
        count = 0
        for task in self.tasks:
            if task.status in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
                task.status = TaskStatus.PENDING.value
                task.error = None
                count += 1
        return count

    def restart_all(self) -> None:
        for task in self.tasks:
            task.status = TaskStatus.PENDING.value
            task.output = None
            task.error = None
            task.input_bytes = 0
            task.output_bytes = 0


class MemoryBudget:
    """A weighted semaphore that caps estimated active image working sets."""

    def __init__(self, capacity_bytes: int) -> None:
        self.capacity = max(1, capacity_bytes)
        self.in_use = 0
        self.peak = 0
        self._condition = threading.Condition()

    def acquire(self, amount: int, cancelled: threading.Event) -> int:
        reservation = min(self.capacity, max(1, amount))
        with self._condition:
            while self.in_use + reservation > self.capacity:
                if cancelled.is_set():
                    raise InterruptedError("批处理已取消")
                self._condition.wait(0.05)
            self.in_use += reservation
            self.peak = max(self.peak, self.in_use)
        return reservation

    def release(self, amount: int) -> None:
        with self._condition:
            self.in_use = max(0, self.in_use - amount)
            self._condition.notify_all()


class BatchRunner:
    def __init__(
        self,
        manifest: BatchManifest,
        manifest_path: str | Path,
        *,
        progress: Callable[[BatchManifest], object] | None = None,
    ) -> None:
        self.manifest = manifest
        self.manifest_path = Path(manifest_path)
        self.progress = progress
        self.cancelled = threading.Event()
        self._save_lock = threading.Lock()
        self._target_lock = threading.Lock()
        self._claimed_targets: set[str] = set()
        self._budget = MemoryBudget(manifest.memory_limit_mb * 1024 * 1024)

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> BatchManifest:
        pending = [task for task in self.manifest.tasks if task.status == TaskStatus.PENDING.value]
        with ThreadPoolExecutor(
            max_workers=self.manifest.concurrency, thread_name_prefix="image-batch"
        ) as executor:
            futures: dict[Future[None], BatchTask] = {}
            for task in pending:
                if self.cancelled.is_set():
                    task.status = TaskStatus.CANCELLED.value
                    continue
                futures[executor.submit(self._process_task, task)] = task
            for future in as_completed(futures):
                try:
                    future.result()
                except InterruptedError:
                    pass
                self._checkpoint()
        if self.cancelled.is_set():
            for task in self.manifest.tasks:
                if task.status == TaskStatus.PENDING.value:
                    task.status = TaskStatus.CANCELLED.value
        self.manifest.peak_reserved_bytes = self._budget.peak
        self._checkpoint()
        return self.manifest

    def _process_task(self, task: BatchTask) -> None:
        if self.cancelled.is_set():
            task.status = TaskStatus.CANCELLED.value
            raise InterruptedError
        task.status = TaskStatus.RUNNING.value
        task.attempts += 1
        self._checkpoint()
        source = Path(task.source)
        reservation = 0
        try:
            estimated = estimate_working_set(source)
            reservation = self._budget.acquire(estimated, self.cancelled)
            task.reserved_bytes = reservation
            target = self._target_for(task, self.manifest.tasks.index(task) + 1)
            if target.resolve() == source.resolve() and not self.manifest.recipe.allow_in_place:
                raise ValueError("输入与输出为同一文件；请更改模板或显式允许原位覆盖")
            options = self.manifest.recipe.export_options()
            with Image.open(source) as header:
                is_animated = (
                    bool(getattr(header, "is_animated", False))
                    and int(getattr(header, "n_frames", 1)) > 1
                )
            if (
                is_animated
                and self.manifest.recipe.preserve_animation
                and options.output_format.upper() in ANIMATION_FORMATS
            ):
                sequence = decode_animation(
                    source, memory_limit_bytes=self.manifest.memory_limit_mb * 1024 * 1024
                )
                saved_target = export_animation(
                    sequence,
                    target,
                    options.output_format,
                    quality=options.quality,
                    transform=lambda frame: prepare_image(frame, options),
                )
            else:
                image = open_image(source)
                try:
                    saved_target = save_image(
                        image,
                        target,
                        options,
                        metadata=metadata_from_image(image),
                        conflict_policy=(
                            ConflictPolicy.OVERWRITE
                            if self.manifest.recipe.conflict_policy
                            == ConflictPolicy.OVERWRITE.value
                            else ConflictPolicy.ERROR
                        ),
                        allow_animation_flatten=not self.manifest.recipe.preserve_animation,
                        allow_transparency_flatten=True,
                    )
                finally:
                    image.close()
            task.output = str(saved_target)
            task.input_bytes = source.stat().st_size
            task.output_bytes = saved_target.stat().st_size
            task.status = TaskStatus.SUCCEEDED.value
            task.error = None
        except InterruptedError:
            task.status = TaskStatus.CANCELLED.value
            task.error = "用户取消"
            raise
        except Exception as error:
            task.status = TaskStatus.FAILED.value
            task.error = f"{type(error).__name__}: {error}"
        finally:
            if reservation:
                self._budget.release(reservation)

    def _target_for(self, task: BatchTask, index: int) -> Path:
        source = Path(task.source)
        output_dir = Path(self.manifest.output_dir)
        if self.manifest.source_root:
            try:
                output_dir /= source.parent.relative_to(Path(self.manifest.source_root))
            except ValueError:
                pass
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = render_name(self.manifest.recipe.rename_template, source, index)
        target = output_dir / f"{stem}{extension_for_format(self.manifest.recipe.output_format)}"
        policy = self.manifest.recipe.conflict_policy
        with self._target_lock:
            key = str(target.resolve()).casefold()
            if policy == ConflictPolicy.UNIQUE.value:
                suffix_index = 1
                while target.exists() or key in self._claimed_targets:
                    target = target.with_name(f"{stem}_{suffix_index}{target.suffix}")
                    key = str(target.resolve()).casefold()
                    suffix_index += 1
            elif policy == ConflictPolicy.ERROR.value and (
                target.exists() or key in self._claimed_targets
            ):
                raise FileExistsError(f"目标文件已存在：{target}")
            self._claimed_targets.add(key)
        return target

    def _checkpoint(self) -> None:
        with self._save_lock:
            self.manifest.peak_reserved_bytes = self._budget.peak
            self.manifest.save(self.manifest_path)
            if self.progress is not None:
                self.progress(self.manifest)


def estimate_working_set(path: str | Path) -> int:
    with Image.open(path) as image:
        frames = min(int(getattr(image, "n_frames", 1)), 4)
        return max(1, image.width * image.height * 8 * frames)


def render_name(template: str, source: Path, index: int, now: datetime | None = None) -> str:
    moment = now or datetime.now()
    if not template.strip():
        raise ValueError("批量重命名模板不能为空")

    def replacement(match: re.Match[str]) -> str:
        field_name, specification = match.groups()
        values: dict[str, object] = {
            "stem": source.stem,
            "name": source.name,
            "ext": source.suffix.lstrip("."),
            "index": index,
            "date": moment.strftime("%Y%m%d"),
            "datetime": moment.strftime("%Y%m%d-%H%M%S"),
        }
        value = values[field_name]
        if specification:
            try:
                return format(value, specification)
            except (ValueError, TypeError) as error:
                raise ValueError(f"模板变量格式无效：{match.group(0)}") from error
        return str(value)

    rendered = _TEMPLATE_FIELD.sub(replacement, template)
    if "{" in rendered or "}" in rendered:
        raise ValueError("模板包含未知变量；支持 name、stem、ext、index、date、datetime")
    rendered = _INVALID_FILENAME.sub("_", rendered).strip(" .")
    if not rendered:
        raise ValueError("重命名模板生成了空文件名")
    return rendered


def export_batch_log(manifest: BatchManifest, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.casefold() == ".json":
        _atomic_json(target, manifest.to_json())
        return target
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["source", "status", "output", "attempts", "error", "input_bytes", "output_bytes"]
        )
        for task in manifest.tasks:
            writer.writerow(
                [
                    task.source,
                    task.status,
                    task.output or "",
                    task.attempts,
                    task.error or "",
                    task.input_bytes,
                    task.output_bytes,
                ]
            )
    return target


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
