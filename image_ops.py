from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps, PngImagePlugin

if TYPE_CHECKING:
    from light_image_editor.commands import EditTimeline
    from light_image_editor.document import SourceFingerprint, SourceMetadata

SUPPORTED_INPUTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".gif",
    ".apng",
    ".avif",
    ".heic",
    ".heif",
}

SUPPORTED_FORMATS = ("JPEG", "PNG", "WEBP", "AVIF", "BMP", "TIFF", "GIF")
_NATURAL_PARTS = re.compile(r"(\d+)")


@dataclass(frozen=True)
class EditState:
    rotation: int = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    brightness: int = 0
    contrast: int = 0
    saturation: int = 0
    sharpness: int = 0
    grayscale: bool = False
    auto_contrast: bool = False


@dataclass(frozen=True)
class ExportOptions:
    output_format: str
    quality: int = 85
    target_size_kb: int | None = None
    webp_lossless: bool = False
    png_palette_colors: int | None = None
    tiff_compression: str = "raw"
    max_width: int | None = None
    max_height: int | None = None
    exact_width: int | None = None
    exact_height: int | None = None
    crop_box: tuple[int, int, int, int] | None = None
    edit_state: EditState = EditState()
    watermark_text: str = ""
    watermark_position: str = "右下"
    watermark_opacity: int = 35
    watermark_size_percent: int = 5
    preserve_metadata: bool = True


class ConflictPolicy(StrEnum):
    ERROR = "error"
    OVERWRITE = "overwrite"
    UNIQUE = "unique"


class SaveSafetyError(RuntimeError):
    """Base class for a save that requires an explicit user decision."""


class AnimationFlatteningRequired(SaveSafetyError):
    pass


class TransparencyFlatteningRequired(SaveSafetyError):
    pass


class SourceChangedError(SaveSafetyError):
    pass


def open_image(path: str | Path) -> Image.Image:
    from light_image_editor.image_loader import decode_image

    return decode_image(path)


def normalize_format(value: str) -> str:
    fmt = value.strip().upper()
    if fmt == "JPG":
        fmt = "JPEG"
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"不支持的输出格式: {value}")
    return fmt


def extension_for_format(output_format: str) -> str:
    fmt = normalize_format(output_format)
    if fmt == "JPEG":
        return ".jpg"
    if fmt == "TIFF":
        return ".tif"
    return "." + fmt.lower()


def natural_sort_key(path: str | Path) -> tuple[tuple[int, int | str], ...]:
    name = Path(path).name.lower()
    parts: list[tuple[int, int | str]] = []
    for part in _NATURAL_PARTS.split(name):
        if part.isdigit():
            parts.append((0, int(part)))
        elif part:
            parts.append((1, part))
    return tuple(parts)


def adjustment_factor(value: int) -> float:
    return max(0.0, 1.0 + value / 100.0)


def apply_edits(image: Image.Image, state: EditState) -> Image.Image:
    result = image.copy()

    rotation = state.rotation % 360
    if rotation:
        result = result.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
    if state.flip_horizontal:
        result = ImageOps.mirror(result)
    if state.flip_vertical:
        result = ImageOps.flip(result)
    if state.grayscale:
        result = ImageOps.grayscale(result).convert("RGB")
    if state.auto_contrast:
        if result.mode in {"RGBA", "LA"}:
            channels = result.split()
            rgb = Image.merge("RGB", channels[:3])
            enhanced = ImageOps.autocontrast(rgb)
            result = Image.merge(result.mode, (*enhanced.split(), channels[-1]))
        else:
            result = ImageOps.autocontrast(result)

    if state.brightness:
        result = ImageEnhance.Brightness(result).enhance(adjustment_factor(state.brightness))
    if state.contrast:
        result = ImageEnhance.Contrast(result).enhance(adjustment_factor(state.contrast))
    if state.saturation and result.mode != "L":
        result = ImageEnhance.Color(result).enhance(adjustment_factor(state.saturation))
    if state.sharpness:
        result = ImageEnhance.Sharpness(result).enhance(adjustment_factor(state.sharpness))

    return result


