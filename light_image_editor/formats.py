"""Optional image codecs and safe animated-image I/O."""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, features

ANIMATION_FORMATS = frozenset({"GIF", "PNG", "WEBP"})
HEIF_EXTENSIONS = frozenset({".heic", ".heics", ".heif", ".heifs", ".hif"})
DEFAULT_ANIMATION_MEMORY_LIMIT = 512 * 1024 * 1024


@dataclass(frozen=True)
class CodecCapabilities:
    avif: bool
    animated_webp: bool
    heif: bool
    heif_detail: str


@dataclass(frozen=True)
class AnimationSequence:
    frames: tuple[Image.Image, ...]
    durations: tuple[int, ...]
    loop: int
    source_format: str
    memory_bytes: int

    @property
    def frame_count(self) -> int:
        return len(self.frames)


_heif_registered = False


def register_optional_heif() -> tuple[bool, str]:
    """Register pillow-heif when installed without making it a base dependency."""

    global _heif_registered
    if _heif_registered:
        return True, "pillow-heif 已启用"
    try:
        pillow_heif = importlib.import_module("pillow_heif")
        register = getattr(pillow_heif, "register_heif_opener")
        register(thumbnails=False)
    except (ImportError, AttributeError, OSError) as error:
        return False, f"可选 HEIF 插件未安装或不可用：{error}"
    _heif_registered = True
    return True, "pillow-heif 已启用"


def codec_capabilities() -> CodecCapabilities:
    heif, detail = register_optional_heif()
    return CodecCapabilities(
        avif=bool(features.check("avif")),
        animated_webp=bool(features.check("webp")),
        heif=heif,
        heif_detail=detail,
    )


def decode_animation(
    path: str | Path,
    *,
    max_frames: int = 10_000,
    memory_limit_bytes: int = DEFAULT_ANIMATION_MEMORY_LIMIT,
) -> AnimationSequence:
    """Decode every composited frame while enforcing frame and memory limits."""

    image_path = Path(path)
    with Image.open(image_path) as source:
        source_format = (source.format or image_path.suffix.lstrip(".")).upper()
        frame_count = int(getattr(source, "n_frames", 1))
        if frame_count <= 1:
            raise ValueError("文件不是多帧动画图片")
        if frame_count > max_frames:
            raise ValueError(f"动画包含 {frame_count} 帧，超过 {max_frames} 帧安全上限")
        estimated = source.width * source.height * 4 * frame_count
        if estimated > memory_limit_bytes:
            raise MemoryError(
                f"动画解码预计需要 {estimated / 1024 / 1024:.1f} MB，"
                f"超过 {memory_limit_bytes / 1024 / 1024:.1f} MB 限额"
            )
        frames: list[Image.Image] = []
        durations: list[int] = []
        for index in range(frame_count):
            source.seek(index)
            frame = source.convert("RGBA").copy()
            frames.append(frame)
            duration = source.info.get("duration", 100)
            durations.append(max(20, int(duration) if isinstance(duration, int) else 100))
        loop = source.info.get("loop", 0)
    actual = sum(frame.width * frame.height * len(frame.getbands()) for frame in frames)
    return AnimationSequence(
        frames=tuple(frames),
        durations=tuple(durations),
        loop=int(loop) if isinstance(loop, int) else 0,
        source_format=source_format,
        memory_bytes=actual,
    )


def export_animation(
    sequence: AnimationSequence,
    target: str | Path,
    output_format: str,
    *,
    quality: int = 85,
    transform: Callable[[Image.Image], Image.Image] | None = None,
) -> Path:
    """Atomically export GIF/APNG/WebP animation, optionally transforming each frame."""

    fmt = output_format.strip().upper()
    if fmt == "APNG":
        fmt = "PNG"
    if fmt not in ANIMATION_FORMATS:
        raise ValueError("动画导出仅支持 GIF、APNG/PNG 和 WebP")
    if sequence.frame_count < 2:
        raise ValueError("动画导出至少需要两帧")
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    prepared = [transform(frame.copy()) if transform else frame.copy() for frame in sequence.frames]
    if fmt == "GIF":
        prepared = [frame.convert("RGBA").quantize(colors=256) for frame in prepared]
    kwargs: dict[str, object] = {
        "save_all": True,
        "append_images": prepared[1:],
        "duration": list(sequence.durations),
        "loop": sequence.loop,
    }
    if fmt == "WEBP":
        kwargs.update(quality=max(1, min(100, quality)), method=6)
    if fmt == "PNG":
        kwargs.update(optimize=True, compress_level=9)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=destination.suffix, dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        prepared[0].save(temporary, fmt, **kwargs)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
        for frame in prepared:
            frame.close()
    return destination
