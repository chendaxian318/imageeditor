"""Bounded, failure-isolating execution for image-conversion recipes."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from collections import Counter, deque
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from PIL import Image

from image_ops import (
    ConflictPolicy,
    ExportOptions,
    SourceChangedError,
    extension_for_format,
    open_image,
    save_image,
)

from ..batch import render_name
from ..document import SourceFingerprint, SourceMetadata, metadata_from_image
from ..formats import AnimationSequence, decode_animation, export_animation
from .conversion_jobs import ConversionInput, ConversionQueue
from .recipe_executor import execute_transform_recipe
from .recipes import TransformRecipe

MAX_CONVERSION_WORKERS = 16
ANIMATED_OUTPUT_FORMATS = frozenset({"GIF", "PNG", "WEBP"})


class ConversionTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UnsupportedAnimationError(ValueError):
    """A requested animation policy cannot be represented by the target format."""


class TransparencyPolicyError(ValueError):
    """A non-alpha target needs an explicit recipe background colour."""


class ConversionOutputValidationError(OSError):
    """A newly written output cannot be decoded as the promised image."""


@dataclass(frozen=True, slots=True)
class ConversionTaskRecord:
    """Immutable state for one planned input/output pair."""

    index: int
    input: ConversionInput
    planned_output: Path
    status: ConversionTaskStatus = ConversionTaskStatus.PENDING
    error: str | None = None
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    input_bytes: int = 0
    output_bytes: int = 0
    output: Path | None = None


@dataclass(frozen=True, slots=True)
class ConversionRunResult:
    """Final immutable result of one runner invocation."""

    tasks: tuple[ConversionTaskRecord, ...]
    started_at: datetime
    finished_at: datetime
    cancel_requested: bool
    peak_in_flight: int

    @property
    def counts(self) -> dict[ConversionTaskStatus, int]:
        counts = Counter(task.status for task in self.tasks)
        return {status: counts.get(status, 0) for status in ConversionTaskStatus}

    @property
    def successful_outputs(self) -> tuple[Path, ...]:
        return tuple(
            task.output
            for task in self.tasks
            if task.status is ConversionTaskStatus.SUCCEEDED and task.output is not None
        )


class _TaskCancelled(InterruptedError):
    pass


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


def _export_options(recipe: TransformRecipe) -> ExportOptions:
    profile = recipe.export_profile
    return ExportOptions(
        output_format=profile.output_format,
        quality=profile.quality,
        target_size_kb=profile.target_size_kb,
        webp_lossless=profile.webp_lossless,
        png_palette_colors=profile.png_palette_colors,
        tiff_compression=profile.tiff_compression,
        preserve_metadata=profile.preserve_metadata,
    )


def _validate_static_output(
    path: Path,
    *,
    expected_size: tuple[int, int],
    expected_format: str,
) -> None:
    """Re-decode an output and verify its basic image contract."""

    try:
        decoded = open_image(path)
    except Exception as error:
        raise ConversionOutputValidationError(f"输出文件无法重新解码：{error}") from error
    try:
        metadata = metadata_from_image(decoded)
        if metadata.is_animated:
            raise ConversionOutputValidationError("静态转换意外生成了动画输出")
        if decoded.size != expected_size:
            raise ConversionOutputValidationError(
                f"输出尺寸验证失败：期望 {expected_size[0]} × {expected_size[1]}，"
                f"实际 {decoded.width} × {decoded.height}"
            )
        actual_format = (metadata.original_format or "").upper()
        if actual_format != expected_format:
            raise ConversionOutputValidationError(
                f"输出格式验证失败：期望 {expected_format}，实际 {actual_format or '未知'}"
            )
    finally:
        decoded.close()


def _validate_animation_output(
    path: Path,
    *,
    expected_size: tuple[int, int],
    expected_format: str,
    expected_frame_count: int,
    expected_durations: tuple[int, ...],
    expected_loop: int,
) -> None:
    """Re-decode an animation and verify its frame and timing contract."""

    try:
        with Image.open(path) as decoded:
            actual_format = (decoded.format or "").upper()
            frame_count = int(getattr(decoded, "n_frames", 1))
            if actual_format != expected_format:
                raise ConversionOutputValidationError(
                    f"动画输出格式验证失败：期望 {expected_format}，实际 {actual_format or '未知'}"
                )
            if frame_count != expected_frame_count or not bool(
                getattr(decoded, "is_animated", False)
            ):
                raise ConversionOutputValidationError(
                    f"动画帧数验证失败：期望 {expected_frame_count}，实际 {frame_count}"
                )
            if decoded.size != expected_size:
                raise ConversionOutputValidationError(
                    f"动画输出尺寸验证失败：期望 {expected_size[0]} × {expected_size[1]}，"
                    f"实际 {decoded.width} × {decoded.height}"
                )
            actual_loop = decoded.info.get("loop", 0)
            if int(actual_loop) != expected_loop:
                raise ConversionOutputValidationError(
                    f"动画循环验证失败：期望 {expected_loop}，实际 {actual_loop}"
                )
            actual_durations: list[int]
            if expected_format == "WEBP":
                actual_durations = _webp_frame_durations(path)
            else:
                actual_durations = []
                for index in range(frame_count):
                    decoded.seek(index)
                    duration = decoded.info.get("duration", 0)
                    actual_durations.append(
                        int(round(duration))
                        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
                        else 0
                    )
            if tuple(actual_durations) != expected_durations:
                raise ConversionOutputValidationError(
                    f"动画时长验证失败：期望 {list(expected_durations)}，实际 {actual_durations}"
                )
    except ConversionOutputValidationError:
        raise
    except Exception as error:
        raise ConversionOutputValidationError(f"动画输出无法重新解码：{error}") from error


def _webp_frame_durations(path: Path) -> list[int]:
    """Read the 24-bit millisecond duration stored in every WebP ANMF chunk."""

    payload = path.read_bytes()
    if len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP":
        raise ConversionOutputValidationError("WebP 动画缺少有效的 RIFF/WEBP 文件头")
    durations: list[int] = []
    offset = 12
    while offset + 8 <= len(payload):
        chunk_type = payload[offset : offset + 4]
        chunk_size = int.from_bytes(payload[offset + 4 : offset + 8], "little")
        start = offset + 8
        end = start + chunk_size
        if end > len(payload):
            raise ConversionOutputValidationError("WebP 动画包含截断的数据块")
        if chunk_type == b"ANMF":
            if chunk_size < 16:
                raise ConversionOutputValidationError("WebP 动画包含无效的 ANMF 帧头")
            durations.append(int.from_bytes(payload[start + 12 : start + 15], "little"))
        offset = end + (chunk_size % 2)
    return durations


def _transparency_characteristics(image: Image.Image) -> tuple[bool, bool]:
    """Return (has_non_opaque_pixels, has_partial_alpha_pixels)."""

    histogram: list[int] | None = None
    if "A" in image.getbands():
        alpha = image.getchannel("A")
        try:
            histogram = alpha.histogram()
        finally:
            alpha.close()
    elif image.mode == "P" and "transparency" in image.info:
        rgba = image.convert("RGBA")
        try:
            alpha = rgba.getchannel("A")
            try:
                histogram = alpha.histogram()
            finally:
                alpha.close()
        finally:
            rgba.close()
    if histogram is None:
        return False, False
    return sum(histogram[:255]) > 0, any(histogram[1:255])


def _flatten_over_background(image: Image.Image, background: str) -> Image.Image:
    red = int(background[1:3], 16)
    green = int(background[3:5], 16)
    blue = int(background[5:7], 16)
    rgba = image.convert("RGBA")
    result = Image.new("RGB", image.size, (red, green, blue))
    try:
        result.paste(rgba, mask=rgba.getchannel("A"))
    finally:
        rgba.close()
    return result


def _backup_target(path: Path) -> Path:
    base = path.with_name(path.name + ".bak")
    if not base.exists():
        return base
    for index in range(1, 10_000):
        candidate = base.with_name(f"{base.stem}_{index}{base.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"无法为目标生成不冲突的备份文件名：{path}")


class ConversionRunner:
    """Run one or many enabled queue inputs through the same frozen recipe.

    At most ``max_workers`` futures exist at once. Cancellation immediately marks
    undispatched items and workers check the signal before any output is written.
    A runner is intentionally one-shot so task paths and status history stay stable.
    """

    def __init__(
        self,
        queue: ConversionQueue,
        recipe: TransformRecipe,
        output_directory: str | os.PathLike[str],
        *,
        max_workers: int = 2,
    ) -> None:
        if not isinstance(queue, ConversionQueue):
            raise TypeError("queue 必须是 ConversionQueue")
        if not isinstance(recipe, TransformRecipe):
            raise TypeError("recipe 必须是 TransformRecipe")
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or not 1 <= max_workers <= MAX_CONVERSION_WORKERS
        ):
            raise ValueError(f"max_workers 必须位于 1 到 {MAX_CONVERSION_WORKERS} 之间")

        # TransformStep contains JSON-shaped dictionaries, so a strict round trip
        # gives this run an isolated snapshot even if a caller retains those dicts.
        self.recipe = TransformRecipe.from_json(recipe.to_json())
        self.output_directory = _normalize_path(output_directory)
        self.max_workers = max_workers
        self._cancelled = threading.Event()
        self._state_lock = threading.RLock()
        self._run_started = False
        self._peak_in_flight = 0
        self._dispatch_task_indices: frozenset[int] | None = None
        self._tasks = self._plan_tasks(queue.enabled_items)

    @classmethod
    def retry_tasks(
        cls,
        tasks: Iterable[ConversionTaskRecord],
        recipe: TransformRecipe,
        output_directory: str | os.PathLike[str],
        *,
        max_workers: int = 2,
        statuses: Iterable[ConversionTaskStatus] = (
            ConversionTaskStatus.FAILED,
            ConversionTaskStatus.CANCELLED,
        ),
    ) -> ConversionRunner:
        """Create a runner that dispatches only selected non-success states.

        Successful records are retained verbatim. Selected records keep their
        original ``planned_output`` and attempt count while transient fields are
        reset for the next attempt.
        """

        selected_statuses = frozenset(statuses)
        if not selected_statuses:
            raise ValueError("重试状态不能为空")
        if any(not isinstance(status, ConversionTaskStatus) for status in selected_statuses):
            raise TypeError("重试状态必须是 ConversionTaskStatus")
        if ConversionTaskStatus.SUCCEEDED in selected_statuses:
            raise ValueError("已成功任务绝不能加入重试集合")

        runner = cls(ConversionQueue(), recipe, output_directory, max_workers=max_workers)
        restored: list[ConversionTaskRecord] = []
        dispatch_indices: set[int] = set()
        seen_indices: set[int] = set()
        for position, task in enumerate(tasks):
            if not isinstance(task, ConversionTaskRecord):
                raise TypeError("重试任务必须是 ConversionTaskRecord")
            if task.index in seen_indices:
                raise ValueError(f"重试任务 index 重复：{task.index}")
            seen_indices.add(task.index)
            planned_output = _normalize_path(task.planned_output)
            output = None if task.output is None else _normalize_path(task.output)
            normalized = replace(task, planned_output=planned_output, output=output)
            if task.status in selected_statuses:
                normalized = replace(
                    normalized,
                    status=ConversionTaskStatus.PENDING,
                    error=None,
                    started_at=None,
                    finished_at=None,
                    input_bytes=0,
                    output_bytes=0,
                    output=None,
                )
                dispatch_indices.add(position)
            restored.append(normalized)
        with runner._state_lock:
            runner._tasks = restored
            runner._dispatch_task_indices = frozenset(dispatch_indices)
        return runner

    @property
    def tasks(self) -> tuple[ConversionTaskRecord, ...]:
        with self._state_lock:
            return tuple(self._tasks)

    @property
    def cancel_requested(self) -> bool:
        return self._cancelled.is_set()

    @property
    def peak_in_flight(self) -> int:
        with self._state_lock:
            return self._peak_in_flight

    def cancel(self) -> None:
        """Stop future dispatch and immediately cancel every not-yet-started item."""

        self._cancelled.set()
        finished_at = _now()
        with self._state_lock:
            for index, task in enumerate(self._tasks):
                if task.status is ConversionTaskStatus.PENDING:
                    self._tasks[index] = replace(
                        task,
                        status=ConversionTaskStatus.CANCELLED,
                        error="用户取消",
                        finished_at=finished_at,
                    )

    def run(self) -> ConversionRunResult:
        with self._state_lock:
            if self._run_started:
                raise RuntimeError("ConversionRunner 只能运行一次")
            self._run_started = True
        started_at = _now()
        pending = deque(
            index
            for index, task in enumerate(self.tasks)
            if task.status is ConversionTaskStatus.PENDING
            and (self._dispatch_task_indices is None or index in self._dispatch_task_indices)
        )
        in_flight: dict[Future[None], int] = {}

        with ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="image-conversion",
        ) as executor:
            while pending or in_flight:
                while (
                    pending and len(in_flight) < self.max_workers and not self._cancelled.is_set()
                ):
                    task_index = pending.popleft()
                    if self._task(task_index).status is not ConversionTaskStatus.PENDING:
                        continue
                    future = executor.submit(self._process_task, task_index)
                    in_flight[future] = task_index
                    with self._state_lock:
                        self._peak_in_flight = max(self._peak_in_flight, len(in_flight))

                if self._cancelled.is_set():
                    self.cancel()
                    pending.clear()
                    for future, task_index in tuple(in_flight.items()):
                        if future.cancel():
                            self._mark_cancelled(task_index)
                            in_flight.pop(future)

                if not in_flight:
                    break
                completed, _ = wait(tuple(in_flight), return_when="FIRST_COMPLETED")
                for future in completed:
                    task_index = in_flight.pop(future)
                    try:
                        future.result()
                    except Exception as error:  # Defensive: workers normally isolate all errors.
                        task = self._task(task_index)
                        if task.status is ConversionTaskStatus.RUNNING:
                            self._mark_failed(task_index, error)

        finished_at = _now()
        return ConversionRunResult(
            tasks=self.tasks,
            started_at=started_at,
            finished_at=finished_at,
            cancel_requested=self._cancelled.is_set(),
            peak_in_flight=self.peak_in_flight,
        )

    def _plan_tasks(self, inputs: tuple[ConversionInput, ...]) -> list[ConversionTaskRecord]:
        profile = self.recipe.export_profile
        extension = extension_for_format(profile.output_format)
        planned_at = _now()
        claimed: set[str] = set()
        input_keys = {_path_key(item.path) for item in inputs}
        tasks: list[ConversionTaskRecord] = []

        for index, item in enumerate(inputs, start=1):
            stem = render_name(profile.rename_template, item.path, index, now=planned_at)
            target = self.output_directory / f"{stem}{extension}"
            planning_error: str | None = None
            key = _path_key(target)

            if profile.conflict_policy == ConflictPolicy.UNIQUE.value:
                suffix_index = 1
                while target.exists() or key in claimed:
                    if suffix_index >= 10_000:
                        planning_error = "FileExistsError: 无法生成不冲突的预定输出路径"
                        break
                    target = target.with_name(f"{stem}_{suffix_index}{extension}")
                    key = _path_key(target)
                    suffix_index += 1
            elif profile.conflict_policy == ConflictPolicy.ERROR.value:
                if target.exists():
                    planning_error = f"FileExistsError: 目标文件已存在：{target}"
                elif key in claimed:
                    planning_error = f"FileExistsError: 多个输入计划写入同一输出：{target}"
            elif key in claimed:
                planning_error = f"FileExistsError: 多个输入不能同时覆盖同一输出：{target}"

            if planning_error is None and key in input_keys:
                if _path_key(item.path) != key:
                    planning_error = "ValueError: 预定输出会覆盖队列中的另一输入文件"
                elif not profile.allow_in_place:
                    planning_error = "ValueError: 输入与输出为同一文件，且配方未允许原位覆盖"

            claimed.add(key)
            if planning_error is None:
                tasks.append(ConversionTaskRecord(index=index, input=item, planned_output=target))
            else:
                tasks.append(
                    ConversionTaskRecord(
                        index=index,
                        input=item,
                        planned_output=target,
                        status=ConversionTaskStatus.FAILED,
                        error=planning_error,
                        finished_at=planned_at,
                    )
                )
        return tasks

    def _process_task(self, task_index: int) -> None:
        if not self._mark_running(task_index):
            return
        task = self._task(task_index)
        source_path = task.input.path
        input_bytes = 0
        output_bytes = 0
        try:
            self._raise_if_cancelled()
            input_bytes = source_path.stat().st_size
            fingerprint = SourceFingerprint.capture(source_path)
            source = open_image(source_path)
            transformed: Image.Image | None = None
            try:
                metadata = metadata_from_image(source)
                animated = metadata.is_animated or metadata.frame_count > 1
                if not animated or not self.recipe.export_profile.preserve_animation:
                    self._raise_if_cancelled()
                    transformed = execute_transform_recipe(source, self.recipe)
            finally:
                source.close()

            if animated and self.recipe.export_profile.preserve_animation:
                (
                    saved,
                    expected_size,
                    expected_frame_count,
                    expected_durations,
                    expected_loop,
                ) = self._transform_and_save_animation(
                    source_path,
                    task.planned_output,
                    source_fingerprint=fingerprint,
                )
                output_bytes = saved.stat().st_size
                _validate_animation_output(
                    saved,
                    expected_size=expected_size,
                    expected_format=self.recipe.export_profile.output_format,
                    expected_frame_count=expected_frame_count,
                    expected_durations=expected_durations,
                    expected_loop=expected_loop,
                )
            else:
                assert transformed is not None
                try:
                    self._raise_if_cancelled()
                    expected_size = transformed.size
                    saved = self._save_transformed(
                        transformed,
                        task.planned_output,
                        source_path=source_path,
                        source_fingerprint=fingerprint,
                        metadata=metadata,
                    )
                finally:
                    transformed.close()

                output_bytes = saved.stat().st_size
                _validate_static_output(
                    saved,
                    expected_size=expected_size,
                    expected_format=self.recipe.export_profile.output_format,
                )

            self._update_task(
                task_index,
                status=ConversionTaskStatus.SUCCEEDED,
                error=None,
                finished_at=_now(),
                input_bytes=input_bytes,
                output_bytes=output_bytes,
                output=saved,
            )
        except _TaskCancelled:
            self._update_task(
                task_index,
                status=ConversionTaskStatus.CANCELLED,
                error="用户取消",
                finished_at=_now(),
                input_bytes=input_bytes,
                output_bytes=output_bytes,
            )
        except Exception as error:
            self._mark_failed(
                task_index,
                error,
                input_bytes=input_bytes,
                output_bytes=output_bytes,
            )

    def _transform_and_save_animation(
        self,
        source_path: Path,
        target: Path,
        *,
        source_fingerprint: SourceFingerprint,
    ) -> tuple[Path, tuple[int, int], int, tuple[int, ...], int]:
        profile = self.recipe.export_profile
        if profile.output_format not in ANIMATED_OUTPUT_FORMATS:
            raise UnsupportedAnimationError(
                "配方要求保留动画，但目标格式 "
                f"{profile.output_format} 不支持逐帧动画；请选择 GIF、PNG（APNG）或 WEBP，"
                "或将 preserve_animation 设为 false 以明确展平首帧"
            )

        sequence = decode_animation(source_path)
        transformed_frames: list[Image.Image] = []
        try:
            expected_size: tuple[int, int] | None = None
            for frame in sequence.frames:
                self._raise_if_cancelled()
                transformed = execute_transform_recipe(frame, self.recipe)
                _visible_transparency, partial_transparency = _transparency_characteristics(
                    transformed
                )
                if profile.output_format == "GIF" and partial_transparency:
                    if profile.transparency_background is None:
                        transformed.close()
                        raise TransparencyPolicyError(
                            "GIF 只能保留二值透明，当前动画包含半透明像素；"
                            "请选择 PNG（APNG）或 WEBP，或在配方 export."
                            "transparency_background 中明确指定 #RRGGBB 背景色"
                        )
                    flattened = _flatten_over_background(
                        transformed,
                        profile.transparency_background,
                    )
                    transformed.close()
                    transformed = flattened
                if expected_size is None:
                    expected_size = transformed.size
                elif transformed.size != expected_size:
                    transformed.close()
                    raise UnsupportedAnimationError("逐帧配方生成了不一致的画面尺寸，无法编码动画")
                transformed_frames.append(transformed)
            self._raise_if_cancelled()
            assert expected_size is not None
            transformed_sequence = AnimationSequence(
                frames=tuple(transformed_frames),
                durations=sequence.durations,
                loop=sequence.loop,
                source_format=sequence.source_format,
                memory_bytes=sum(
                    frame.width * frame.height * len(frame.getbands())
                    for frame in transformed_frames
                ),
            )
            saved = self._save_animation_sequence(
                transformed_sequence,
                target,
                source_path=source_path,
                source_fingerprint=source_fingerprint,
            )
            return (
                saved,
                expected_size,
                sequence.frame_count,
                sequence.durations,
                sequence.loop,
            )
        finally:
            for frame in transformed_frames:
                frame.close()
            for frame in sequence.frames:
                frame.close()

    def _save_animation_sequence(
        self,
        sequence: AnimationSequence,
        target: Path,
        *,
        source_path: Path,
        source_fingerprint: SourceFingerprint,
    ) -> Path:
        profile = self.recipe.export_profile
        target.parent.mkdir(parents=True, exist_ok=True)
        overwrite = profile.conflict_policy == ConflictPolicy.OVERWRITE.value
        if target.exists() and not overwrite:
            raise FileExistsError(f"目标文件已存在：{target}")
        if not source_fingerprint.matches(source_path):
            raise SourceChangedError("源文件已被外部修改、移动或删除，不能写入动画输出。")

        descriptor, staging_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=target.suffix,
            dir=target.parent,
        )
        os.close(descriptor)
        staging = Path(staging_name)
        try:
            export_animation(
                sequence,
                staging,
                profile.output_format,
                quality=profile.quality,
                webp_lossless=profile.webp_lossless,
                png_palette_colors=profile.png_palette_colors,
            )
            self._raise_if_cancelled()
            if not source_fingerprint.matches(source_path):
                raise SourceChangedError("源文件已被外部修改、移动或删除，不能写入动画输出。")
            if target.exists():
                if not overwrite:
                    raise FileExistsError(f"目标文件已存在：{target}")
                if profile.backup_on_overwrite:
                    shutil.copy2(target, _backup_target(target))
            os.replace(staging, target)
            return target
        finally:
            staging.unlink(missing_ok=True)

    def _save_transformed(
        self,
        image: Image.Image,
        target: Path,
        *,
        source_path: Path,
        source_fingerprint: SourceFingerprint,
        metadata: SourceMetadata,
    ) -> Path:
        profile = self.recipe.export_profile
        output_format = profile.output_format
        prepared = image
        prepared_owned = False
        visible_transparency, partial_transparency = _transparency_characteristics(image)
        requires_background = output_format in {"JPEG", "BMP"} and visible_transparency
        requires_background = requires_background or (
            output_format == "GIF" and partial_transparency
        )
        if requires_background and profile.transparency_background is None:
            if output_format == "GIF":
                raise TransparencyPolicyError(
                    "GIF 只能保留二值透明，当前图片包含半透明像素；"
                    "请选择 PNG 或 WEBP，或在配方 export.transparency_background "
                    "中明确指定 #RRGGBB 背景色"
                )
            raise TransparencyPolicyError(
                f"{output_format} 不支持透明通道；请在配方 export."
                "transparency_background 中明确指定 #RRGGBB 背景色"
            )
        if output_format in {"JPEG", "BMP"}:
            if "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info):
                if profile.transparency_background is None:
                    prepared = image.convert("RGB")
                else:
                    prepared = _flatten_over_background(
                        image,
                        profile.transparency_background,
                    )
                prepared_owned = True
        elif output_format == "GIF" and partial_transparency:
            assert profile.transparency_background is not None
            prepared = _flatten_over_background(image, profile.transparency_background)
            prepared_owned = True
        # Unique names were allocated during planning. ERROR here intentionally
        # preserves planned_output if another process occupies it before execution.
        conflict_policy = (
            ConflictPolicy.OVERWRITE
            if profile.conflict_policy == ConflictPolicy.OVERWRITE.value
            else ConflictPolicy.ERROR
        )
        try:
            return save_image(
                prepared,
                target,
                _export_options(self.recipe),
                metadata=metadata,
                source_path=source_path,
                source_fingerprint=source_fingerprint,
                conflict_policy=conflict_policy,
                backup_on_overwrite=profile.backup_on_overwrite,
                allow_animation_flatten=not profile.preserve_animation,
                allow_transparency_flatten=(
                    output_format not in {"JPEG", "BMP"}
                    or not visible_transparency
                    or profile.transparency_background is not None
                ),
            )
        finally:
            if prepared_owned:
                prepared.close()

    def _task(self, index: int) -> ConversionTaskRecord:
        with self._state_lock:
            return self._tasks[index]

    def _update_task(self, index: int, **changes: Any) -> ConversionTaskRecord:
        with self._state_lock:
            current = self._tasks[index]
            updated = replace(current, **changes)
            self._tasks[index] = updated
            return updated

    def _mark_running(self, index: int) -> bool:
        with self._state_lock:
            task = self._tasks[index]
            if self._cancelled.is_set() or task.status is not ConversionTaskStatus.PENDING:
                if task.status is ConversionTaskStatus.PENDING:
                    self._tasks[index] = replace(
                        task,
                        status=ConversionTaskStatus.CANCELLED,
                        error="用户取消",
                        finished_at=_now(),
                    )
                return False
            self._tasks[index] = replace(
                task,
                status=ConversionTaskStatus.RUNNING,
                attempts=task.attempts + 1,
                started_at=_now(),
            )
            return True

    def _mark_cancelled(self, index: int) -> None:
        task = self._task(index)
        if task.status in {ConversionTaskStatus.PENDING, ConversionTaskStatus.RUNNING}:
            self._update_task(
                index,
                status=ConversionTaskStatus.CANCELLED,
                error="用户取消",
                finished_at=_now(),
            )

    def _mark_failed(
        self,
        index: int,
        error: Exception,
        *,
        input_bytes: int = 0,
        output_bytes: int = 0,
    ) -> None:
        self._update_task(
            index,
            status=ConversionTaskStatus.FAILED,
            error=f"{type(error).__name__}: {error}",
            finished_at=_now(),
            input_bytes=input_bytes,
            output_bytes=output_bytes,
        )

    def _raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise _TaskCancelled("用户取消")
