"""Optional image codecs and safe animated-image I/O."""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

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
    webp_lossless: bool = False,
    png_palette_colors: int | None = None,
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
    palette_colors = 256 if fmt == "GIF" else None
    if palette_colors is not None:
        quantized: list[Image.Image] = []
        try:
            for frame in prepared:
                rgba = frame.convert("RGBA")
                try:
                    quantized.append(
                        rgba.quantize(
                            colors=palette_colors,
                            method=Image.Quantize.FASTOCTREE,
                            dither=Image.Dither.FLOYDSTEINBERG,
                        )
                    )
                finally:
                    rgba.close()
        except BaseException:
            for frame in quantized:
                frame.close()
            raise
        for frame in prepared:
            frame.close()
        prepared = quantized
    elif fmt == "PNG" and png_palette_colors is not None:
        if not 2 <= png_palette_colors <= 256:
            for frame in prepared:
                frame.close()
            raise ValueError("PNG 调色板颜色数必须位于 2 到 256 之间")
        for frame in prepared:
            rgba = frame.convert("RGBA")
            try:
                alpha = rgba.getchannel("A")
                try:
                    min_alpha = cast(int, alpha.getextrema()[0])
                finally:
                    alpha.close()
                if min_alpha < 255:
                    for item in prepared:
                        item.close()
                    raise ValueError("带透明通道的 APNG 暂不支持调色板量化")
            finally:
                rgba.close()
        atlas_width = max(frame.width for frame in prepared)
        atlas_height = sum(frame.height for frame in prepared)
        atlas = Image.new("RGB", (atlas_width, atlas_height))
        shared_palette: Image.Image | None = None
        quantized = []
        try:
            top = 0
            for frame in prepared:
                rgb = frame.convert("RGB")
                try:
                    atlas.paste(rgb, (0, top))
                finally:
                    rgb.close()
                top += frame.height
            shared_palette = atlas.quantize(
                colors=png_palette_colors,
                method=Image.Quantize.MEDIANCUT,
            )
            for frame in prepared:
                rgb = frame.convert("RGB")
                try:
                    quantized.append(
                        rgb.quantize(
                            palette=shared_palette,
                            dither=Image.Dither.FLOYDSTEINBERG,
                        )
                    )
                finally:
                    rgb.close()
        except BaseException:
            for frame in quantized:
                frame.close()
            raise
        finally:
            atlas.close()
            if shared_palette is not None:
                shared_palette.close()
        for frame in prepared:
            frame.close()
        prepared = quantized
    kwargs: dict[str, object] = {
        "save_all": True,
        "append_images": prepared[1:],
        "duration": list(sequence.durations),
        "loop": sequence.loop,
    }
    if fmt == "WEBP":
        kwargs.update(
            quality=max(1, min(100, quality)),
            lossless=bool(webp_lossless),
            method=6,
        )
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
