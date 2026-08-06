"""Offline visual font ranking for a selected text region."""

from __future__ import annotations

import math
import os
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

MAX_FONT_MATCH_PIXELS: Final = 80_000_000
FONT_FILES: Final[dict[str, str]] = {
    "微软雅黑": "msyh.ttc",
    "微软雅黑 Light": "msyhl.ttc",
    "等线": "Deng.ttf",
    "宋体": "simsun.ttc",
    "黑体": "simhei.ttf",
}


class FontMatchError(ValueError):
    """Raised when a font match request cannot be evaluated safely."""


@dataclass(frozen=True, slots=True)
class FontCandidate:
    family: str
    path: Path

    def __post_init__(self) -> None:
        family = self.family.strip()
        if not family:
            raise FontMatchError("候选字体名称不能为空")
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "path", Path(self.path).expanduser())


@dataclass(frozen=True, slots=True)
class FontMatchSuggestion:
    font_family: str
    score: float

    def __post_init__(self) -> None:
        if not self.font_family.strip():
            raise FontMatchError("字体推荐名称不能为空")
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise FontMatchError("字体推荐分数必须在 0 到 1 之间")


def installed_font_candidates(
    families: tuple[str, ...] | None = None,
    *,
    fonts_directory: str | Path | None = None,
) -> tuple[FontCandidate, ...]:
    requested = tuple(FONT_FILES) if families is None else families
    if fonts_directory is None:
        windows = Path(os.environ.get("WINDIR", "C:/Windows"))
        directory = windows / "Fonts"
    else:
        directory = Path(fonts_directory).expanduser()
    candidates: list[FontCandidate] = []
    for family in requested:
        filename = FONT_FILES.get(family)
        if filename is None:
            continue
        path = directory / filename
        if path.is_file():
            candidates.append(FontCandidate(family, path))
    return tuple(candidates)


def _validated_region(
    region: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    if not isinstance(region, tuple) or len(region) != 4:
        raise FontMatchError("字体推荐区域必须包含四个坐标")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in region
    ):
        raise FontMatchError("字体推荐区域坐标必须是有限数字")
    left, top, right, bottom = (float(value) for value in region)
    if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
        raise FontMatchError("字体推荐区域必须位于图片范围内")
    return (left, top, right, bottom)


def _pixel_region(
    region: tuple[float, float, float, float],
    size: tuple[int, int],
) -> tuple[int, int, int, int]:
    width, height = size
    left = max(0, min(width - 1, round(region[0] * width)))
    top = max(0, min(height - 1, round(region[1] * height)))
    right = max(left + 1, min(width, round(region[2] * width)))
    bottom = max(top + 1, min(height, round(region[3] * height)))
    if right - left < 8 or bottom - top < 8:
        raise FontMatchError("字体推荐区域至少需要 8 × 8 像素")
    return (left, top, right, bottom)


def _foreground_mask(region: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]]:
    gray = ImageOps.grayscale(region)
    width, height = gray.size
    border_values = [
        *(cast(int, gray.getpixel((x, 0))) for x in range(width)),
        *(cast(int, gray.getpixel((x, height - 1))) for x in range(width)),
        *(cast(int, gray.getpixel((0, y))) for y in range(1, height - 1)),
        *(cast(int, gray.getpixel((width - 1, y))) for y in range(1, height - 1)),
    ]
    background = round(statistics.median(border_values))
    background_image = Image.new("L", gray.size, background)
    difference = ImageChops.difference(gray, background_image)
    gray.close()
    background_image.close()
    try:
        extrema = cast(tuple[int, int], difference.getextrema())
        maximum = extrema[1]
        if maximum < 18:
            raise FontMatchError("框选区域没有足够清晰的文字前景")
        threshold = max(12, min(64, round(maximum * 0.18)))
        mask = difference.point(lambda value: 255 if value >= threshold else 0)
    finally:
        difference.close()
    bbox = mask.getbbox()
    if bbox is None or (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) < 16:
        mask.close()
        raise FontMatchError("框选区域没有检测到可比较的文字轮廓")
    return mask, bbox


def _render_candidate_mask(
    text: str,
    candidate: FontCandidate,
    canvas_size: tuple[int, int],
    observed_box: tuple[int, int, int, int],
) -> Image.Image:
    try:
        font = ImageFont.truetype(str(candidate.path), 128)
    except OSError as error:
        raise FontMatchError(f"无法读取候选字体：{candidate.family}") from error
    probe = Image.new("L", (16, 16), 0)
    draw = ImageDraw.Draw(probe)
    bounds = draw.textbbox((0, 0), text, font=font)
    probe.close()
    glyph_width = max(1, round(bounds[2] - bounds[0]))
    glyph_height = max(1, round(bounds[3] - bounds[1]))
    glyph = Image.new("L", (glyph_width + 8, glyph_height + 8), 0)
    ImageDraw.Draw(glyph).text(
        (4 - bounds[0], 4 - bounds[1]),
        text,
        font=font,
        fill=255,
    )
    glyph_box = glyph.getbbox()
    if glyph_box is None:
        glyph.close()
        raise FontMatchError(f"候选字体无法渲染当前文字：{candidate.family}")
    cropped = glyph.crop(glyph_box)
    glyph.close()
    observed_height = observed_box[3] - observed_box[1]
    scale = observed_height / max(1, cropped.height)
    target_width = max(1, round(cropped.width * scale))
    resized = cropped.resize((target_width, observed_height), Image.Resampling.LANCZOS)
    cropped.close()
    canvas = Image.new("L", canvas_size, 0)
    x = round((observed_box[0] + observed_box[2] - target_width) / 2)
    y = observed_box[1]
    canvas.paste(resized, (x, y))
    resized.close()
    return canvas.point(lambda value: 255 if value >= 96 else 0)