def prepare_image(image: Image.Image, options: ExportOptions) -> Image.Image:
    result = apply_edits(image, options.edit_state)

    if options.crop_box:
        left, top, right, bottom = options.crop_box
        left = max(0, min(left, result.width - 1))
        top = max(0, min(top, result.height - 1))
        right = max(left + 1, min(right, result.width))
        bottom = max(top + 1, min(bottom, result.height))
        result = result.crop((left, top, right, bottom))

    if options.exact_width and options.exact_height:
        result = result.resize(
            (options.exact_width, options.exact_height), Image.Resampling.LANCZOS
        )
    elif options.max_width or options.max_height:
        max_width = options.max_width or result.width
        max_height = options.max_height or result.height
        result.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

    if options.watermark_text.strip():
        result = apply_text_watermark(
            result,
            options.watermark_text.strip(),
            options.watermark_position,
            options.watermark_opacity,
            options.watermark_size_percent,
        )

    return result


def apply_text_watermark(
    image: Image.Image,
    text: str,
    position: str = "右下",
    opacity: int = 35,
    size_percent: int = 5,
) -> Image.Image:
    if not text.strip():
        return image.copy()

    result = image.convert("RGBA")
    overlay = Image.new("RGBA", result.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = watermark_font(result.size, size_percent)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=2)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    margin = max(12, round(min(result.size) * 0.035))
    x, y = watermark_xy(position, result.width, result.height, text_w, text_h, margin)
    alpha = max(0, min(100, int(opacity))) * 255 // 100
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, alpha),
        stroke_width=2,
        stroke_fill=(0, 0, 0, max(0, alpha - 35)),
    )
    watermarked = Image.alpha_composite(result, overlay)
    if image.mode == "RGBA":
        return watermarked
    if image.mode == "LA":
        return watermarked.convert("LA")
    return watermarked.convert(image.mode)


def watermark_font(image_size: tuple[int, int], size_percent: int) -> ImageFont.ImageFont:
    font_size = max(14, round(min(image_size) * max(1, min(30, int(size_percent))) / 100))
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), font_size)
            except OSError:
                continue
    return ImageFont.load_default(size=font_size)


