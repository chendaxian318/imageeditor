"""Deterministic offline suggestions for geometric and controlled styled text."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from itertools import combinations
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from ..domain.perspective_quad import NormalizedQuad, validate_normalized_quad

MAX_DETECTION_PIXELS = 80_000_000
MAX_ANALYSIS_SIDE = 1_200
COARSE_DESKEW_ANGLES = (
    0.0,
    -10.0,
    10.0,
    -20.0,
    20.0,
    -30.0,
    30.0,
    -40.0,
    40.0,
    -50.0,
    50.0,
    -60.0,
    60.0,
    -70.0,
    70.0,
    -80.0,
    80.0,
    -90.0,
    90.0,
)
REFINEMENT_RADIUS = 5
MAX_REFINEMENT_SEEDS = 4
TextVisualEffect = Literal["plain", "outline", "shadow", "gradient", "glow"]
_HEX_COLOR = re.compile(r"#[0-9a-f]{6}")


@dataclass(frozen=True, slots=True)
class TextRegionSuggestion:
    region: tuple[float, float, float, float]
    confidence: float
    rotation: float = 0.0
    perspective_quad: NormalizedQuad | None = None
    curve: float = 0.0
    visual_effect: TextVisualEffect = "plain"
    estimated_fill_color: str | None = None
    estimated_effect_color: str | None = None
    estimated_effect_size: int | None = None

    def __post_init__(self) -> None:
        left, top, right, bottom = self.region
        if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
            raise ValueError("文字建议区域必须位于归一化画布范围内")
        if not 0 <= self.confidence <= 1:
            raise ValueError("文字建议置信度必须在 0 到 1 之间")
        if (
            isinstance(self.rotation, bool)
            or not isinstance(self.rotation, (int, float))
            or not math.isfinite(float(self.rotation))
            or not -90 <= float(self.rotation) <= 90
        ):
            raise ValueError("文字建议旋转角度必须位于 -90 到 90 度")
        object.__setattr__(self, "rotation", float(self.rotation))
        if (
            isinstance(self.curve, bool)
            or not isinstance(self.curve, (int, float))
            or not math.isfinite(float(self.curve))
            or not -1 <= float(self.curve) <= 1
        ):
            raise ValueError("文字建议弧度必须位于 -100% 到 100%")
        object.__setattr__(self, "curve", float(self.curve))
        if self.perspective_quad is not None and abs(self.curve) > 1e-9:
            raise ValueError("自动文字建议不能同时包含透视四角和弧度")
        if self.perspective_quad is not None:
            object.__setattr__(
                self,
                "perspective_quad",
                validate_normalized_quad(self.perspective_quad, region=self.region),
            )
        if self.visual_effect not in {"plain", "outline", "shadow", "gradient", "glow"}:
            raise ValueError("文字建议视觉字效无效")
        for field_name in ("estimated_fill_color", "estimated_effect_color"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, str) or _HEX_COLOR.fullmatch(value.casefold()) is None:
                raise ValueError("文字建议估计颜色必须使用 #RRGGBB 格式")
            object.__setattr__(self, field_name, value.casefold())
        if self.estimated_effect_size is not None and (
            isinstance(self.estimated_effect_size, bool)
            or not isinstance(self.estimated_effect_size, int)
            or not 1 <= self.estimated_effect_size <= 64
        ):
            raise ValueError("文字建议估计字效强度必须是 1 到 64 的整数")


@dataclass(frozen=True, slots=True)
class _TextEffectEstimate:
    visual_effect: TextVisualEffect
    fill_color: str | None = None
    effect_color: str | None = None
    effect_size: int | None = None


@dataclass(frozen=True, slots=True)
class _PixelSuggestion:
    box: tuple[int, int, int, int]
    confidence: float
    perspective_quad: tuple[float, float, float, float, float, float, float, float] | None = None
    curve: float = 0.0


def detect_text_regions(
    source: str | Path,
    *,
    max_suggestions: int = 5,
) -> tuple[TextRegionSuggestion, ...]:
    """Return likely text lines, including a refined visual rotation angle.

    Detection is intentionally local and deterministic. A bounded 10-degree sweep
    selects several distinct orientations, then refines each at one-degree steps.
    It is aimed at ordinary printed text between -90 and +90 degrees; exact vertical
    text keeps both reading-direction candidates because pixels alone cannot
    reliably distinguish +90 from -90. Controlled single-plane keystone perspective
    is reported as a four-corner region. It is not an OCR engine. Controlled
    outline, offset-shadow, linear-gradient and diffuse-glow printed text may be conservatively
    labelled with bounded color and effect-size estimates for user correction.
    Controlled single-line curved printed text is reported
    only when a quadratic baseline has broad horizontal support and a low residual.
    Spatially separated controlled perspective planes are kept as independent
    candidates and ordered top-to-bottom, left-to-right after confidence filtering.
    Two partially overlapping perspective lines can also be separated when their
    opaque glyph cores have clearly different chromatic directions from the local
    background; same-colour or heavily occluded planes remain outside this detector.
    """

    if isinstance(max_suggestions, bool) or not 1 <= max_suggestions <= 20:
        raise ValueError("文字区域建议数量必须在 1 到 20 之间")
    path = Path(source).expanduser().resolve(strict=True)
    with Image.open(path) as opened:
        source_format = (opened.format or "").upper()
        width, height = opened.size
        if width < 1 or height < 1 or width * height > MAX_DETECTION_PIXELS:
            raise ValueError("图片尺寸超过离线文字检测安全上限")
        opened.load()
        oriented = ImageOps.exif_transpose(opened)
        try:
            grayscale = oriented.convert("L")
        finally:
            if oriented is not opened:
                oriented.close()
    try:
        grayscale.thumbnail(
            (MAX_ANALYSIS_SIDE, MAX_ANALYSIS_SIDE),
            Image.Resampling.LANCZOS,
        )
        base_width, base_height = grayscale.size
        if base_width < 8 or base_height < 8:
            return ()
        grayscale_pixels = np.asarray(grayscale, dtype=np.int16)
        background = int(np.median(grayscale_pixels))
        photo_like_edges = _has_photo_like_edge_field(grayscale_pixels) or (
            source_format == "JPEG" and _has_compressed_photo_field(grayscale_pixels)
        )
        if photo_like_edges and source_format == "JPEG":
            photo_color_suggestions = _detect_photo_like_high_contrast_regions(
                path,
                analysis_size=(base_width, base_height),
            )
            trusted_photo_regions = tuple(item.region for item in photo_color_suggestions)
        elif photo_like_edges:
            photo_color_suggestions = _detect_photo_like_solid_color_regions(
                path,
                analysis_size=(base_width, base_height),
            )
            trusted_photo_regions = ()
        else:
            photo_color_suggestions = ()
            trusted_photo_regions = ()
        suggestions: list[TextRegionSuggestion] = list(photo_color_suggestions)
        angle_scores: dict[float, float] = {}
        perspective_counts: dict[float, int] = {}
        color_separated_overlap = False
        separated_plane_ids: set[int] = set()
        if not photo_color_suggestions:
            for deskew_angle in COARSE_DESKEW_ANGLES:
                detected = _detect_at_angle(
                    grayscale,
                    background=background,
                    source_size=(base_width, base_height),
                    deskew_angle=deskew_angle,
                )
                if (
                    deskew_angle == 0
                    and source_format != "JPEG"
                    and _may_contain_overlapping_planes(detected)
                ):
                    separated = _detect_color_separated_overlapping_planes(
                        path,
                        analysis_size=(base_width, base_height),
                    )
                    if len(separated) >= 2:
                        # A white/flat poster containing two overlapping chromatic
                        # glyph planes can look edge-dense enough to trip the photo
                        # heuristic. Two independently fitted colour-axis quads are
                        # stronger evidence than that global density signal.
                        photo_like_edges = False
                        color_separated_overlap = True
                        separated_plane_ids = {id(item) for item in separated}
                        detected = separated + tuple(
                            item
                            for item in detected
                            if all(
                                _intersection_over_smaller(item.region, plane.region) < 0.45
                                for plane in separated
                            )
                        )
                suggestions.extend(detected)
                if detected:
                    angle_scores[deskew_angle] = max(item.confidence for item in detected)
                    perspective_counts[deskew_angle] = sum(
                        item.perspective_quad is not None for item in detected
                    )
                    if (
                        deskew_angle == 0
                        and perspective_counts[deskew_angle] >= 1
                        and len(detected) >= 2
                        and angle_scores[deskew_angle] >= 0.90
                    ):
                        # Two separated high-confidence regions, with at least
                        # one perspective quad, establish the horizontal seed.
                        # Local 1-degree refinement can recover the second quad;
                        # wider 10-degree sweeps only rediscover the same planes.
                        break
            refinement_scores = {} if color_separated_overlap else angle_scores
            multi_plane_angles = [
                angle
                for angle, count in perspective_counts.items()
                if count >= 2 and angle_scores[angle] >= 0.90
            ]
            if multi_plane_angles and not color_separated_overlap:
                dominant_angle = max(
                    multi_plane_angles,
                    key=lambda angle: angle_scores[angle],
                )
                refinement_scores = {dominant_angle: angle_scores[dominant_angle]}
            for deskew_angle in _refinement_angles(refinement_scores):
                suggestions.extend(
                    _detect_at_angle(
                        grayscale,
                        background=background,
                        source_size=(base_width, base_height),
                        deskew_angle=deskew_angle,
                    )
                )
    finally:
        grayscale.close()

    suggestions.sort(
        key=lambda item: (
            -_suggestion_ranking_score(item, photo_like_edges=photo_like_edges),
            abs(item.rotation),
            item.region[1],
            item.region[0],
        )
    )
    selected: list[TextRegionSuggestion] = []
    for suggestion in suggestions:
        if any(
            not (id(suggestion) in separated_plane_ids and id(item) in separated_plane_ids)
            and (
                _intersection_over_union(suggestion.region, item.region) >= 0.38
                or _intersection_over_smaller(suggestion.region, item.region) >= 0.75
            )
            and abs(suggestion.rotation - item.rotation) <= 20
            for item in selected
        ):
            continue
        selected.append(suggestion)
        selection_limit = max(max_suggestions, 12) if photo_like_edges else max_suggestions
        if len(selected) >= selection_limit:
            break
    ordered = _order_perspective_planes(selected)
    labelled = _label_visual_effects(
        path,
        ordered,
        analysis_size=(base_width, base_height),
    )
    if photo_like_edges:
        labelled = _filter_photo_like_suggestions(
            path,
            labelled,
            analysis_size=(base_width, base_height),
            trusted_regions=trusted_photo_regions,
        )
        labelled = _order_photo_like_same_color_lines(labelled)
    return labelled[:max_suggestions]


def _may_contain_overlapping_planes(
    suggestions: tuple[TextRegionSuggestion, ...],
) -> bool:
    return any(
        item.confidence >= 0.80
        and item.region[2] - item.region[0] >= 0.55
        and item.region[3] - item.region[1] >= 0.14
        for item in suggestions
    )


def _detect_color_separated_overlapping_planes(
    path: Path,
    *,
    analysis_size: tuple[int, int],
) -> tuple[TextRegionSuggestion, ...]:
    """Recover two overlapping keystone lines from distinct opaque colour axes.

    Anti-aliased pixels from one glyph colour form a ray toward the background in
    RGB space. Grouping those rays instead of raw grayscale tones keeps differently
    coloured planes separate even where one line overwrites a small part of the
    other. The path is deliberately conservative: both layers must independently
    fit a keystone and their padded regions must overlap.
    """

    with Image.open(path) as opened:
        opened.load()
        oriented = ImageOps.exif_transpose(opened)
        try:
            color = oriented.convert("RGB")
        finally:
            if oriented is not opened:
                oriented.close()
    try:
        color.thumbnail(analysis_size, Image.Resampling.BOX)
        pixels = np.asarray(color, dtype=np.float64)
    finally:
        color.close()
    if pixels.shape[:2] != (analysis_size[1], analysis_size[0]):
        return ()

    height, width = pixels.shape[:2]
    pixel_count = height * width
    background = np.median(pixels.reshape(-1, 3), axis=0)
    vectors = pixels - background
    magnitudes = np.linalg.norm(vectors, axis=2)
    chroma = np.ptp(pixels, axis=2)
    core_mask = (magnitudes >= 48.0) & (chroma >= 18.0)
    if np.count_nonzero(core_mask) < max(192, round(pixel_count * 0.003)):
        return ()

    quantized = np.floor_divide(pixels.astype(np.int16), 12)
    packed = quantized[:, :, 0] * 484 + quantized[:, :, 1] * 22 + quantized[:, :, 2]
    bucket_values, bucket_counts = np.unique(packed[core_mask], return_counts=True)
    minimum_bucket_count = max(48, round(pixel_count * 0.00045))
    directions: list[tuple[np.ndarray, np.ndarray]] = []
    for index in np.argsort(bucket_counts)[::-1]:
        if int(bucket_counts[index]) < minimum_bucket_count:
            break
        bucket_mask = core_mask & (packed == bucket_values[index])
        center = np.median(pixels[bucket_mask], axis=0)
        direction = center - background
        distance = float(np.linalg.norm(direction))
        if distance < 48.0:
            continue
        unit = direction / distance
        if any(float(np.dot(unit, existing[0])) >= 0.998 for existing in directions):
            continue
        directions.append((unit, center))
        if len(directions) >= 4:
            break
    if len(directions) < 2:
        return ()

    direction_units = np.stack([item[0] for item in directions], axis=0)
    cosine_scores = np.divide(
        np.einsum("hwc,dc->hwd", vectors, direction_units),
        magnitudes[:, :, None],
        out=np.zeros((height, width, len(directions)), dtype=np.float64),
        where=magnitudes[:, :, None] > 0,
    )
    best_direction = np.argmax(cosine_scores, axis=2)
    best_cosine = np.max(cosine_scores, axis=2)
    second_cosine = np.partition(cosine_scores, -2, axis=2)[:, :, -2]
    unambiguous_direction = (best_cosine - second_cosine) >= 0.0006

    minimum_layer_pixels = max(96, round(pixel_count * 0.0015))
    candidates: list[TextRegionSuggestion] = []
    for direction_index, (unit, center) in enumerate(directions):
        layer_mask = (
            (magnitudes >= 24.0)
            & (best_direction == direction_index)
            & (best_cosine >= 0.985)
            & unambiguous_direction
        )
        if np.count_nonzero(layer_mask) < minimum_layer_pixels:
            continue
        projection = np.sum(vectors * unit, axis=2)
        strongest_threshold = float(np.percentile(projection[layer_mask], 90))
        strongest_mask = layer_mask & (projection >= strongest_threshold)
        fill_center = np.median(pixels[strongest_mask], axis=0)
        y_indexes, x_indexes = np.nonzero(layer_mask)
        left = int(np.min(x_indexes))
        top = int(np.min(y_indexes))
        right = int(np.max(x_indexes)) + 1
        bottom = int(np.max(y_indexes)) + 1
        run_width = right - left
        run_height = bottom - top
        if run_width < 48 or run_height < 10 or run_width / run_height < 1.8:
            continue
        density = float(np.count_nonzero(layer_mask[top:bottom, left:right])) / max(
            1,
            run_width * run_height,
        )
        if density < 0.045:
            continue
        pad_x = max(2, round(run_width * 0.025))
        pad_y = max(2, round(run_height * 0.16))
        padded_box = (
            max(0, left - pad_x),
            max(0, top - pad_y),
            min(width, right + pad_x),
            min(height, bottom + pad_y),
        )
        pixel_quad = _estimate_perspective_quad(
            layer_mask,
            content_box=(left, top, right, bottom),
            padded_box=padded_box,
            minimum_side_ratio=0.30,
            require_opposite_edge_slopes=False,
        )
        if pixel_quad is None:
            continue
        region = (
            padded_box[0] / width,
            padded_box[1] / height,
            padded_box[2] / width,
            padded_box[3] / height,
        )
        normalized_quad = tuple(
            value / (width if index % 2 == 0 else height) for index, value in enumerate(pixel_quad)
        )
        try:
            quad = validate_normalized_quad(normalized_quad, region=region)
        except ValueError:
            continue
        area_ratio = run_width * run_height / pixel_count
        confidence = float(np.clip(0.72 + density * 0.50 + min(0.10, area_ratio * 0.50), 0, 0.97))
        candidates.append(
            TextRegionSuggestion(
                region=region,
                confidence=confidence,
                perspective_quad=quad,
                estimated_fill_color=_rgb_hex(fill_center),
            )
        )

    compatible_groups: list[tuple[TextRegionSuggestion, ...]] = []
    for group_size in range(2, min(4, len(candidates)) + 1):
        for group in combinations(candidates, group_size):
            pairs = tuple(combinations(group, 2))
            if all(
                0.08 <= _intersection_over_union(first.region, second.region) <= 0.65
                and first.estimated_fill_color is not None
                and second.estimated_fill_color is not None
                and _hex_color_distance(first.estimated_fill_color, second.estimated_fill_color)
                >= 12
                for first, second in pairs
            ):
                compatible_groups.append(group)
    if not compatible_groups:
        return ()
    best_group = max(
        compatible_groups,
        key=lambda group: (
            len(group),
            sum(item.confidence for item in group),
            sum(
                _intersection_over_union(first.region, second.region)
                for first, second in combinations(group, 2)
            ),
        ),
    )
    return tuple(
        sorted(
            best_group,
            key=lambda item: (item.region[1], item.region[0], -item.confidence),
        )
    )


def _suggestion_ranking_score(
    suggestion: TextRegionSuggestion,
    *,
    photo_like_edges: bool,
) -> float:
    if not photo_like_edges:
        return suggestion.confidence
    width = suggestion.region[2] - suggestion.region[0]
    height = max(1e-6, suggestion.region[3] - suggestion.region[1])
    aspect_bonus = min(0.04, width / height * 0.015)
    height_penalty = height * 0.11
    return suggestion.confidence + aspect_bonus - height_penalty


def _label_visual_effects(
    path: Path,
    suggestions: tuple[TextRegionSuggestion, ...],
    *,
    analysis_size: tuple[int, int],
) -> tuple[TextRegionSuggestion, ...]:
    if not suggestions:
        return suggestions
    with Image.open(path) as opened:
        source_format = (opened.format or "").upper()
        opened.load()
        oriented = ImageOps.exif_transpose(opened)
        try:
            color = oriented.convert("RGB")
        finally:
            if oriented is not opened:
                oriented.close()
    try:
        original_size = color.size
        # Area sampling keeps controlled fill/effect layers discrete enough for
        # bounded color and pixel-strength estimates. Lanczos ringing can make a
        # light fill appear wider than its outline after downsampling.
        color.thumbnail(analysis_size, Image.Resampling.BOX)
        pixel_scale = max(
            original_size[0] / max(1, color.width),
            original_size[1] / max(1, color.height),
        )
        labelled: list[TextRegionSuggestion] = []
        for suggestion in suggestions:
            estimate = _analyze_text_visual_effect(
                color,
                suggestion.region,
                rotation=suggestion.rotation,
                pixel_scale=pixel_scale,
            )
            compressed_glow = (
                _estimate_compressed_glow(
                    color,
                    suggestion.region,
                    fill_color=suggestion.estimated_fill_color,
                    pixel_scale=pixel_scale,
                )
                if source_format == "JPEG" and suggestion.estimated_fill_color is not None
                else None
            )
            if compressed_glow is not None:
                estimate = compressed_glow
            if suggestion.estimated_fill_color is not None and estimate.visual_effect != "glow":
                # Solid-colour proposals already carry a color sampled from the
                # full horizontal glyph band.  Photo texture inside their padded
                # box can otherwise be mistaken for a shadow or gradient and can
                # replace the actual text color with a background cluster.
                estimate = _TextEffectEstimate(
                    "plain",
                    fill_color=suggestion.estimated_fill_color,
                )
            elif (
                suggestion.estimated_fill_color is not None
                and estimate.visual_effect == "glow"
                and estimate.effect_size is not None
            ):
                if compressed_glow is not None:
                    pass
                elif (
                    estimate.fill_color is not None
                    and _hex_color_distance(
                        suggestion.estimated_fill_color,
                        estimate.fill_color,
                    )
                    > 24
                ):
                    estimate = _TextEffectEstimate(
                        "plain",
                        fill_color=suggestion.estimated_fill_color,
                    )
                else:
                    # The proposal is fitted to the opaque glyph core, so its local
                    # distance model overstates the halo radius relative to the
                    # broader grayscale proposal. Apply a bounded scale correction.
                    effect_size = estimate.effect_size
                    assert effect_size is not None
                    estimate = replace(
                        estimate,
                        effect_size=max(1, round(effect_size * 0.7)),
                    )
            labelled.append(
                replace(
                    suggestion,
                    visual_effect=estimate.visual_effect,
                    estimated_fill_color=estimate.fill_color,
                    estimated_effect_color=estimate.effect_color,
                    estimated_effect_size=estimate.effect_size,
                )
            )
        return tuple(labelled)
    finally:
        color.close()


def _filter_photo_like_suggestions(
    path: Path,
    suggestions: tuple[TextRegionSuggestion, ...],
    *,
    analysis_size: tuple[int, int],
    trusted_regions: tuple[tuple[float, float, float, float], ...] = (),
) -> tuple[TextRegionSuggestion, ...]:
    if not suggestions:
        return suggestions
    with Image.open(path) as opened:
        source_format = (opened.format or "").upper()
        opened.load()
        oriented = ImageOps.exif_transpose(opened)
        try:
            color = oriented.convert("RGB")
        finally:
            if oriented is not opened:
                oriented.close()
    try:
        color.thumbnail(analysis_size, Image.Resampling.BOX)
        pixels = np.asarray(color, dtype=np.int16)
    finally:
        color.close()
    height, width, _channels = pixels.shape
    median_color = np.median(pixels.reshape(-1, 3), axis=0)
    minimum_solid_pixels = max(80, round(width * height * 0.008))
    filtered: list[TextRegionSuggestion] = []
    for suggestion in suggestions:
        if suggestion.region in trusted_regions:
            filtered.append(suggestion)
            continue
        fill_color = suggestion.estimated_fill_color
        if fill_color is None:
            continue
        fill = _parse_hex_rgb(fill_color).astype(np.int16)
        if source_format == "JPEG" and float(np.max(np.abs(fill - median_color))) < 55:
            continue
        left = max(0, min(width - 1, round(suggestion.region[0] * width)))
        top = max(0, min(height - 1, round(suggestion.region[1] * height)))
        right = max(left + 1, min(width, round(suggestion.region[2] * width)))
        bottom = max(top + 1, min(height, round(suggestion.region[3] * height)))
        crop = pixels[top:bottom, left:right]
        color_distance = np.max(np.abs(crop - fill), axis=2)
        solid = color_distance <= 8
        solid_count = int(np.count_nonzero(solid))
        solid_fraction = solid_count / max(1, solid.size)
        core_count = int(np.count_nonzero(color_distance <= 1))
        concentrated_text_core = bool(
            core_count >= max(60, round(width * height * 0.002))
            and core_count / max(1, solid_count) >= 0.45
        )
        required_solid_pixels = minimum_solid_pixels
        if concentrated_text_core and abs(suggestion.rotation) <= 1:
            required_solid_pixels = min(
                required_solid_pixels,
                max(80, round(solid.size * 0.075)),
            )
        solid_y, solid_x = np.nonzero(solid)
        horizontal_text_band = bool(
            solid_x.size >= 2
            and float(np.ptp(solid_x)) >= crop.shape[1] * 0.35
            and float(np.ptp(solid_y)) <= crop.shape[0] * 0.60
        )
        if solid_count < required_solid_pixels or (
            solid_fraction < 0.075 and not horizontal_text_band
        ):
            continue
        filtered.append(suggestion)
    return tuple(filtered)


def _order_photo_like_same_color_lines(
    suggestions: tuple[TextRegionSuggestion, ...],
) -> tuple[TextRegionSuggestion, ...]:
    if len(suggestions) < 2:
        return suggestions
    anchor = suggestions[0]
    if anchor.estimated_fill_color is None:
        return suggestions
    matching_indexes = [
        index
        for index, item in enumerate(suggestions)
        if abs(item.rotation) <= 1
        and item.visual_effect == anchor.visual_effect
        and item.estimated_fill_color is not None
        and _hex_color_distance(item.estimated_fill_color, anchor.estimated_fill_color) <= 16
    ]
    if len(matching_indexes) < 2:
        return suggestions
    ordered_lines = sorted(
        (suggestions[index] for index in matching_indexes),
        key=lambda item: (item.region[1], item.region[0]),
    )
    result = list(suggestions)
    for index, line in zip(matching_indexes, ordered_lines, strict=True):
        result[index] = line
    return tuple(result)


def _detect_photo_like_solid_color_regions(
    path: Path,
    *,
    analysis_size: tuple[int, int],
) -> tuple[TextRegionSuggestion, ...]:
    with Image.open(path) as opened:
        opened.load()
        oriented = ImageOps.exif_transpose(opened)
        try:
            color = oriented.convert("RGB")
        finally:
            if oriented is not opened:
                oriented.close()
    try:
        color.thumbnail(analysis_size, Image.Resampling.BOX)
        pixels = np.asarray(color, dtype=np.int16)
    finally:
        color.close()
    height, width, _channels = pixels.shape
    image_area = width * height
    quantized = np.clip(pixels, 0, 255).astype(np.uint8) // 16
    keys = (
        quantized[:, :, 0].astype(np.int32) * 256
        + quantized[:, :, 1].astype(np.int32) * 16
        + quantized[:, :, 2].astype(np.int32)
    )
    unique_keys, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)
    minimum_cluster = max(80, round(image_area * 0.0035))
    maximum_cluster = round(image_area * 0.20)
    suggestions: list[TextRegionSuggestion] = []
    for index in order:
        cluster_count = int(counts[index])
        if cluster_count < minimum_cluster:
            break
        if cluster_count > maximum_cluster:
            continue
        member = keys == unique_keys[index]
        member_colors = pixels[member]
        median_color = np.median(member_colors, axis=0)
        member_core_count = int(
            np.count_nonzero(np.max(np.abs(member_colors - median_color), axis=1) <= 1)
        )
        if member_core_count < max(60, round(image_area * 0.002)) or (
            member_core_count / cluster_count < 0.25
        ):
            continue
        solid = np.max(np.abs(pixels - median_color), axis=2) <= 8
        solid &= member
        concentrated_core = np.max(np.abs(pixels - median_color), axis=2) <= 1
        concentrated_core &= member
        row_counts = np.count_nonzero(solid, axis=1)
        active_rows = row_counts >= max(5, round(width * 0.018))
        for top, bottom in _runs(active_rows, max_gap=2):
            run_height = bottom - top
            if run_height < 5 or run_height > height * 0.30:
                continue
            band = solid[top:bottom]
            y_indexes, x_indexes = np.nonzero(band)
            solid_count = int(x_indexes.size)
            if solid_count < max(60, round(image_area * 0.0025)):
                continue
            core_count = int(np.count_nonzero(concentrated_core[top:bottom]))
            if core_count < max(60, round(image_area * 0.002)) or (core_count / solid_count < 0.45):
                # Natural photo tones often occupy the same coarse RGB bin but
                # spread smoothly across many neighboring colors.  Rendered text
                # keeps a concentrated, nearly flat-color glyph core.
                continue
            left = int(np.min(x_indexes))
            right = int(np.max(x_indexes)) + 1
            run_width = right - left
            if run_width < width * 0.18:
                continue
            density = solid_count / max(1, run_width * run_height)
            if density < 0.08:
                continue
            pad_x = max(3, round(run_width * 0.03))
            pad_y = max(4, round(height * 0.06))
            padded_left = max(0, left - pad_x)
            padded_top = max(0, top - pad_y)
            padded_right = min(width, right + pad_x)
            padded_bottom = min(height, bottom + pad_y)
            confidence = float(
                np.clip(
                    0.90 + min(0.025, density * 0.15) + min(0.07, solid_count / image_area * 4),
                    0,
                    0.99,
                )
            )
            suggestions.append(
                TextRegionSuggestion(
                    region=(
                        padded_left / width,
                        padded_top / height,
                        padded_right / width,
                        padded_bottom / height,
                    ),
                    confidence=confidence,
                    rotation=0.0,
                    estimated_fill_color=_rgb_hex(median_color),
                )
            )
    return tuple(suggestions)


def _detect_photo_like_high_contrast_regions(
    path: Path,
    *,
    analysis_size: tuple[int, int],
) -> tuple[TextRegionSuggestion, ...]:
    """Find horizontal text cores that survived bounded JPEG compression.

    JPEG ringing destroys the exact RGB concentration used for lossless inputs,
    while large compression blocks can themselves look nearly constant.  This
    path instead requires a broad, dense horizontal band whose luminance remains
    far from the global photo tone, then estimates its fill from the strongest
    quartile of that band.
    """

    with Image.open(path) as opened:
        opened.load()
        oriented = ImageOps.exif_transpose(opened)
        try:
            color = oriented.convert("RGB")
        finally:
            if oriented is not opened:
                oriented.close()
    try:
        color.thumbnail(analysis_size, Image.Resampling.BOX)
        pixels = np.asarray(color, dtype=np.int16)
        grayscale = np.asarray(color.convert("L"), dtype=np.int16)
    finally:
        color.close()
    height, width, _channels = pixels.shape
    image_area = width * height
    background = float(np.median(grayscale))
    suggestions: list[TextRegionSuggestion] = []
    for direction in (-1, 1):
        signed_contrast = (grayscale - background) * direction
        high_contrast = signed_contrast >= 70
        row_counts = np.count_nonzero(high_contrast, axis=1)
        active_rows = row_counts >= max(5, round(width * 0.01))
        for top, bottom in _runs(active_rows, max_gap=2):
            run_height = bottom - top
            if run_height < 5 or run_height > height * 0.30:
                continue
            band = high_contrast[top:bottom]
            y_indexes, x_indexes = np.nonzero(band)
            pixel_count = int(x_indexes.size)
            if pixel_count < max(80, round(image_area * 0.0035)):
                continue
            left = int(np.min(x_indexes))
            right = int(np.max(x_indexes)) + 1
            run_width = right - left
            if run_width < width * 0.18:
                continue
            density = pixel_count / max(1, run_width * run_height)
            if density < 0.12:
                continue
            band_gray = grayscale[top:bottom]
            visible_values = band_gray[band]
            percentile = 25 if direction < 0 else 75
            core_limit = float(np.percentile(visible_values, percentile))
            if direction < 0:
                core = band & (band_gray <= core_limit)
            else:
                core = band & (band_gray >= core_limit)
            core_colors = pixels[top:bottom][core]
            if core_colors.shape[0] < max(40, round(image_area * 0.0015)):
                continue
            fill_color = _rgb_hex(np.median(core_colors, axis=0))
            pad_x = max(3, round(run_width * 0.03))
            pad_y = max(4, round(height * 0.06))
            confidence = float(
                np.clip(
                    0.92 + min(0.04, pixel_count / image_area * 2) + min(0.03, density * 0.10),
                    0,
                    0.99,
                )
            )
            suggestions.append(
                TextRegionSuggestion(
                    region=(
                        max(0, left - pad_x) / width,
                        max(0, top - pad_y) / height,
                        min(width, right + pad_x) / width,
                        min(height, bottom + pad_y) / height,
                    ),
                    confidence=confidence,
                    rotation=0.0,
                    estimated_fill_color=fill_color,
                )
            )
    return tuple(suggestions)


def _classify_text_visual_effect(
    image: Image.Image,
    region: tuple[float, float, float, float],
    *,
    rotation: float = 0.0,
) -> TextVisualEffect:
    """Conservatively classify a controlled printed-text appearance."""

    return _analyze_text_visual_effect(image, region, rotation=rotation).visual_effect


def _analyze_text_visual_effect(
    image: Image.Image,
    region: tuple[float, float, float, float],
    *,
    rotation: float = 0.0,
    pixel_scale: float = 1.0,
) -> _TextEffectEstimate:
    """Classify and conservatively estimate colors and effect size."""

    width, height = image.size
    left = max(0, min(width - 1, round(region[0] * width)))
    top = max(0, min(height - 1, round(region[1] * height)))
    right = max(left + 1, min(width, round(region[2] * width)))
    bottom = max(top + 1, min(height, round(region[3] * height)))
    pad_x = max(2, round((right - left) * 0.025))
    pad_y = max(2, round((bottom - top) * 0.025))
    left = max(0, left - pad_x)
    top = max(0, top - pad_y)
    right = min(width, right + pad_x)
    bottom = min(height, bottom + pad_y)
    crop = image.crop((left, top, right, bottom))
    if abs(rotation) >= 0.05:
        border_pixels = np.concatenate(
            (
                np.asarray(crop)[0],
                np.asarray(crop)[-1],
                np.asarray(crop)[:, 0],
                np.asarray(crop)[:, -1],
            ),
            axis=0,
        )
        fill = tuple(int(round(value)) for value in np.median(border_pixels, axis=0))
        deskewed = crop.rotate(
            -rotation,
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor=fill,
        )
        crop.close()
        crop = deskewed
    try:
        pixels = np.asarray(crop, dtype=np.int16)
    finally:
        crop.close()
    plain = _TextEffectEstimate("plain")
    if pixels.shape[0] < 8 or pixels.shape[1] < 20:
        return plain
    background_surface = _estimate_background_surface(pixels)
    analysis_background = _estimate_texture_background(pixels, background_surface)
    textured_background = analysis_background is not background_surface
    distance = np.max(np.abs(pixels - analysis_background), axis=2)
    foreground = distance >= 14
    foreground_count = int(np.count_nonzero(foreground))
    if foreground_count < max(40, round(pixels.shape[0] * pixels.shape[1] * 0.015)):
        return plain

    y_indexes, x_indexes = np.nonzero(foreground)
    background = np.median(analysis_background[foreground], axis=0)
    colors = pixels[foreground]
    surface_glow = _estimate_surface_relative_glow(
        colors,
        background_surface[foreground],
        x_indexes=x_indexes,
        y_indexes=y_indexes,
        foreground_count=foreground_count,
        image_size=(pixels.shape[1], pixels.shape[0]),
        pixel_scale=pixel_scale,
        textured_background=textured_background,
    )
    if surface_glow is not None:
        return surface_glow
    quantized = np.clip(colors, 0, 255).astype(np.uint8) // 32
    keys = (
        quantized[:, 0].astype(np.int32) * 64
        + quantized[:, 1].astype(np.int32) * 8
        + quantized[:, 2].astype(np.int32)
    )
    unique_keys, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)
    dominant_fill = None
    if order.size:
        dominant_fill = _rgb_hex(np.median(colors[keys == unique_keys[order[0]]], axis=0))
        plain = _TextEffectEstimate("plain", fill_color=dominant_fill)
        dominant_colors = colors[keys == unique_keys[order[0]]].astype(np.float64)
        dominant_median = np.median(dominant_colors, axis=0)
        dominant_solid_fraction = float(
            np.mean(np.max(np.abs(dominant_colors - dominant_median), axis=1) <= 8)
        )
        second_count = 0 if order.size < 2 else int(counts[order[1]])
        if dominant_solid_fraction >= 0.90 and int(counts[order[0]]) >= max(80, second_count * 3):
            return plain
    significant = [
        int(index) for index in order if counts[index] >= max(8, round(foreground_count * 0.025))
    ]

    core = distance[foreground] >= 28
    core_colors = colors[core]
    core_y = y_indexes[core].astype(np.float64)
    if core_colors.shape[0] >= 40 and len(significant) >= 5 and np.ptp(core_y) >= 6:
        normalized_y = (core_y - np.mean(core_y)) / max(1.0, float(np.std(core_y)))
        gradient_channel = False
        for channel in range(3):
            values = core_colors[:, channel].astype(np.float64)
            if np.std(values) < 1:
                continue
            correlation = abs(float(np.corrcoef(normalized_y, values)[0, 1]))
            slope = float(np.polyfit(core_y, values, 1)[0])
            predicted_range = abs(slope) * float(np.ptp(core_y))
            gradient_channel = gradient_channel or (correlation >= 0.30 and predicted_range >= 22)
        if gradient_channel:
            upper = core_y <= float(np.percentile(core_y, 35))
            lower = core_y >= float(np.percentile(core_y, 65))
            if np.count_nonzero(upper) >= 8 and np.count_nonzero(lower) >= 8:
                return _TextEffectEstimate(
                    "gradient",
                    fill_color=_rgb_hex(np.median(core_colors[upper], axis=0)),
                    effect_color=_rgb_hex(np.median(core_colors[lower], axis=0)),
                )
            return _TextEffectEstimate("gradient", fill_color=dominant_fill)

    if len(significant) < 2:
        return plain
    padded_foreground = np.pad(foreground.astype(np.int8), 1)
    neighbor_count = (
        padded_foreground[:-2, 1:-1]
        + padded_foreground[2:, 1:-1]
        + padded_foreground[1:-1, :-2]
        + padded_foreground[1:-1, 2:]
    )
    foreground_exposure = (4 - neighbor_count[foreground]).astype(np.float64) / 4
    cluster_metrics: list[
        tuple[int, np.ndarray, str, float, float, float, float, float, float, float]
    ] = []
    for index in significant[:3]:
        key = unique_keys[index]
        member = keys == key
        if np.count_nonzero(member) < 8:
            continue
        member_colors = colors[member].astype(np.float64)
        member_x = x_indexes[member].astype(np.float64)
        member_y = y_indexes[member].astype(np.float64)
        cluster_metrics.append(
            (
                int(counts[index]),
                np.mean(member_colors, axis=0),
                _rgb_hex(np.median(member_colors, axis=0)),
                float(np.mean(member_x)),
                float(np.mean(member_y)),
                float(np.ptp(member_x)),
                float(np.ptp(member_y)),
                float(np.mean(foreground_exposure[member])),
                float(np.max(member_x)),
                float(np.max(member_y)),
            )
        )
    if len(cluster_metrics) < 2:
        return plain
    glow = _estimate_glow_layer(
        background,
        cluster_metrics,
        image_size=(pixels.shape[1], pixels.shape[0]),
        pixel_scale=pixel_scale,
    )
    if glow is not None:
        return glow
    first, second = cluster_metrics[:2]
    color_distance = float(np.max(np.abs(first[1] - second[1])))
    minimum_cluster_count = min(first[0], second[0])
    if color_distance < 48 or minimum_cluster_count < foreground_count * 0.08:
        return plain
    if _is_interpolated_color_layer(background, first[1], second[1]):
        return plain
    centroid_dx = abs(first[3] - second[3])
    centroid_dy = abs(first[4] - second[4])
    broad_width = min(first[5], second[5]) >= pixels.shape[1] * 0.45
    span_width_delta = abs(first[5] - second[5])
    span_height_delta = abs(first[6] - second[6])
    if (
        broad_width
        and span_width_delta >= 4
        and span_height_delta >= 4
        and centroid_dy <= max(3.0, pixels.shape[0] * 0.06)
    ):
        fill_cluster, effect_cluster = sorted(
            (first, second),
            key=lambda cluster: cluster[5] * cluster[6],
        )
        outline_width = max(
            1.0,
            (effect_cluster[5] - fill_cluster[5]) / 2,
            (effect_cluster[6] - fill_cluster[6]) / 2,
        )
        return _TextEffectEstimate(
            "outline",
            fill_color=fill_cluster[2],
            effect_color=effect_cluster[2],
            effect_size=_scaled_effect_size(outline_width, pixel_scale),
        )
    if broad_width and (
        centroid_dx >= max(3.0, pixels.shape[1] * 0.025)
        or centroid_dy >= max(3.0, pixels.shape[0] * 0.08)
    ):
        fill_cluster, effect_cluster = sorted(
            (first, second),
            key=lambda cluster: cluster[3] + cluster[4],
        )
        return _TextEffectEstimate(
            "shadow",
            fill_color=fill_cluster[2],
            effect_color=effect_cluster[2],
            effect_size=_scaled_effect_size(
                max(
                    1.0,
                    effect_cluster[8] - fill_cluster[8],
                    effect_cluster[9] - fill_cluster[9],
                ),
                pixel_scale,
            ),
        )
    if broad_width and centroid_dx <= pixels.shape[1] * 0.04:
        fill_cluster, effect_cluster = sorted(
            (first, second),
            key=lambda cluster: cluster[5] * cluster[6],
        )
        outline_width = max(
            1.0,
            (effect_cluster[5] - fill_cluster[5]) / 2,
            (effect_cluster[6] - fill_cluster[6]) / 2,
        )
        return _TextEffectEstimate(
            "outline",
            fill_color=fill_cluster[2],
            effect_color=effect_cluster[2],
            effect_size=_scaled_effect_size(outline_width, pixel_scale),
        )
    return plain


def _rgb_hex(values: np.ndarray) -> str:
    channels = np.clip(np.rint(values), 0, 255).astype(np.uint8)
    return f"#{int(channels[0]):02x}{int(channels[1]):02x}{int(channels[2]):02x}"


def _estimate_background_surface(pixels: np.ndarray) -> np.ndarray:
    height, width, _channels = pixels.shape
    x_axis = np.linspace(-1.0, 1.0, width, dtype=np.float64)
    y_axis = np.linspace(-1.0, 1.0, height, dtype=np.float64)
    top_x = x_axis
    top_y = np.full(width, -1.0)
    bottom_x = x_axis
    bottom_y = np.full(width, 1.0)
    side_y = y_axis[1:-1]
    border_x = np.concatenate(
        (top_x, bottom_x, np.full(side_y.size, -1.0), np.full(side_y.size, 1.0))
    )
    border_y = np.concatenate((top_y, bottom_y, side_y, side_y))
    border_colors = np.concatenate(
        (pixels[0], pixels[-1], pixels[1:-1, 0], pixels[1:-1, -1]),
        axis=0,
    ).astype(np.float64)
    if float(np.max(np.ptp(border_colors, axis=0))) <= 6.0:
        background = np.median(border_colors, axis=0)
        return np.broadcast_to(background, (height, width, 3)).copy()
    design = _background_design(border_x, border_y)
    coefficients, *_unused = np.linalg.lstsq(design, border_colors, rcond=None)
    residual = np.max(np.abs(border_colors - design @ coefficients), axis=1)
    median_residual = float(np.median(residual))
    mad = float(np.median(np.abs(residual - median_residual)))
    cutoff = max(4.0, median_residual + 3.5 * max(1.0, 1.4826 * mad))
    inliers = residual <= cutoff
    if np.count_nonzero(inliers) >= max(24, round(border_colors.shape[0] * 0.55)):
        coefficients, *_unused = np.linalg.lstsq(
            design[inliers],
            border_colors[inliers],
            rcond=None,
        )
    grid_x, grid_y = np.meshgrid(x_axis, y_axis)
    surface = _background_design(grid_x.ravel(), grid_y.ravel()) @ coefficients
    return np.clip(surface.reshape((height, width, 3)), 0, 255)


def _background_design(x_values: np.ndarray, y_values: np.ndarray) -> np.ndarray:
    return np.column_stack(
        (
            np.ones_like(x_values),
            x_values,
            y_values,
            x_values * y_values,
            x_values * x_values,
            y_values * y_values,
        )
    )


def _estimate_texture_background(
    pixels: np.ndarray,
    global_surface: np.ndarray,
) -> np.ndarray:
    residual = np.max(np.abs(pixels - global_surface), axis=2)
    border_residual = np.concatenate(
        (residual[0], residual[-1], residual[1:-1, 0], residual[1:-1, -1])
    )
    if float(np.percentile(border_residual, 75)) < 8.0:
        return global_surface
    image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    try:
        filtered = image.filter(ImageFilter.MedianFilter(15))
        try:
            return np.asarray(filtered, dtype=np.float64).copy()
        finally:
            filtered.close()
    finally:
        image.close()


def _estimate_surface_relative_glow(
    colors: np.ndarray,
    local_backgrounds: np.ndarray,
    *,
    x_indexes: np.ndarray,
    y_indexes: np.ndarray,
    foreground_count: int,
    image_size: tuple[int, int],
    pixel_scale: float,
    textured_background: bool,
) -> _TextEffectEstimate | None:
    deltas = colors.astype(np.float64) - local_backgrounds
    magnitudes = np.max(np.abs(deltas), axis=1)
    positive_limits = np.divide(
        255.0 - local_backgrounds,
        deltas,
        out=np.full_like(deltas, np.inf),
        where=deltas > 1e-9,
    )
    negative_limits = np.divide(
        0.0 - local_backgrounds,
        deltas,
        out=np.full_like(deltas, np.inf),
        where=deltas < -1e-9,
    )
    limits = np.minimum(positive_limits, negative_limits)
    scales = np.clip(np.min(limits, axis=1), 1.0, 8.0)
    endpoints = np.clip(local_backgrounds + deltas * scales[:, None], 0, 255)
    quantized = endpoints.astype(np.uint8) // 32
    keys = (
        quantized[:, 0].astype(np.int32) * 64
        + quantized[:, 1].astype(np.int32) * 8
        + quantized[:, 2].astype(np.int32)
    )
    unique_keys, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)
    families: list[
        tuple[int, np.ndarray, str, float, float, float, float, float, float, float]
    ] = []
    minimum_count = max(12, round(foreground_count * 0.05))
    for index in order[:4]:
        if counts[index] < minimum_count:
            continue
        member = keys == unique_keys[index]
        member_colors = colors[member].astype(np.float64)
        median_color = np.median(member_colors, axis=0)
        member_magnitudes = magnitudes[member]
        member_x = x_indexes[member].astype(np.float64)
        member_y = y_indexes[member].astype(np.float64)
        families.append(
            (
                int(counts[index]),
                np.median(endpoints[member], axis=0),
                _rgb_hex(median_color),
                float(np.max(np.std(member_colors, axis=0))),
                float(np.ptp(member_x)),
                float(np.ptp(member_y)),
                float(np.mean(member_x)),
                float(np.mean(member_y)),
                float(np.percentile(member_magnitudes, 90))
                - float(np.percentile(member_magnitudes, 10)),
                float(np.mean(np.max(np.abs(member_colors - median_color), axis=1) <= 8)),
            )
        )
    if len(families) < 2:
        return None
    for first_index in range(len(families) - 1):
        for second_index in range(first_index + 1, len(families)):
            first = families[first_index]
            second = families[second_index]
            if abs(first[9] - second[9]) >= 0.15:
                fill, effect = sorted(
                    (first, second),
                    key=lambda family: family[9],
                    reverse=True,
                )
            else:
                fill, effect = sorted((first, second), key=lambda family: family[3])
            if effect[3] < max(7.0, fill[3] * 1.5):
                continue
            if effect[8] < 18:
                continue
            if effect[9] > 0.65:
                continue
            fill_rgb = _parse_hex_rgb(fill[2])
            if textured_background:
                fill_core = np.max(np.abs(colors.astype(np.float64) - fill_rgb), axis=1) <= 8
                merged_effect = np.max(np.abs(endpoints - effect[1]), axis=1) <= 48
                merged_effect &= ~fill_core
                if np.count_nonzero(fill_core) < 12 or np.count_nonzero(merged_effect) < 12:
                    continue
                fill_x = x_indexes[fill_core].astype(np.float64)
                fill_y = y_indexes[fill_core].astype(np.float64)
                effect_x = x_indexes[merged_effect].astype(np.float64)
                effect_y = y_indexes[merged_effect].astype(np.float64)
                fill_width = float(np.ptp(fill_x))
                fill_height = float(np.ptp(fill_y))
                effect_width = float(np.ptp(effect_x))
                effect_height = float(np.ptp(effect_y))
                if effect_height < fill_height + 4:
                    continue
                if effect_width < fill_width - max(4.0, image_size[0] * 0.05):
                    continue
                if min(effect_width, fill_width) < image_size[0] * 0.45:
                    continue
                if abs(float(np.mean(effect_x)) - float(np.mean(fill_x))) > max(
                    4.0, image_size[0] * 0.06
                ):
                    continue
                if abs(float(np.mean(effect_y)) - float(np.mean(fill_y))) > max(
                    4.0, image_size[1] * 0.08
                ):
                    continue
                effect_endpoint = np.median(endpoints[merged_effect], axis=0)
                radius = (
                    max(
                        (effect_width - fill_width) / 2,
                        (effect_height - fill_height) / 2,
                    )
                    * 1.5
                )
            else:
                if effect[4] < fill[4] + 6 or effect[5] < fill[5] + 6:
                    continue
                if min(effect[4], fill[4]) < image_size[0] * 0.45:
                    continue
                if abs(effect[6] - fill[6]) > max(4.0, image_size[0] * 0.06):
                    continue
                if abs(effect[7] - fill[7]) > max(4.0, image_size[1] * 0.08):
                    continue
                effect_endpoint = effect[1]
                radius = max(
                    (effect[4] - fill[4]) / 2,
                    (effect[5] - fill[5]) / 2,
                )
            if float(np.max(np.abs(effect_endpoint - fill_rgb))) < 48:
                continue
            return _TextEffectEstimate(
                "glow",
                fill_color=fill[2],
                effect_color=_rgb_hex(effect_endpoint),
                effect_size=_scaled_effect_size(radius, pixel_scale),
            )
    return None


def _parse_hex_rgb(value: str) -> np.ndarray:
    return np.asarray(
        [int(value[index : index + 2], 16) for index in (1, 3, 5)],
        dtype=np.float64,
    )


def _hex_color_distance(first: str, second: str) -> int:
    first_rgb = _parse_hex_rgb(first)
    second_rgb = _parse_hex_rgb(second)
    return int(np.max(np.abs(first_rgb - second_rgb)))


def _estimate_compressed_glow(
    image: Image.Image,
    region: tuple[float, float, float, float],
    *,
    fill_color: str,
    pixel_scale: float,
) -> _TextEffectEstimate | None:
    fill = _parse_hex_rgb(fill_color)
    if float(np.min(fill)) < 200:
        return None
    width, height = image.size
    left = max(0, min(width - 1, round(region[0] * width)))
    top = max(0, min(height - 1, round(region[1] * height)))
    right = max(left + 1, min(width, round(region[2] * width)))
    bottom = max(top + 1, min(height, round(region[3] * height)))
    crop = np.asarray(image.crop((left, top, right, bottom)), dtype=np.float64)
    core = np.max(np.abs(crop - fill), axis=2) <= 32
    if int(np.count_nonzero(core)) < max(40, round(core.size * 0.01)):
        return None
    outer_core = _dilate(core, horizontal_radius=8, vertical_radius=8)
    inner_core = _dilate(core, horizontal_radius=1, vertical_radius=1)
    ring = outer_core & ~inner_core
    if int(np.count_nonzero(ring)) < 80:
        return None
    border = np.concatenate((crop[0], crop[-1], crop[:, 0], crop[:, -1]), axis=0)
    background = np.median(border, axis=0)
    residual = crop - background
    chroma = np.max(residual, axis=2) - np.min(residual, axis=2)
    colored_ring = ring & (chroma >= 30)
    if float(np.mean(colored_ring[ring])) < 0.10:
        return None
    colored_pixels = crop[colored_ring]
    colored_chroma = chroma[colored_ring]
    strongest = colored_pixels[colored_chroma >= float(np.percentile(colored_chroma, 75))]
    visible_effect = np.median(strongest, axis=0)
    effect_color = _rgb_hex(_extrapolate_color_endpoint(background, visible_effect))
    core_y, core_x = np.nonzero(core)
    halo_y, halo_x = np.nonzero(colored_ring)
    radius = max(
        (float(np.ptp(halo_x)) - float(np.ptp(core_x))) / 2,
        (float(np.ptp(halo_y)) - float(np.ptp(core_y))) / 2,
    )
    return _TextEffectEstimate(
        "glow",
        fill_color=fill_color,
        effect_color=effect_color,
        effect_size=_scaled_effect_size(max(1.0, radius * 0.875), pixel_scale),
    )


def _estimate_glow_layer(
    background: np.ndarray,
    clusters: list[tuple[int, np.ndarray, str, float, float, float, float, float, float, float]],
    *,
    image_size: tuple[int, int],
    pixel_scale: float,
) -> _TextEffectEstimate | None:
    if len(clusters) < 3:
        return None
    candidates = clusters[:3]
    for first_index, second_index, fill_index in ((0, 1, 2), (0, 2, 1), (1, 2, 0)):
        first = candidates[first_index]
        second = candidates[second_index]
        fill = candidates[fill_index]
        if not _is_interpolated_color_layer(background, first[1], second[1]):
            continue
        if _is_interpolated_color_layer(background, fill[1], first[1]) or (
            _is_interpolated_color_layer(background, fill[1], second[1])
        ):
            continue
        inner, outer = sorted(
            (first, second),
            key=lambda cluster: float(np.max(np.abs(cluster[1] - background))),
            reverse=True,
        )
        if outer[5] < inner[5] + 4 or outer[6] < inner[6] + 4:
            continue
        if abs(outer[3] - fill[3]) > max(4.0, image_size[0] * 0.04):
            continue
        if abs(outer[4] - fill[4]) > max(4.0, image_size[1] * 0.08):
            continue
        radius = max(
            (outer[5] - fill[5]) / 2,
            (outer[6] - fill[6]) / 2,
        )
        if radius < 2:
            continue
        return _TextEffectEstimate(
            "glow",
            fill_color=fill[2],
            effect_color=_rgb_hex(_extrapolate_color_endpoint(background, inner[1])),
            effect_size=_scaled_effect_size(radius, pixel_scale),
        )
    return None


def _extrapolate_color_endpoint(background: np.ndarray, visible: np.ndarray) -> np.ndarray:
    direction = visible - background
    scales = [1.0]
    for channel in range(3):
        delta = float(direction[channel])
        if delta > 1e-9:
            scales.append((255.0 - float(background[channel])) / delta)
        elif delta < -1e-9:
            scales.append((0.0 - float(background[channel])) / delta)
    scale = max(1.0, min(4.0, min(value for value in scales[1:] if value >= 1.0)))
    return np.clip(background + direction * scale, 0, 255)


def _scaled_effect_size(value: float, pixel_scale: float) -> int:
    scale = pixel_scale if math.isfinite(pixel_scale) and pixel_scale > 0 else 1.0
    return max(1, min(64, round(max(1.0, value) * scale)))


def _is_interpolated_color_layer(
    background: np.ndarray,
    first: np.ndarray,
    second: np.ndarray,
) -> bool:
    for endpoint, candidate in ((first, second), (second, first)):
        direction = endpoint - background
        denominator = float(np.dot(direction, direction))
        if denominator < 1:
            continue
        progress = float(np.dot(candidate - background, direction) / denominator)
        if not 0.05 <= progress <= 0.95:
            continue
        predicted = background + direction * progress
        if float(np.max(np.abs(candidate - predicted))) <= 14:
            return True
    return False


def _order_perspective_planes(
    suggestions: list[TextRegionSuggestion],
) -> tuple[TextRegionSuggestion, ...]:
    perspective_indexes = [
        index for index, item in enumerate(suggestions) if item.perspective_quad is not None
    ]
    if len(perspective_indexes) < 2:
        return tuple(suggestions)
    ordered = sorted(
        (suggestions[index] for index in perspective_indexes),
        key=lambda item: (
            item.region[1],
            item.region[0],
            -item.confidence,
            abs(item.rotation),
        ),
    )
    result = list(suggestions)
    for index, suggestion in zip(perspective_indexes, ordered):
        result[index] = suggestion
    return tuple(result)


def _detect_at_angle(
    grayscale: Image.Image,
    *,
    background: int,
    source_size: tuple[int, int],
    deskew_angle: float,
) -> tuple[TextRegionSuggestion, ...]:
    if deskew_angle == 0:
        analysis = grayscale.copy()
    else:
        analysis = grayscale.rotate(
            deskew_angle,
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor=background,
        )
    suggestions: list[TextRegionSuggestion] = []
    try:
        pixels = np.asarray(analysis, dtype=np.int16)
        for candidate in _detect_horizontal_regions(pixels):
            region = _map_box_to_source(
                candidate.box,
                rotated_size=analysis.size,
                source_size=source_size,
                deskew_angle=deskew_angle,
            )
            if region is None:
                continue
            perspective_quad = None
            if candidate.perspective_quad is not None:
                mapped_quad = _map_quad_to_source(
                    candidate.perspective_quad,
                    rotated_size=analysis.size,
                    source_size=source_size,
                    deskew_angle=deskew_angle,
                )
                if mapped_quad is not None:
                    try:
                        perspective_quad = validate_normalized_quad(
                            mapped_quad,
                            region=region,
                        )
                    except ValueError:
                        perspective_quad = None
            alignment_bonus = min(
                0.12,
                _box_aspect(candidate.box) * 0.012,
            )
            angle_penalty = abs(deskew_angle) / 90 * 0.05
            confidence = float(
                np.clip(
                    candidate.confidence + alignment_bonus - angle_penalty,
                    0,
                    0.99,
                )
            )
            suggestions.append(
                TextRegionSuggestion(
                    region=region,
                    confidence=min(0.99, confidence + (0.025 if perspective_quad else 0.0)),
                    rotation=-deskew_angle,
                    curve=0.0 if perspective_quad is not None else candidate.curve,
                    perspective_quad=perspective_quad,
                )
            )
    finally:
        analysis.close()
    return tuple(suggestions)


def _refinement_angles(angle_scores: dict[float, float]) -> tuple[float, ...]:
    ranked = sorted(
        angle_scores,
        key=lambda angle: (-angle_scores[angle], abs(angle), angle),
    )
    if ranked and angle_scores[ranked[0]] >= 0.80:
        minimum_score = max(0.55, angle_scores[ranked[0]] * 0.58)
        ranked = [angle for angle in ranked if angle_scores[angle] >= minimum_score]
    seeds: list[float] = []
    for angle in ranked:
        if any(abs(angle - selected) < REFINEMENT_RADIUS * 2 + 1 for selected in seeds):
            continue
        seeds.append(angle)
        if len(seeds) >= MAX_REFINEMENT_SEEDS:
            break
    coarse = set(COARSE_DESKEW_ANGLES)
    refined = {
        float(seed + offset)
        for seed in seeds
        for offset in range(-REFINEMENT_RADIUS, REFINEMENT_RADIUS + 1)
        if -90 <= seed + offset <= 90 and float(seed + offset) not in coarse
    }
    return tuple(sorted(refined, key=lambda angle: (abs(angle), angle)))


def _has_photo_like_edge_field(pixels: np.ndarray) -> bool:
    horizontal = np.abs(np.diff(pixels, axis=1, prepend=pixels[:, :1]))
    vertical = np.abs(np.diff(pixels, axis=0, prepend=pixels[:1, :]))
    gradient = np.maximum(horizontal, vertical)
    nonzero = gradient[gradient > 0]
    if nonzero.size == 0:
        return False
    threshold = int(np.clip(np.percentile(nonzero, 72), 18, 72))
    raw_edges = gradient >= threshold
    row_counts = np.count_nonzero(raw_edges, axis=1)
    row_coverage = float(np.mean(row_counts >= max(2, round(raw_edges.shape[1] * 0.01))))
    active_tiles = 0
    tile_count = 0
    for row_indexes in np.array_split(np.arange(raw_edges.shape[0]), 4):
        for column_indexes in np.array_split(np.arange(raw_edges.shape[1]), 6):
            tile = raw_edges[np.ix_(row_indexes, column_indexes)]
            active_tiles += int(float(np.mean(tile)) >= 0.003)
            tile_count += 1
    tile_coverage = active_tiles / max(1, tile_count)
    return float(np.mean(raw_edges)) >= 0.025 and row_coverage >= 0.55 and tile_coverage >= 0.55


def _has_compressed_photo_field(pixels: np.ndarray) -> bool:
    active_tiles = 0
    tile_count = 0
    for row_indexes in np.array_split(np.arange(pixels.shape[0]), 4):
        for column_indexes in np.array_split(np.arange(pixels.shape[1]), 6):
            tile = pixels[np.ix_(row_indexes, column_indexes)]
            active_tiles += int(float(np.std(tile)) >= 1.0)
            tile_count += 1
    return active_tiles / max(1, tile_count) >= 0.75


def _is_photo_like_edge_mask(raw_edges: np.ndarray) -> bool:
    row_counts = np.count_nonzero(raw_edges, axis=1)
    row_coverage = float(np.mean(row_counts >= max(2, round(raw_edges.shape[1] * 0.01))))
    return float(np.mean(raw_edges)) >= 0.035 and row_coverage >= 0.55


def _detect_horizontal_regions(pixels: np.ndarray) -> tuple[_PixelSuggestion, ...]:
    if pixels.shape[0] < 8 or pixels.shape[1] < 8:
        return ()
    horizontal = np.abs(np.diff(pixels, axis=1, prepend=pixels[:, :1]))
    vertical = np.abs(np.diff(pixels, axis=0, prepend=pixels[:1, :]))
    gradient = np.maximum(horizontal, vertical)
    nonzero = gradient[gradient > 0]
    if nonzero.size == 0:
        return ()
    threshold = int(np.clip(np.percentile(nonzero, 72), 18, 72))
    raw_edges = gradient >= threshold
    textured_edges = _is_photo_like_edge_mask(raw_edges)
    if textured_edges:
        image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
        try:
            smoothed = image.filter(ImageFilter.BoxBlur(5))
            try:
                local_background = np.asarray(smoothed, dtype=np.int16)
            finally:
                smoothed.close()
        finally:
            image.close()
        local_contrast = np.abs(pixels - local_background)
        contrast_values = local_contrast[local_contrast > 0]
        if contrast_values.size:
            contrast_threshold = int(np.clip(np.percentile(contrast_values, 72), 12, 48))
            raw_edges &= local_contrast >= contrast_threshold
    edges = _dilate(
        raw_edges,
        horizontal_radius=max(2, pixels.shape[1] // 260),
        vertical_radius=2,
    )

    analysis_height, analysis_width = edges.shape
    row_counts = np.count_nonzero(edges, axis=1)
    minimum_row_pixels = max(4, round(analysis_width * 0.012))
    if textured_edges:
        positive_row_counts = row_counts[row_counts > 0]
        dense_row_threshold = (
            round(float(np.percentile(positive_row_counts, 80))) if positive_row_counts.size else 0
        )
        minimum_row_pixels = max(
            minimum_row_pixels,
            round(analysis_width * 0.06),
            dense_row_threshold,
        )
    active_rows = row_counts >= minimum_row_pixels
    row_runs = _runs(active_rows, max_gap=max(2, analysis_height // 180))
    suggestions: list[_PixelSuggestion] = []
    for top, bottom in row_runs:
        run_height = bottom - top
        if run_height < max(5, round(analysis_height * 0.008)):
            continue
        if run_height > analysis_height * 0.32:
            continue
        band = edges[top:bottom]
        minimum_column_pixels = max(2, round(run_height * 0.10))
        if textured_edges:
            minimum_column_pixels = max(
                minimum_column_pixels,
                round(run_height * 0.20),
            )
        active_columns = np.count_nonzero(band, axis=0) >= minimum_column_pixels
        column_runs = _runs(active_columns, max_gap=max(5, analysis_width // 45))
        for left, right in column_runs:
            run_width = right - left
            if run_width < max(18, round(analysis_width * 0.045)):
                continue
            if run_width / run_height < 1.15:
                continue
            occupied = float(np.count_nonzero(band[:, left:right]))
            density = occupied / max(1, run_width * run_height)
            if density < 0.035:
                continue
            pad_x = max(2, round(run_width * 0.025))
            pad_y = max(2, round(run_height * 0.16))
            if textured_edges:
                pad_y = max(pad_y, round(analysis_height * 0.055))
            padded_left = max(0, left - pad_x)
            padded_top = max(0, top - pad_y)
            padded_right = min(analysis_width, right + pad_x)
            padded_bottom = min(analysis_height, bottom + pad_y)
            area_ratio = (run_width * run_height) / (analysis_width * analysis_height)
            confidence = float(np.clip(0.35 + density * 0.8 + min(0.25, area_ratio * 3), 0, 0.86))
            perspective_quad = _estimate_perspective_quad(
                edges,
                content_box=(left, top, right, bottom),
                padded_box=(padded_left, padded_top, padded_right, padded_bottom),
            )
            curve = (
                0.0
                if perspective_quad is not None
                else _estimate_curved_baseline(
                    raw_edges,
                    content_box=(left, top, right, bottom),
                )
            )
            suggestions.append(
                _PixelSuggestion(
                    box=(padded_left, padded_top, padded_right, padded_bottom),
                    confidence=min(
                        0.88,
                        confidence + (0.02 if abs(curve) > 1e-9 else 0.0),
                    ),
                    perspective_quad=perspective_quad,
                    curve=curve,
                )
            )
    return tuple(suggestions)


def _estimate_curved_baseline(
    edges: np.ndarray,
    *,
    content_box: tuple[int, int, int, int],
) -> float:
    """Estimate a conservative signed curve from independent glyph envelopes."""

    left, top, right, bottom = content_box
    run_width = right - left
    run_height = bottom - top
    if run_width < 96 or run_height < 12 or run_width / run_height < 2.4:
        return 0.0
    band = edges[top:bottom, left:right]
    active_columns = np.count_nonzero(band, axis=0) >= 2
    column_runs = _runs(active_columns, max_gap=1)
    samples: list[tuple[float, float, float]] = []
    maximum_glyph_width = max(8, round(run_width * 0.18))
    for run_left, run_right in column_runs:
        glyph_width = run_right - run_left
        if not 2 <= glyph_width <= maximum_glyph_width:
            continue
        y_indexes = np.flatnonzero(band[:, run_left:run_right].any(axis=1))
        if y_indexes.size < 4:
            continue
        envelope_center = (
            float(np.percentile(y_indexes, 4)) + float(np.percentile(y_indexes, 96))
        ) / 2
        samples.append(
            (
                left + (run_left + run_right) / 2,
                top + envelope_center,
                float(glyph_width),
            )
        )
    glyph_curve = _fit_curved_samples(
        samples,
        run_width=run_width,
        run_height=run_height,
        minimum_samples=7,
        minimum_span_ratio=0.62,
        maximum_residual_ratio=0.025,
    )
    if abs(glyph_curve) > 1e-9:
        return glyph_curve

    bin_samples: list[tuple[float, float, float]] = []
    bin_count = 12
    for index in range(bin_count):
        bin_left = round(run_width * index / bin_count)
        bin_right = round(run_width * (index + 1) / bin_count)
        if bin_right <= bin_left:
            continue
        y_indexes = np.flatnonzero(band[:, bin_left:bin_right].any(axis=1))
        if y_indexes.size < 4:
            continue
        envelope_center = (
            float(np.percentile(y_indexes, 4)) + float(np.percentile(y_indexes, 96))
        ) / 2
        bin_samples.append(
            (
                left + (bin_left + bin_right) / 2,
                top + envelope_center,
                float(bin_right - bin_left),
            )
        )
    return _fit_curved_samples(
        bin_samples,
        run_width=run_width,
        run_height=run_height,
        minimum_samples=10,
        minimum_span_ratio=0.82,
        maximum_residual_ratio=0.022,
    )


def _fit_curved_samples(
    samples: list[tuple[float, float, float]],
    *,
    run_width: int,
    run_height: int,
    minimum_samples: int,
    minimum_span_ratio: float,
    maximum_residual_ratio: float,
) -> float:
    if len(samples) < minimum_samples:
        return 0.0
    sample_left = min(item[0] for item in samples)
    sample_right = max(item[0] for item in samples)
    sample_span = sample_right - sample_left
    if sample_span < run_width * minimum_span_ratio:
        return 0.0
    x_values = np.asarray([item[0] for item in samples], dtype=np.float64)
    y_values = np.asarray([item[1] for item in samples], dtype=np.float64)
    weights = np.sqrt(np.asarray([item[2] for item in samples], dtype=np.float64))
    center_x = (sample_left + sample_right) / 2
    normalized_x = (x_values - center_x) / max(1.0, sample_span / 2)
    coefficients = np.polyfit(normalized_x, y_values, 2, w=weights)
    predicted = np.polyval(coefficients, normalized_x)
    residual_ratio = float(np.mean(np.abs(y_values - predicted)) / run_height)
    if residual_ratio > maximum_residual_ratio:
        return 0.0
    edge_center = float((np.polyval(coefficients, -1.0) + np.polyval(coefficients, 1.0)) / 2)
    middle_center = float(np.polyval(coefficients, 0.0))
    curve_delta = edge_center - middle_center
    delta_ratio = curve_delta / run_height
    if not 0.045 <= abs(delta_ratio) <= 0.70:
        return 0.0
    linear_delta = abs(float(coefficients[1]))
    if linear_delta > max(abs(curve_delta) * 0.65, run_height * 0.08):
        return 0.0
    magnitude = math.sqrt(abs(delta_ratio) / 0.54) * 0.9
    return float(np.clip(math.copysign(magnitude, curve_delta), -1.0, 1.0))


def _estimate_perspective_quad(
    edges: np.ndarray,
    *,
    content_box: tuple[int, int, int, int],
    padded_box: tuple[int, int, int, int],
    minimum_side_ratio: float = 0.38,
    require_opposite_edge_slopes: bool = True,
) -> tuple[float, float, float, float, float, float, float, float] | None:
    """Estimate a conservative keystone only when scale changes across a text line."""

    left, top, right, bottom = content_box
    run_width = right - left
    run_height = bottom - top
    if run_width < 48 or run_height < 10:
        return None
    bin_count = max(6, min(12, run_width // 28))
    samples: list[tuple[float, float, float]] = []
    for index in range(bin_count):
        bin_left = left + round(run_width * index / bin_count)
        bin_right = left + round(run_width * (index + 1) / bin_count)
        if bin_right <= bin_left:
            continue
        y_indexes = np.flatnonzero(edges[top:bottom, bin_left:bin_right].any(axis=1))
        if y_indexes.size < max(4, round(run_height * 0.25)):
            continue
        samples.append(
            (
                (bin_left + bin_right) / 2,
                top + float(np.percentile(y_indexes, 4)),
                top + float(np.percentile(y_indexes, 96)),
            )
        )
    if len(samples) < max(6, bin_count - 2):
        return None

    x_values = np.asarray([item[0] for item in samples], dtype=np.float64)
    top_values = np.asarray([item[1] for item in samples], dtype=np.float64)
    bottom_values = np.asarray([item[2] for item in samples], dtype=np.float64)
    top_line = np.polyfit(x_values, top_values, 1)
    bottom_line = np.polyfit(x_values, bottom_values, 1)
    predicted_top = np.polyval(top_line, x_values)
    predicted_bottom = np.polyval(bottom_line, x_values)
    residual = float(
        np.mean(np.abs(top_values - predicted_top) + np.abs(bottom_values - predicted_bottom))
        / (2 * max(1, run_height))
    )
    if residual > 0.105:
        return None

    left_height = float(np.polyval(bottom_line, left) - np.polyval(top_line, left))
    right_height = float(np.polyval(bottom_line, right) - np.polyval(top_line, right))
    maximum_height = max(left_height, right_height)
    if min(left_height, right_height) < max(4.0, run_height * minimum_side_ratio):
        return None
    scale_change = abs(left_height - right_height) / max(1.0, maximum_height)
    if scale_change < 0.17:
        return None
    top_change = float(np.polyval(top_line, right) - np.polyval(top_line, left))
    bottom_change = float(np.polyval(bottom_line, right) - np.polyval(bottom_line, left))
    if (require_opposite_edge_slopes and top_change * bottom_change >= 0) or max(
        abs(top_change), abs(bottom_change)
    ) < max(2.0, run_height * 0.14):
        return None

    padded_left, padded_top, padded_right, padded_bottom = padded_box
    pad_y = max(1.0, (padded_bottom - padded_top - run_height) / 2)

    def clamped_y(line: np.ndarray, x: float, offset: float) -> float:
        return float(np.clip(np.polyval(line, x) + offset, padded_top, padded_bottom))

    quad = (
        float(padded_left),
        clamped_y(top_line, padded_left, -pad_y),
        float(padded_right),
        clamped_y(top_line, padded_right, -pad_y),
        float(padded_right),
        clamped_y(bottom_line, padded_right, pad_y),
        float(padded_left),
        clamped_y(bottom_line, padded_left, pad_y),
    )
    points = [(quad[index], quad[index + 1]) for index in range(0, 8, 2)]
    crosses = []
    for index in range(4):
        first = points[index]
        second = points[(index + 1) % 4]
        third = points[(index + 2) % 4]
        crosses.append(
            (second[0] - first[0]) * (third[1] - second[1])
            - (second[1] - first[1]) * (third[0] - second[0])
        )
    if not (all(value > 1 for value in crosses) or all(value < -1 for value in crosses)):
        return None
    return quad


def _map_box_to_source(
    box: tuple[int, int, int, int],
    *,
    rotated_size: tuple[int, int],
    source_size: tuple[int, int],
    deskew_angle: float,
) -> tuple[float, float, float, float] | None:
    left, top, right, bottom = box
    rotated_width, rotated_height = rotated_size
    source_width, source_height = source_size
    radians = math.radians(deskew_angle)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    points: list[tuple[float, float]] = []
    for x, y in ((left, top), (right, top), (right, bottom), (left, bottom)):
        rotated_x = x - rotated_width / 2
        rotated_y = y - rotated_height / 2
        source_x = cosine * rotated_x - sine * rotated_y + source_width / 2
        source_y = sine * rotated_x + cosine * rotated_y + source_height / 2
        points.append((source_x, source_y))
    mapped_left = max(0.0, min(point[0] for point in points))
    mapped_top = max(0.0, min(point[1] for point in points))
    mapped_right = min(float(source_width), max(point[0] for point in points))
    mapped_bottom = min(float(source_height), max(point[1] for point in points))
    if mapped_right - mapped_left < 2 or mapped_bottom - mapped_top < 2:
        return None
    return (
        mapped_left / source_width,
        mapped_top / source_height,
        mapped_right / source_width,
        mapped_bottom / source_height,
    )


def _map_quad_to_source(
    quad: tuple[float, float, float, float, float, float, float, float],
    *,
    rotated_size: tuple[int, int],
    source_size: tuple[int, int],
    deskew_angle: float,
) -> NormalizedQuad | None:
    rotated_width, rotated_height = rotated_size
    source_width, source_height = source_size
    radians = math.radians(deskew_angle)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    mapped: list[float] = []
    for index in range(0, 8, 2):
        rotated_x = quad[index] - rotated_width / 2
        rotated_y = quad[index + 1] - rotated_height / 2
        source_x = cosine * rotated_x - sine * rotated_y + source_width / 2
        source_y = sine * rotated_x + cosine * rotated_y + source_height / 2
        if not 0 <= source_x <= source_width or not 0 <= source_y <= source_height:
            return None
        mapped.extend((source_x / source_width, source_y / source_height))
    try:
        return validate_normalized_quad(mapped)
    except ValueError:
        return None


def _box_aspect(box: tuple[int, int, int, int]) -> float:
    left, top, right, bottom = box
    return (right - left) / max(1, bottom - top)


def _intersection_over_union(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    intersection_width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    intersection_height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = intersection_width * intersection_height
    if intersection <= 0:
        return 0.0
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / max(1e-12, first_area + second_area - intersection)


def _intersection_over_smaller(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    intersection_width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    intersection_height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = intersection_width * intersection_height
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / max(1e-12, min(first_area, second_area))


def _dilate(
    mask: np.ndarray,
    *,
    horizontal_radius: int,
    vertical_radius: int,
) -> np.ndarray:
    height, width = mask.shape
    dilated = np.zeros_like(mask)
    for offset in range(-horizontal_radius, horizontal_radius + 1):
        source_left = max(0, -offset)
        source_right = min(width, width - offset)
        target_left = max(0, offset)
        target_right = min(width, width + offset)
        dilated[:, target_left:target_right] |= mask[:, source_left:source_right]
    vertical = np.zeros_like(mask)
    for offset in range(-vertical_radius, vertical_radius + 1):
        source_top = max(0, -offset)
        source_bottom = min(height, height - offset)
        target_top = max(0, offset)
        target_bottom = min(height, height + offset)
        vertical[target_top:target_bottom] |= dilated[source_top:source_bottom]
    return vertical


def _runs(active: np.ndarray, *, max_gap: int) -> tuple[tuple[int, int], ...]:
    indexes = np.flatnonzero(active)
    if indexes.size == 0:
        return ()
    runs: list[tuple[int, int]] = []
    start = previous = int(indexes[0])
    for raw_index in indexes[1:]:
        index = int(raw_index)
        if index - previous > max_gap + 1:
            runs.append((start, previous + 1))
            start = index
        previous = index
    runs.append((start, previous + 1))
    return tuple(runs)


__all__ = ["TextRegionSuggestion", "detect_text_regions"]