def _best_iou(observed: Image.Image, candidate: Image.Image) -> float:
    best = 0.0
    observed_count = observed.histogram()[255]
    for shift_y in range(-2, 3):
        for shift_x in range(-2, 3):
            shifted = ImageChops.offset(candidate, shift_x, shift_y)
            if shift_x > 0:
                shifted.paste(0, (0, 0, shift_x, shifted.height))
            elif shift_x < 0:
                shifted.paste(0, (shifted.width + shift_x, 0, shifted.width, shifted.height))
            if shift_y > 0:
                shifted.paste(0, (0, 0, shifted.width, shift_y))
            elif shift_y < 0:
                shifted.paste(0, (0, shifted.height + shift_y, shifted.width, shifted.height))
            intersection = ImageChops.multiply(observed, shifted)
            try:
                intersection_count = intersection.histogram()[255]
                shifted_count = shifted.histogram()[255]
            finally:
                intersection.close()
                shifted.close()
            union = observed_count + shifted_count - intersection_count
            best = max(best, intersection_count / max(1, union))
    return best


def match_fonts(
    source: str | Path,
    *,
    region: tuple[float, float, float, float],
    text: str,
    rotation: float = 0.0,
    candidates: tuple[FontCandidate, ...] | None = None,
    maximum_results: int = 3,
) -> tuple[FontMatchSuggestion, ...]:
    """Rank installed candidate fonts against real pixels in a selected region."""

    normalized = _validated_region(region)
    normalized_text = text.strip()
    if not normalized_text or len(normalized_text) > 200:
        raise FontMatchError("字体推荐文字必须包含 1 到 200 个字符")
    if (
        isinstance(rotation, bool)
        or not isinstance(rotation, (int, float))
        or not math.isfinite(float(rotation))
        or not -180 <= float(rotation) <= 180
    ):
        raise FontMatchError("字体推荐旋转角度必须在 -180 到 180 度之间")
    if isinstance(maximum_results, bool) or not isinstance(maximum_results, int):
        raise FontMatchError("字体推荐数量必须是整数")
    if not 1 <= maximum_results <= 10:
        raise FontMatchError("字体推荐数量必须在 1 到 10 之间")
    available = installed_font_candidates() if candidates is None else candidates
    readable = tuple(candidate for candidate in available if candidate.path.is_file())
    if not readable:
        raise FontMatchError("本机没有可用于比较的候选中文字体")
    path = Path(source).expanduser().resolve(strict=True)
    try:
        with Image.open(path) as opened:
            if opened.width * opened.height > MAX_FONT_MATCH_PIXELS:
                raise FontMatchError("图片超过字体推荐像素安全上限")
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            try:
                box = _pixel_region(normalized, oriented.size)
                selected = oriented.crop(box).convert("RGB")
            finally:
                if oriented is not opened:
                    oriented.close()
    except FontMatchError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise FontMatchError("无法安全读取字体推荐图片") from error
    if abs(float(rotation)) >= 0.05:
        corner_colors = tuple(
            cast(tuple[int, int, int], selected.getpixel(point))
            for point in (
                (0, 0),
                (selected.width - 1, 0),
                (0, selected.height - 1),
                (selected.width - 1, selected.height - 1),
            )
        )
        fill_color = tuple(
            round(statistics.median(color[channel] for color in corner_colors))
            for channel in range(3)
        )
        deskewed = selected.rotate(
            -float(rotation),
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor=fill_color,
        )
        selected.close()
        selected = deskewed
    try:
        observed, observed_box = _foreground_mask(selected)
    finally:
        selected.close()
    try:
        suggestions: list[FontMatchSuggestion] = []
        for candidate in readable:
            try:
                rendered = _render_candidate_mask(
                    normalized_text,
                    candidate,
                    observed.size,
                    observed_box,
                )
            except FontMatchError:
                continue
            try:
                score = _best_iou(observed, rendered)
            finally:
                rendered.close()
            suggestions.append(FontMatchSuggestion(candidate.family, round(score, 6)))
    finally:
        observed.close()
    suggestions.sort(key=lambda suggestion: (-suggestion.score, suggestion.font_family))
    if not suggestions:
        raise FontMatchError("候选字体均无法渲染当前文字")
    return tuple(suggestions[:maximum_results])


__all__ = [
    "FONT_FILES",
    "FontCandidate",
    "FontMatchError",
    "FontMatchSuggestion",
    "installed_font_candidates",
    "match_fonts",
]