def watermark_xy(
    position: str, image_w: int, image_h: int, text_w: int, text_h: int, margin: int
) -> tuple[int, int]:
    positions = {
        "左上": (margin, margin),
        "右上": (image_w - text_w - margin, margin),
        "左下": (margin, image_h - text_h - margin),
        "右下": (image_w - text_w - margin, image_h - text_h - margin),
        "居中": ((image_w - text_w) // 2, (image_h - text_h) // 2),
    }
    x, y = positions.get(position, positions["右下"])
    return (max(0, x), max(0, y))


def estimate_save_size(
    image: Image.Image, options: ExportOptions, *, timeline: EditTimeline | None = None
) -> int:
    if timeline is not None:
        from light_image_editor.renderer import render_image

        prepared = render_image(image, timeline)
    else:
        prepared = prepare_image(image, options)
    output_format = normalize_format(options.output_format)
    converted = convert_for_format(prepared, output_format, options)
    try:
        return len(encode_image(converted, output_format, options))
    finally:
        if converted is not prepared:
            converted.close()
        prepared.close()


def encode_image(
    image: Image.Image,
    output_format: str,
    options: ExportOptions,
    extra_kwargs: dict[str, object] | None = None,
) -> bytes:
    kwargs = save_kwargs_for(
        output_format,
        options.quality,
        webp_lossless=options.webp_lossless,
        tiff_compression=options.tiff_compression,
    )
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    if options.target_size_kb and output_format in {"JPEG", "WEBP"}:
        if output_format == "WEBP" and options.webp_lossless:
            raise ValueError("WebP 无损不能与目标 KB 同时启用")
        return encode_to_target_size(
            image,
            output_format,
            options.quality,
            options.target_size_kb * 1024,
            extra_kwargs=extra_kwargs,
        )
    buffer = BytesIO()
    image.save(buffer, output_format, **kwargs)
    return buffer.getvalue()


def encode_to_target_size(
    image: Image.Image,
    output_format: str,
    max_quality: int,
    target_bytes: int,
    *,
    extra_kwargs: dict[str, object] | None = None,
) -> bytes:
    low = 1
    high = max(1, min(100, int(max_quality)))
    best_under: bytes | None = None
    best_over: bytes | None = None

    while low <= high:
        quality = (low + high) // 2
        buffer = BytesIO()
        kwargs = save_kwargs_for(output_format, quality)
        if extra_kwargs:
            kwargs.update(extra_kwargs)
        image.save(buffer, output_format, **kwargs)
        data = buffer.getvalue()
        if len(data) <= target_bytes:
            best_under = data
            low = quality + 1
        else:
            best_over = data
            high = quality - 1

    if best_under is not None:
        return best_under
    if best_over is not None:
        return best_over
    buffer = BytesIO()
    kwargs = save_kwargs_for(output_format, 1)
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    image.save(buffer, output_format, **kwargs)
    return buffer.getvalue()


def save_kwargs_for(
    output_format: str,
    quality: int,
    *,
    webp_lossless: bool = False,
    tiff_compression: str = "raw",
) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    if output_format in {"JPEG", "WEBP", "AVIF"}:
        kwargs["quality"] = max(1, min(100, int(quality)))
        kwargs["optimize"] = True
    if output_format == "WEBP":
        kwargs["lossless"] = bool(webp_lossless)
        kwargs["method"] = 6
    if output_format == "PNG":
        kwargs["optimize"] = True
        kwargs["compress_level"] = 9
    if output_format == "TIFF":
        kwargs["compression"] = tiff_compression
    return kwargs


def convert_for_format(
    image: Image.Image,
    output_format: str,
    options: ExportOptions | None = None,
) -> Image.Image:
    if output_format == "JPEG" and image.mode in {"RGBA", "LA", "P"}:
        rgba = image.convert("RGBA") if image.mode == "P" else image
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A") if "A" in rgba.getbands() else None)
        return background
    if output_format in {"BMP", "TIFF"} and image.mode == "P":
        return image.convert("RGB")
    if output_format == "PNG" and options is not None and options.png_palette_colors is not None:
        colors = int(options.png_palette_colors)
        if not 2 <= colors <= 256:
            raise ValueError("PNG 调色板颜色数必须位于 2 到 256 之间")
        source = image if image.mode in {"RGB", "RGBA"} else image.convert("RGBA")
        try:
            return source.quantize(
                colors=colors,
                method=Image.Quantize.FASTOCTREE,
                dither=Image.Dither.FLOYDSTEINBERG,
            )
        finally:
            if source is not image:
                source.close()
    return image


def save_image(
    image: Image.Image,
    target: str | Path,
    options: ExportOptions,
    *,
    metadata: SourceMetadata | None = None,
    source_path: str | Path | None = None,
    source_fingerprint: SourceFingerprint | None = None,
    conflict_policy: ConflictPolicy = ConflictPolicy.ERROR,
    backup_on_overwrite: bool = False,
    source_passthrough: bool = False,
    timeline: EditTimeline | None = None,
    allow_animation_flatten: bool = False,
    allow_transparency_flatten: bool = False,
    before_replace: Callable[[Path, Path], object] | None = None,
) -> Path:
    output_format = normalize_format(options.output_format)
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if metadata is None:
        from light_image_editor.document import metadata_from_image

        inferred = metadata_from_image(image)
        metadata = inferred if inferred.original_format is not None else None

    source_is_current = True
    if source_path is not None and source_fingerprint is not None:
        source_is_current = source_fingerprint.matches(source_path)
        try:
            same_target = target_path.resolve() == Path(source_path).resolve()
        except OSError:
            same_target = target_path.absolute() == Path(source_path).absolute()
        if same_target and not source_is_current:
            raise SourceChangedError("源文件已被外部修改、移动或删除，不能直接覆盖。")

    if target_path.exists():
        if conflict_policy == ConflictPolicy.ERROR:
            raise FileExistsError(f"目标文件已存在：{target_path}")
        if conflict_policy == ConflictPolicy.UNIQUE:
            target_path = _unique_target(target_path)

    if (
        source_passthrough
        and source_path is not None
        and source_fingerprint is not None
        and metadata is not None
        and metadata.original_format == output_format
        and options.preserve_metadata
        and _has_no_rendering_changes(options)
        and (timeline is None or timeline.cursor == 0)
    ):
        if not source_is_current:
            raise SourceChangedError("源文件已被外部修改、移动或删除，不能按原质量复制。")
        _atomic_copy_file(
            Path(source_path),
            target_path,
            backup_on_overwrite=backup_on_overwrite,
            before_replace=before_replace,
        )
        return target_path

    if metadata is not None and metadata.is_animated and not allow_animation_flatten:
        raise AnimationFlatteningRequired("动画图片将被导出为单帧，需要明确确认。")
    if (
        metadata is not None
        and metadata.has_alpha
        and output_format in {"JPEG", "BMP"}
        and not allow_transparency_flatten
    ):
        raise TransparencyFlatteningRequired("目标格式不支持透明通道，需要明确确认使用白色背景。")

    if timeline is not None:
        from light_image_editor.renderer import render_image

        prepared = render_image(image, timeline)
    else:
        prepared = prepare_image(image, options)
    converted = convert_for_format(prepared, output_format, options)
    metadata_kwargs = (
        metadata_save_kwargs(output_format, metadata)
        if options.preserve_metadata and metadata is not None
        else {}
    )
    try:
        encoded = encode_image(converted, output_format, options, metadata_kwargs)
    finally:
        if converted is not prepared:
            converted.close()
        prepared.close()
    _atomic_replace_bytes(
        target_path,
        encoded,
        backup_on_overwrite=backup_on_overwrite,
        before_replace=before_replace,
    )
    return target_path


def metadata_save_kwargs(output_format: str, metadata: SourceMetadata | None) -> dict[str, object]:
    if metadata is None:
        return {}
    kwargs: dict[str, object] = {}
    if output_format in {"JPEG", "PNG", "WEBP", "AVIF", "TIFF"} and metadata.icc_profile:
        kwargs["icc_profile"] = metadata.icc_profile
    if output_format in {"JPEG", "PNG", "WEBP", "AVIF"} and metadata.exif:
        kwargs["exif"] = metadata.exif
    if output_format in {"JPEG", "PNG", "TIFF"} and metadata.dpi:
        kwargs["dpi"] = metadata.dpi
    if output_format in {"JPEG", "WEBP", "AVIF"} and metadata.xmp:
        kwargs["xmp"] = metadata.xmp
    if output_format == "PNG" and metadata.xmp:
        png_info = PngImagePlugin.PngInfo()
        png_info.add_itxt("XML:com.adobe.xmp", metadata.xmp.decode("utf-8", errors="replace"))
        kwargs["pnginfo"] = png_info
    return kwargs


def _unique_target(path: Path) -> Path:
    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"无法为目标生成不冲突的文件名：{path}")


