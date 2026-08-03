from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

SUPPORTED_INPUTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}

SUPPORTED_FORMATS = ("JPEG", "PNG", "WEBP", "BMP", "TIFF")
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


def open_image(path: str | Path) -> Image.Image:
    image = Image.open(path)
    return ImageOps.exif_transpose(image)


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


def natural_sort_key(path: str | Path) -> tuple[object, ...]:
    name = Path(path).name.lower()
    parts: list[object] = []
    for part in _NATURAL_PARTS.split(name):
        if part.isdigit():
            parts.append(int(part))
        elif part:
            parts.append(part)
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


def estimate_save_size(image: Image.Image, options: ExportOptions) -> int:
    result = prepare_image(image, options)
    output_format = normalize_format(options.output_format)
    result = convert_for_format(result, output_format)
    return len(encode_image(result, output_format, options))


def encode_image(image: Image.Image, output_format: str, options: ExportOptions) -> bytes:
    if options.target_size_kb and output_format in {"JPEG", "WEBP"}:
        return encode_to_target_size(
            image, output_format, options.quality, options.target_size_kb * 1024
        )
    buffer = BytesIO()
    image.save(buffer, output_format, **save_kwargs_for(output_format, options.quality))
    return buffer.getvalue()


def encode_to_target_size(
    image: Image.Image, output_format: str, max_quality: int, target_bytes: int
) -> bytes:
    low = 1
    high = max(1, min(100, int(max_quality)))
    best_under: bytes | None = None
    best_over: bytes | None = None

    while low <= high:
        quality = (low + high) // 2
        buffer = BytesIO()
        image.save(buffer, output_format, **save_kwargs_for(output_format, quality))
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
    image.save(buffer, output_format, **save_kwargs_for(output_format, 1))
    return buffer.getvalue()


def save_kwargs_for(output_format: str, quality: int) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    if output_format in {"JPEG", "WEBP"}:
        kwargs["quality"] = max(1, min(100, int(quality)))
        kwargs["optimize"] = True
    if output_format == "PNG":
        kwargs["optimize"] = True
        kwargs["compress_level"] = 9
    return kwargs


def convert_for_format(image: Image.Image, output_format: str) -> Image.Image:
    if output_format == "JPEG" and image.mode in {"RGBA", "LA", "P"}:
        rgba = image.convert("RGBA") if image.mode == "P" else image
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A") if "A" in rgba.getbands() else None)
        return background
    if output_format in {"BMP", "TIFF"} and image.mode == "P":
        return image.convert("RGB")
    return image


def save_image(image: Image.Image, target: str | Path, options: ExportOptions) -> Path:
    output_format = normalize_format(options.output_format)
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    result = prepare_image(image, options)
    result = convert_for_format(result, output_format)
    target_path.write_bytes(encode_image(result, output_format, options))
    return target_path


def iter_image_files(paths: Iterable[str | Path]) -> list[Path]:
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(p for p in path.rglob("*") if p.suffix.lower() in SUPPORTED_INPUTS)
        elif path.suffix.lower() in SUPPORTED_INPUTS:
            files.append(path)
    return sorted(set(files), key=natural_sort_key)