def _backup_target(path: Path) -> Path:
    candidate = path.with_name(path.name + ".bak")
    if not candidate.exists():
        return candidate
    return _unique_target(candidate)


def _atomic_replace_bytes(
    target: Path,
    data: bytes,
    *,
    backup_on_overwrite: bool,
    before_replace: Callable[[Path, Path], object] | None,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with open(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if before_replace is not None:
            before_replace(temporary, target)
        if target.exists() and backup_on_overwrite:
            shutil.copy2(target, _backup_target(target))
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _has_no_rendering_changes(options: ExportOptions) -> bool:
    return (
        options.edit_state == EditState()
        and options.crop_box is None
        and options.max_width is None
        and options.max_height is None
        and options.exact_width is None
        and options.exact_height is None
        and options.target_size_kb is None
        and not options.webp_lossless
        and options.png_palette_colors is None
        and options.tiff_compression == "raw"
        and not options.watermark_text.strip()
    )


def _atomic_copy_file(
    source: Path,
    target: Path,
    *,
    backup_on_overwrite: bool,
    before_replace: Callable[[Path, Path], object] | None,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if before_replace is not None:
            before_replace(temporary, target)
        if target.exists() and backup_on_overwrite:
            shutil.copy2(target, _backup_target(target))
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def iter_image_files(paths: Iterable[str | Path]) -> list[Path]:
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(p for p in path.rglob("*") if p.suffix.lower() in SUPPORTED_INPUTS)
        elif path.suffix.lower() in SUPPORTED_INPUTS:
            files.append(path)
    return sorted(set(files), key=natural_sort_key)
