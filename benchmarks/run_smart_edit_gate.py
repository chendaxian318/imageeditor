"""Run the M5 offline smart-edit fidelity and performance acceptance gate."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

import numpy as np
from PIL import (
    Image,
    ImageChops,
    ImageColor,
    ImageDraw,
    ImageFilter,
    ImageFont,
    ImageOps,
    ImageStat,
)
from PIL import __version__ as pillow_version

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmarks.run_benchmarks import _metric
from light_image_editor.domain import (
    AlphaBrushStroke,
    AssetRevision,
    ContentEditRequest,
    Provenance,
    SelectionMaskStroke,
    TextReplacementDraft,
    serialize_alpha_strokes,
)
from light_image_editor.providers import MOCK_MODEL_ID, MockProvider
from light_image_editor.providers.mock_provider import (
    _draw_replacement_text,
    _warp_surface_to_quad,
)
from light_image_editor.services.background_compositor import (
    BackgroundSpec,
    compose_background,
)
from light_image_editor.services.font_matcher import (
    installed_font_candidates,
    match_fonts,
)
from light_image_editor.services.smart_selection import (
    decode_selection_mask,
    detect_smart_selection,
    refine_selection_mask,
)
from light_image_editor.services.text_ocr import _rectify_curved_baseline
from light_image_editor.services.text_region_detector import detect_text_regions
from light_image_editor.version import __version__

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {
        "image_size": (128, 96),
        "repetitions": 2,
        "backgrounds": ((250, 250, 250),),
    },
    "standard": {
        "image_size": (512, 384),
        "repetitions": 3,
        "backgrounds": (
            (250, 250, 250),
            (42, 180, 74),
            (28, 32, 40),
        ),
    },
}


@dataclass(frozen=True, slots=True)
class SmartEditGateThresholds:
    minimum_background_cases: int = 3
    minimum_foreground_recall: float = 0.995
    minimum_background_removal: float = 0.995
    minimum_edge_background_cases: int = 2
    maximum_edge_foreground_alpha_mae: float = 2.0
    minimum_background_replacement_cases: int = 2
    maximum_region_repair_mae: float = 3.0
    minimum_region_repair_outside_unchanged: float = 1.0
    minimum_mask_selected_change: float = 0.95
    minimum_mask_outside_unchanged: float = 1.0
    minimum_mask_unselected_subject_unchanged: float = 1.0
    minimum_mask_manual_exclusion_unchanged: float = 1.0
    maximum_upscale_alpha_mae: float = 2.0
    minimum_text_region_change: float = 0.03
    minimum_text_red_pixels: int = 5
    minimum_vertical_text_red_pixels: int = 5
    minimum_vertical_text_aspect_ratio: float = 1.5
    minimum_perspective_text_red_pixels: int = 5
    minimum_curved_text_red_pixels: int = 5
    minimum_curved_text_center_delta: float = 4.0
    minimum_styled_text_replacement_cases: int = 5
    minimum_styled_text_primary_pixels: int = 100
    minimum_styled_text_secondary_pixels: int = 100
    minimum_glow_halo_pixels: int = 100
    maximum_styled_text_replacement_p95_ms: float = 1_000.0
    minimum_perspective_scale_delta: float = 0.04
    minimum_outside_region_unchanged: float = 1.0
    minimum_text_detection_cases: int = 8
    minimum_text_detection_overlap: float = 0.40
    maximum_text_detection_angle_error: float = 1.0
    minimum_multi_plane_candidates: int = 2
    minimum_multi_plane_overlap: float = 0.85
    minimum_multi_plane_scale_delta: float = 0.02
    minimum_occluded_multi_plane_candidates: int = 2
    minimum_occluded_multi_plane_coverage: float = 0.85
    minimum_occluded_multi_plane_containment: float = 0.65
    minimum_occluded_multi_plane_region_iou: float = 0.10
    minimum_occluded_multi_plane_ink_occlusion: float = 0.04
    minimum_occluded_multi_plane_scale_delta: float = 0.015
    maximum_occluded_multi_plane_fill_color_error: int = 4
    minimum_occluded_multi_plane_source_color_distance: int = 12
    maximum_occluded_multi_plane_source_color_distance: int = 24
    minimum_occluded_multi_plane_axis_similarity: float = 0.996
    maximum_occluded_multi_plane_axis_similarity: float = 0.998
    minimum_three_plane_candidates: int = 3
    minimum_three_plane_coverage: float = 0.85
    minimum_three_plane_containment: float = 0.65
    minimum_three_plane_pairwise_region_iou: float = 0.01
    minimum_three_plane_pairwise_ink_occlusion: float = 0.01
    minimum_three_plane_peak_ink_occlusion: float = 0.15
    minimum_three_plane_scale_delta: float = 0.02
    maximum_three_plane_fill_color_error: int = 4
    maximum_same_color_occluded_plane_candidates: int = 1
    minimum_curved_text_detection_cases: int = 6
    minimum_curved_text_detection_overlap: float = 0.85
    maximum_curved_text_curve_error: float = 0.12
    minimum_styled_text_detection_cases: int = 5
    minimum_styled_text_detection_overlap: float = 0.85
    maximum_text_fill_color_error: int = 24
    maximum_styled_text_color_error: int = 8
    maximum_styled_text_effect_size_error: int = 2
    maximum_styled_text_detection_p95_ms: float = 1_000.0
    minimum_surface_background_text_cases: int = 2
    minimum_surface_background_text_overlap: float = 0.85
    maximum_surface_background_fill_color_error: int = 8
    maximum_surface_background_effect_color_error: int = 8
    maximum_surface_background_effect_size_error: int = 2
    maximum_surface_background_text_p95_ms: float = 1_000.0
    minimum_structured_background_text_cases: int = 2
    minimum_structured_background_text_overlap: float = 0.85
    maximum_structured_background_fill_color_error: int = 8
    maximum_structured_background_effect_color_error: int = 12
    maximum_structured_background_effect_size_error: int = 2
    maximum_structured_background_text_p95_ms: float = 1_000.0
    minimum_photo_background_text_cases: int = 8
    minimum_photo_background_text_overlap: float = 0.85
    maximum_photo_background_fill_color_error: int = 5
    maximum_photo_background_effect_color_error: int = 12
    maximum_photo_background_effect_size_error: int = 4
    maximum_photo_background_region_width: float = 0.75
    maximum_photo_background_region_height: float = 0.30
    minimum_photo_background_negative_cases: int = 4
    maximum_photo_background_negative_candidates: int = 0
    maximum_photo_background_text_p95_ms: float = 1_200.0
    minimum_photo_layout_cases: int = 5
    minimum_photo_layout_overlap: float = 0.85
    maximum_photo_layout_fill_color_error: int = 5
    minimum_jpeg_photo_cases: int = 15
    minimum_jpeg_photo_negative_cases: int = 12
    maximum_jpeg_photo_negative_candidates: int = 0
    minimum_jpeg_photo_overlap: float = 0.85
    maximum_jpeg_photo_fill_color_error: int = 16
    maximum_jpeg_photo_effect_color_error: int = 8
    maximum_jpeg_photo_effect_size_error: int = 1
    minimum_curved_ocr_rectification_cases: int = 4
    minimum_curved_ocr_input_delta_px: float = 3.0
    maximum_curved_ocr_residual_delta_px: float = 3.5
    maximum_curved_ocr_residual_ratio: float = 0.45
    maximum_curved_ocr_p95_ms: float = 300.0
    maximum_text_detection_p95_ms: float = 1_000.0
    minimum_font_match_cases: int = 2
    maximum_font_match_p95_ms: float = 1_000.0
    minimum_smart_selection_cases: int = 7
    minimum_smart_selection_coverage: float = 0.95
    minimum_smart_selection_iou: float = 0.70
    maximum_smart_selection_p95_ms: float = 1_000.0
    maximum_operation_p95_ms: float = 5_000.0


def _file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme.casefold() != "file":
        raise ValueError("门禁结果必须是本地文件 URI")
    value = unquote(parsed.path)
    if len(value) >= 3 and value[0] == "/" and value[2] == ":":
        value = value[1:]
    return Path(value).resolve(strict=True)


def _pixel_hash(image: Image.Image) -> str:
    rgba = image.convert("RGBA")
    try:
        return hashlib.sha256(rgba.tobytes()).hexdigest()
    finally:
        rgba.close()


def _source_revision(path: Path, *, suffix: str) -> AssetRevision:
    with Image.open(path) as image:
        image.load()
        size = image.size
    return AssetRevision(
        asset_id=f"asset_gate_{suffix}",
        sequence=1,
        content_uri=path.resolve().as_uri(),
        media_type="image/png",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        provenance=Provenance.imported(source=path.name),
        revision_id=f"rev_gate_{suffix}",
        byte_size=path.stat().st_size,
        width=size[0],
        height=size[1],
    )


def _submit_edit(
    provider: MockProvider,
    source: AssetRevision,
    *,
    operation: str,
    parameters: tuple[tuple[str, str | int | float | bool | None], ...],
    request_id: str,
) -> tuple[AssetRevision, Path, float]:
    started = time.perf_counter()
    snapshot = provider.submit_edit(
        ContentEditRequest(
            source_revision=source,
            prompt=f"M5 门禁：{operation}",
            model=MOCK_MODEL_ID,
            parameters=parameters,
            request_id=request_id,
            idempotency_key=f"key-{request_id}",
        )
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    revision = snapshot.outputs[0].current_revision
    return revision, _file_uri_path(revision.content_uri), elapsed_ms


def _create_upscale_fixture(path: Path, size: tuple[int, int]) -> None:
    width, height = size
    image = Image.new("RGBA", size)
    for y in range(height):
        for x in range(width):
            image.putpixel(
                (x, y),
                (
                    (x * 7 + y * 3) % 256,
                    (x * 2 + y * 11) % 256,
                    (x * 13 + y * 5) % 256,
                    round(x * 255 / max(1, width - 1)),
                ),
            )
    image.save(path, "PNG")
    image.close()


def _create_background_fixture(
    path: Path,
    size: tuple[int, int],
    background: tuple[int, int, int],
) -> tuple[int, int, int, int]:
    width, height = size
    box = (width // 4, height // 5, width * 3 // 4, height * 4 // 5)
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (box[0], box[1], box[2] - 1, box[3] - 1),
        fill=(52, 104, 202),
    )
    draw.ellipse(
        (
            width * 3 // 8,
            height // 3,
            width * 5 // 8,
            height * 2 // 3,
        ),
        fill=(225, 82, 90),
    )
    image.save(path, "PNG")
    image.close()
    return box


def _mask_quality(
    alpha: Image.Image,
    subject_box: tuple[int, int, int, int],
) -> tuple[float, float]:
    foreground_total = 0
    foreground_kept = 0
    background_total = 0
    background_removed = 0
    left, top, right, bottom = subject_box
    pixels = cast(list[int], list(alpha.getdata()))
    for y in range(alpha.height):
        for x in range(alpha.width):
            value = pixels[y * alpha.width + x]
            if left <= x < right and top <= y < bottom:
                foreground_total += 1
                foreground_kept += value >= 250
            else:
                background_total += 1
                background_removed += value <= 5
    return (
        foreground_kept / max(1, foreground_total),
        background_removed / max(1, background_total),
    )


def _run_upscale_probe(
    root: Path,
    provider: MockProvider,
    size: tuple[int, int],
    repetitions: int,
) -> dict[str, Any]:
    source_path = root / "upscale-source.png"
    _create_upscale_fixture(source_path, size)
    source_revision = _source_revision(source_path, suffix="upscale")
    with Image.open(source_path) as source_image:
        source_image.load()
        source_alpha = source_image.getchannel("A")
    scales: dict[str, Any] = {}
    try:
        for scale in (2, 4):
            elapsed: list[float] = []
            hashes: list[str] = []
            alpha_mae: list[float] = []
            output_sizes: list[list[int]] = []
            modes: list[str] = []
            for index in range(repetitions):
                _revision, output_path, elapsed_ms = _submit_edit(
                    provider,
                    source_revision,
                    operation="upscale",
                    parameters=(("operation", "upscale"), ("scale", scale)),
                    request_id=f"gate-upscale-{scale}-{index}",
                )
                elapsed.append(elapsed_ms)
                with Image.open(output_path) as output:
                    output.load()
                    output_sizes.append(list(output.size))
                    modes.append(output.mode)
                    hashes.append(_pixel_hash(output))
                    output_alpha = output.getchannel("A")
                    reduced = output_alpha.resize(size, Image.Resampling.LANCZOS)
                    difference = ImageChops.difference(source_alpha, reduced)
                    alpha_mae.append(float(ImageStat.Stat(difference).mean[0]))
                    difference.close()
                    reduced.close()
                    output_alpha.close()
            scales[str(scale)] = {
                "expected_size": [size[0] * scale, size[1] * scale],
                "output_sizes": output_sizes,
                "modes": modes,
                "maximum_alpha_mae": round(max(alpha_mae), 4),
                "deterministic_pixels": len(set(hashes)) == 1,
                "elapsed_ms": _metric(elapsed),
            }
    finally:
        source_alpha.close()
    return {"source_size": list(size), "scales": scales}


def _run_background_probe(
    root: Path,
    provider: MockProvider,
    size: tuple[int, int],
    repetitions: int,
    backgrounds: tuple[tuple[int, int, int], ...],
) -> tuple[dict[str, Any], AssetRevision]:
    cases: list[dict[str, Any]] = []
    first_revision: AssetRevision | None = None
    for case_index, background in enumerate(backgrounds):
        source_path = root / f"background-{case_index}.png"
        subject_box = _create_background_fixture(source_path, size, background)
        source_revision = _source_revision(source_path, suffix=f"background_{case_index}")
        hashes: list[str] = []
        elapsed: list[float] = []
        recalls: list[float] = []
        removal_rates: list[float] = []
        for repetition in range(repetitions):
            revision, output_path, elapsed_ms = _submit_edit(
                provider,
                source_revision,
                operation="remove_background",
                parameters=(
                    ("operation", "remove_background"),
                    ("tolerance", 20),
                    ("feather", 1),
                ),
                request_id=f"gate-background-{case_index}-{repetition}",
            )
            if first_revision is None:
                first_revision = revision
            elapsed.append(elapsed_ms)
            with Image.open(output_path) as output:
                output.load()
                hashes.append(_pixel_hash(output))
                alpha = output.getchannel("A")
                foreground_recall, background_removal = _mask_quality(alpha, subject_box)
                alpha.close()
                recalls.append(foreground_recall)
                removal_rates.append(background_removal)
        cases.append(
            {
                "background_rgb": list(background),
                "subject_box": list(subject_box),
                "minimum_foreground_recall": round(min(recalls), 6),
                "minimum_background_removal": round(min(removal_rates), 6),
                "deterministic_pixels": len(set(hashes)) == 1,
                "elapsed_ms": _metric(elapsed),
            }
        )

    if first_revision is None:
        raise RuntimeError("去背景门禁没有生成结果")
    return {"case_count": len(cases), "cases": cases}, first_revision


def _create_edge_background_fixture(
    path: Path,
    size: tuple[int, int],
    *,
    subject_alpha: int,
) -> tuple[int, int, int, int]:
    width, height = size
    subject_box = (0, 0, max(1, width // 3), height)
    image = Image.new("RGBA", size)
    for x in range(width):
        shade = round(190 + x * 60 / max(1, width - 1))
        for y in range(height):
            image.putpixel((x, y), (shade, shade, shade, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (0, 0, subject_box[2] - 1, height - 1),
        fill=(30, 90, 220, subject_alpha),
    )
    image.save(path, "PNG")
    image.close()
    return subject_box


def _edge_alpha_quality(
    alpha: Image.Image,
    subject_box: tuple[int, int, int, int],
    *,
    expected_subject_alpha: int,
) -> tuple[float, float]:
    left, top, right, bottom = subject_box
    pixels = cast(list[int], list(alpha.getdata()))
    foreground_error = 0
    foreground_total = 0
    background_removed = 0
    background_total = 0
    for y in range(alpha.height):
        for x in range(alpha.width):
            value = pixels[y * alpha.width + x]
            if left <= x < right and top <= y < bottom:
                foreground_total += 1
                foreground_error += abs(value - expected_subject_alpha)
            else:
                background_total += 1
                background_removed += value <= 5
    return (
        foreground_error / max(1, foreground_total),
        background_removed / max(1, background_total),
    )


def _run_edge_background_probe(
    root: Path,
    provider: MockProvider,
    size: tuple[int, int],
    repetitions: int,
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for name, subject_alpha in (("opaque", 255), ("semi_transparent", 128)):
        source_path = root / f"edge-background-{name}.png"
        subject_box = _create_edge_background_fixture(
            source_path,
            size,
            subject_alpha=subject_alpha,
        )
        source_revision = _source_revision(source_path, suffix=f"edge_{name}")
        elapsed: list[float] = []
        hashes: list[str] = []
        alpha_errors: list[float] = []
        removal_rates: list[float] = []
        for repetition in range(repetitions):
            _revision, output_path, elapsed_ms = _submit_edit(
                provider,
                source_revision,
                operation="remove_background",
                parameters=(
                    ("operation", "remove_background"),
                    ("tolerance", 12),
                    ("feather", 4),
                ),
                request_id=f"gate-edge-background-{name}-{repetition}",
            )
            elapsed.append(elapsed_ms)
            with Image.open(output_path) as output:
                output.load()
                hashes.append(_pixel_hash(output))
                alpha = output.getchannel("A")
                foreground_error, background_removal = _edge_alpha_quality(
                    alpha,
                    subject_box,
                    expected_subject_alpha=subject_alpha,
                )
                alpha.close()
                alpha_errors.append(foreground_error)
                removal_rates.append(background_removal)
        cases.append(
            {
                "scenario": name,
                "subject_box": list(subject_box),
                "expected_subject_alpha": subject_alpha,
                "maximum_foreground_alpha_mae": round(max(alpha_errors), 6),
                "minimum_background_removal": round(min(removal_rates), 6),
                "deterministic_pixels": len(set(hashes)) == 1,
                "elapsed_ms": _metric(elapsed),
            }
        )
    return {"case_count": len(cases), "cases": cases}


def _run_alpha_refinement_probe(
    provider: MockProvider,
    removal_revision: AssetRevision,
) -> dict[str, Any]:
    strokes = (
        AlphaBrushStroke("preserve", 0.05, ((0.1, 0.1),)),
        AlphaBrushStroke("remove", 0.05, ((0.5, 0.5),)),
    )
    revision, output_path, elapsed_ms = _submit_edit(
        provider,
        removal_revision,
        operation="refine_alpha",
        parameters=(
            ("operation", "refine_alpha"),
            ("strokes", serialize_alpha_strokes(strokes)),
        ),
        request_id="gate-alpha-refinement",
    )
    with Image.open(output_path) as output:
        output.load()
        preserve_alpha = output.getpixel((round(output.width * 0.1), round(output.height * 0.1)))[3]
        remove_alpha = output.getpixel((round(output.width * 0.5), round(output.height * 0.5)))[3]
    return {
        "preserve_alpha": preserve_alpha,
        "remove_alpha": remove_alpha,
        "source_lineage_matches": revision.provenance.source_revision_ids
        == (removal_revision.revision_id,),
        "elapsed_ms": round(elapsed_ms, 3),
    }


def _run_background_replacement_probe(
    root: Path,
    removal_revision: AssetRevision,
    repetitions: int,
) -> dict[str, Any]:
    foreground_path = _file_uri_path(removal_revision.content_uri)
    with Image.open(foreground_path) as foreground:
        foreground.load()
        size = foreground.size
        with foreground.convert("RGBA") as rgba:
            subject_pixel = rgba.getpixel((rgba.width // 2, rgba.height // 2))

    custom_color = (34, 139, 230)
    custom_path = root / "replacement-background.png"
    with Image.new("RGB", size, custom_color) as custom_background:
        custom_background.save(custom_path, "PNG")
    custom_sha256 = hashlib.sha256(custom_path.read_bytes()).hexdigest()
    definitions = (
        ("solid_color", BackgroundSpec.solid_color("#f2b8b5"), (242, 184, 181), None),
        (
            "custom_image",
            BackgroundSpec.custom_image(custom_path, fallback_color="#ffffff"),
            custom_color,
            custom_sha256,
        ),
    )
    cases: list[dict[str, Any]] = []
    for name, spec, expected_background, expected_sha256 in definitions:
        elapsed: list[float] = []
        hashes: list[str] = []
        modes: list[str] = []
        alpha_extrema: list[list[int]] = []
        background_pixels: list[list[int]] = []
        subject_pixels: list[list[int]] = []
        source_transparency: list[bool] = []
        background_hashes: list[str | None] = []
        for index in range(repetitions):
            output_path = root / f"replacement-{name}-{index}.png"
            started = time.perf_counter()
            composition = compose_background(foreground_path, output_path, spec)
            elapsed.append((time.perf_counter() - started) * 1000.0)
            source_transparency.append(composition.source_had_transparency)
            background_hashes.append(composition.background_sha256)
            with Image.open(output_path) as output:
                output.load()
                with output.convert("RGBA") as rgba:
                    hashes.append(_pixel_hash(rgba))
                    modes.append(output.mode)
                    alpha_channel = rgba.getchannel("A")
                    try:
                        extrema = cast(tuple[int, int], alpha_channel.getextrema())
                    finally:
                        alpha_channel.close()
                    alpha_extrema.append(list(extrema))
                    background_pixels.append(list(rgba.getpixel((0, 0))[:3]))
                    subject_pixels.append(list(rgba.getpixel((rgba.width // 2, rgba.height // 2))))
        cases.append(
            {
                "kind": name,
                "expected_background_pixel": list(expected_background),
                "background_pixels": background_pixels,
                "expected_subject_pixel": list(subject_pixel),
                "subject_pixels": subject_pixels,
                "modes": modes,
                "alpha_extrema": alpha_extrema,
                "source_had_transparency": all(source_transparency),
                "expected_background_sha256": expected_sha256,
                "background_sha256": background_hashes,
                "deterministic_pixels": len(set(hashes)) == 1,
                "elapsed_ms": _metric(elapsed),
            }
        )
    return {"case_count": len(cases), "cases": cases}


def _run_region_repair_probe(
    root: Path,
    provider: MockProvider,
    size: tuple[int, int],
    repetitions: int,
) -> dict[str, Any]:
    width, height = size
    box = (width * 3 // 10, height * 3 // 10, width * 7 // 10, height * 7 // 10)
    region = (box[0] / width, box[1] / height, box[2] / width, box[3] / height)
    source_path = root / "region-repair-source.png"
    expected = Image.new("RGBA", size)
    for y in range(height):
        for x in range(width):
            expected.putpixel(
                (x, y),
                (
                    30 + round(x * 60 / max(1, width - 1)),
                    40 + round(y * 70 / max(1, height - 1)),
                    110,
                    180 + round(x * 60 / max(1, width - 1)),
                ),
            )
    source = expected.copy()
    draw = ImageDraw.Draw(source)
    draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=(245, 25, 35, 255))
    source.save(source_path, "PNG")
    source.close()
    source_revision = _source_revision(source_path, suffix="region_repair")
    expected_pixels = cast(list[tuple[int, int, int, int]], list(expected.getdata()))
    expected.close()
    with Image.open(source_path) as source_image:
        source_image.load()
        with source_image.convert("RGBA") as rgba_source:
            source_pixels = cast(
                list[tuple[int, int, int, int]],
                list(rgba_source.getdata()),
            )

    elapsed: list[float] = []
    hashes: list[str] = []
    region_mae: list[float] = []
    outside_unchanged: list[float] = []
    alpha_mae: list[float] = []
    lineage_matches: list[bool] = []
    for index in range(repetitions):
        revision, output_path, elapsed_ms = _submit_edit(
            provider,
            source_revision,
            operation="erase_region",
            parameters=(
                ("operation", "erase_region"),
                ("region_left", region[0]),
                ("region_top", region[1]),
                ("region_right", region[2]),
                ("region_bottom", region[3]),
                ("feather", 0),
            ),
            request_id=f"gate-region-repair-{index}",
        )
        elapsed.append(elapsed_ms)
        lineage_matches.append(
            revision.provenance.source_revision_ids == (source_revision.revision_id,)
        )
        with Image.open(output_path) as output:
            output.load()
            rgba = output.convert("RGBA")
            try:
                hashes.append(_pixel_hash(rgba))
                output_pixels = cast(
                    list[tuple[int, int, int, int]],
                    list(rgba.getdata()),
                )
            finally:
                rgba.close()
        error_total = 0
        alpha_error_total = 0
        repaired_count = 0
        unchanged_count = 0
        outside_count = 0
        left, top, right, bottom = box
        for y in range(height):
            for x in range(width):
                offset = y * width + x
                output_pixel = output_pixels[offset]
                if left <= x < right and top <= y < bottom:
                    expected_pixel = expected_pixels[offset]
                    error_total += sum(
                        abs(actual - target) for actual, target in zip(output_pixel, expected_pixel)
                    )
                    alpha_error_total += abs(output_pixel[3] - expected_pixel[3])
                    repaired_count += 1
                else:
                    outside_count += 1
                    unchanged_count += output_pixel == source_pixels[offset]
        region_mae.append(error_total / max(1, repaired_count * 4))
        alpha_mae.append(alpha_error_total / max(1, repaired_count))
        outside_unchanged.append(unchanged_count / max(1, outside_count))
    return {
        "region": list(region),
        "maximum_region_mae": round(max(region_mae), 6),
        "maximum_alpha_mae": round(max(alpha_mae), 6),
        "minimum_outside_unchanged": round(min(outside_unchanged), 6),
        "source_lineage_matches": all(lineage_matches),
        "deterministic_pixels": len(set(hashes)) == 1,
        "elapsed_ms": _metric(elapsed),
    }


def _run_mask_repair_probe(
    root: Path,
    provider: MockProvider,
    size: tuple[int, int],
    repetitions: int,
) -> dict[str, Any]:
    width, height = size
    source_path = root / "mask-repair-source.png"
    source = Image.new("RGBA", size, (250, 250, 250, 255))
    draw = ImageDraw.Draw(source)
    retained_box = (width // 12, height // 5, width * 5 // 12, height * 4 // 5)
    selected_box = (width * 2 // 3, height // 3, width * 11 // 12, height * 5 // 6)
    draw.rectangle(retained_box, fill=(36, 95, 210, 255))
    draw.ellipse(selected_box, fill=(220, 60, 80, 255))
    source.save(source_path, "PNG")
    source_pixels = cast(list[tuple[int, int, int, int]], list(source.getdata()))
    source.close()
    suggestions = detect_smart_selection(source_path, padding_ratio=0)
    selected = max(suggestions, key=lambda item: item.region[0])
    if selected.mask_size is None or selected.mask_rle is None:
        raise RuntimeError("智能选区没有生成像素蒙版")
    excluded_point = (
        (selected_box[0] + selected_box[2]) / 2 / width,
        (selected_box[1] + selected_box[3]) / 2 / height,
    )
    refined_mask_rle = refine_selection_mask(
        selected.mask_rle,
        selected.mask_size,
        (SelectionMaskStroke("remove", 0.04, (excluded_point,)),),
    )
    mask = decode_selection_mask(
        refined_mask_rle,
        selected.mask_size,
        target_size=size,
    )
    mask_pixels = cast(list[int], list(mask.getdata()))
    mask.close()
    source_revision = _source_revision(source_path, suffix="mask_repair")

    elapsed: list[float] = []
    hashes: list[str] = []
    selected_change: list[float] = []
    outside_unchanged: list[float] = []
    retained_unchanged: list[float] = []
    lineage_matches: list[bool] = []
    identity_only: list[bool] = []
    manual_exclusion_unchanged: list[float] = []
    for index in range(repetitions):
        revision, output_path, elapsed_ms = _submit_edit(
            provider,
            source_revision,
            operation="erase_mask",
            parameters=(
                ("operation", "erase_mask"),
                ("region_left", selected.region[0]),
                ("region_top", selected.region[1]),
                ("region_right", selected.region[2]),
                ("region_bottom", selected.region[3]),
                ("feather", 0),
                ("mask_width", selected.mask_size[0]),
                ("mask_height", selected.mask_size[1]),
                ("mask_rle", refined_mask_rle),
                ("mask_refined", True),
                ("mask_stroke_count", 1),
                ("mask_point_count", 1),
            ),
            request_id=f"gate-mask-repair-{index}",
        )
        elapsed.append(elapsed_ms)
        lineage_matches.append(
            revision.provenance.source_revision_ids == (source_revision.revision_id,)
        )
        identity_only.append(
            "mask_rle" not in revision.provenance.parameters
            and isinstance(revision.provenance.parameters.get("mask_sha256"), str)
        )
        with Image.open(output_path) as output:
            output.load()
            rgba = output.convert("RGBA")
            try:
                hashes.append(_pixel_hash(rgba))
                output_pixels = cast(
                    list[tuple[int, int, int, int]],
                    list(rgba.getdata()),
                )
            finally:
                rgba.close()
        selected_total = 0
        selected_changed = 0
        outside_total = 0
        outside_same = 0
        retained_total = 0
        retained_same = 0
        for y in range(height):
            for x in range(width):
                offset = y * width + x
                same = output_pixels[offset] == source_pixels[offset]
                if mask_pixels[offset]:
                    selected_total += 1
                    selected_changed += not same
                else:
                    outside_total += 1
                    outside_same += same
                if (
                    retained_box[0] <= x <= retained_box[2]
                    and retained_box[1] <= y <= retained_box[3]
                ):
                    retained_total += 1
                    retained_same += same
        selected_change.append(selected_changed / max(1, selected_total))
        outside_unchanged.append(outside_same / max(1, outside_total))
        retained_unchanged.append(retained_same / max(1, retained_total))
        excluded_x = round(excluded_point[0] * max(1, width - 1))
        excluded_y = round(excluded_point[1] * max(1, height - 1))
        excluded_offset = excluded_y * width + excluded_x
        manual_exclusion_unchanged.append(
            float(output_pixels[excluded_offset] == source_pixels[excluded_offset])
        )
    return {
        "candidate_count": len(suggestions),
        "minimum_selected_change": round(min(selected_change), 6),
        "minimum_outside_mask_unchanged": round(min(outside_unchanged), 6),
        "minimum_unselected_subject_unchanged": round(min(retained_unchanged), 6),
        "minimum_manual_exclusion_unchanged": round(
            min(manual_exclusion_unchanged),
            6,
        ),
        "source_lineage_matches": all(lineage_matches),
        "provenance_stores_identity_only": all(identity_only),
        "deterministic_pixels": len(set(hashes)) == 1,
        "elapsed_ms": _metric(elapsed),
    }


def _run_text_probe(
    root: Path,
    provider: MockProvider,
    size: tuple[int, int],
    repetitions: int,
) -> dict[str, Any]:
    width, height = size
    region = (0.2, 0.3, 0.8, 0.7)
    pixel_region = (
        round(region[0] * width),
        round(region[1] * height),
        round(region[2] * width),
        round(region[3] * height),
    )
    source_path = root / "text-source.png"
    source = Image.new("RGBA", size, (246, 244, 240, 255))
    draw = ImageDraw.Draw(source)
    left, top, right, bottom = pixel_region
    line_height = max(2, (bottom - top) // 8)
    for index in range(3):
        y = top + line_height * (index * 2 + 1)
        draw.rectangle((left + 4, y, right - 5, y + line_height - 1), fill=(8, 8, 8, 255))
    source.save(source_path, "PNG")
    source_revision = _source_revision(source_path, suffix="text")
    draft = TextReplacementDraft(
        region=region,
        text="新标题",
        font_family="微软雅黑",
        font_size=max(12, height // 5),
        color="#c62828",
        align="center",
        rotation=0,
    )
    hashes: list[str] = []
    elapsed: list[float] = []
    changed_ratios: list[float] = []
    unchanged_ratios: list[float] = []
    red_counts: list[int] = []
    old_dark_counts: list[int] = []
    source_pixels = cast(list[tuple[int, int, int, int]], list(source.getdata()))
    try:
        for index in range(repetitions):
            _revision, output_path, elapsed_ms = _submit_edit(
                provider,
                source_revision,
                operation="replace_text",
                parameters=draft.to_parameters(),
                request_id=f"gate-text-{index}",
            )
            elapsed.append(elapsed_ms)
            with Image.open(output_path) as output:
                output.load()
                rgba = output.convert("RGBA")
                try:
                    hashes.append(_pixel_hash(rgba))
                    output_pixels = cast(
                        list[tuple[int, int, int, int]],
                        list(rgba.getdata()),
                    )
                    region_total = 0
                    region_changed = 0
                    outside_total = 0
                    outside_unchanged = 0
                    red_count = 0
                    old_dark_count = 0
                    for y in range(height):
                        for x in range(width):
                            inside = left <= x < right and top <= y < bottom
                            offset = y * width + x
                            current = output_pixels[offset]
                            original = source_pixels[offset]
                            if inside:
                                region_total += 1
                                region_changed += current != original
                                red_count += current[0] > 120 and current[0] > current[1] * 1.5
                                old_dark_count += max(current[:3]) < 40
                            else:
                                outside_total += 1
                                outside_unchanged += current == original
                    changed_ratios.append(region_changed / max(1, region_total))
                    unchanged_ratios.append(outside_unchanged / max(1, outside_total))
                    red_counts.append(red_count)
                    old_dark_counts.append(old_dark_count)
                finally:
                    rgba.close()
    finally:
        source.close()

    vertical_region = (0.35, 0.08, 0.65, 0.92)
    vertical_pixel_region = (
        round(vertical_region[0] * width),
        round(vertical_region[1] * height),
        round(vertical_region[2] * width),
        round(vertical_region[3] * height),
    )
    vertical_left, vertical_top, vertical_right, vertical_bottom = vertical_pixel_region
    vertical_source_path = root / "vertical-text-source.png"
    vertical_source = Image.new("RGBA", size, (246, 244, 240, 255))
    vertical_draw = ImageDraw.Draw(vertical_source)
    line_width = max(2, (vertical_right - vertical_left) // 6)
    for index in range(3):
        x = vertical_left + line_width * (index * 2 + 1)
        vertical_draw.rectangle(
            (x, vertical_top + 4, x + line_width - 1, vertical_bottom - 5),
            fill=(8, 8, 8, 255),
        )
    vertical_source.save(vertical_source_path, "PNG")
    vertical_revision = _source_revision(vertical_source_path, suffix="vertical-text")
    vertical_draft = TextReplacementDraft(
        region=vertical_region,
        text="VERTICAL 2026",
        font_family="微软雅黑",
        font_size=max(
            12,
            min(
                vertical_right - vertical_left,
                (vertical_bottom - vertical_top) // 6,
            ),
        ),
        color="#c62828",
        align="center",
        rotation=90,
    )
    vertical_hashes: list[str] = []
    vertical_elapsed: list[float] = []
    vertical_red_counts: list[int] = []
    vertical_aspect_ratios: list[float] = []
    vertical_unchanged_ratios: list[float] = []
    vertical_source_pixels = cast(
        list[tuple[int, int, int, int]],
        list(vertical_source.getdata()),
    )
    try:
        for index in range(repetitions):
            _revision, output_path, elapsed_ms = _submit_edit(
                provider,
                vertical_revision,
                operation="replace_text",
                parameters=vertical_draft.to_parameters(),
                request_id=f"gate-text-vertical-{index}",
            )
            vertical_elapsed.append(elapsed_ms)
            with Image.open(output_path) as output:
                output.load()
                rgba = output.convert("RGBA")
                try:
                    vertical_hashes.append(_pixel_hash(rgba))
                    output_pixels = cast(
                        list[tuple[int, int, int, int]],
                        list(rgba.getdata()),
                    )
                    red_x: list[int] = []
                    red_y: list[int] = []
                    outside_total = 0
                    outside_unchanged = 0
                    for y in range(height):
                        for x in range(width):
                            inside = (
                                vertical_left <= x < vertical_right
                                and vertical_top <= y < vertical_bottom
                            )
                            offset = y * width + x
                            current = output_pixels[offset]
                            if inside:
                                if current[0] > 120 and current[0] > current[1] * 1.5:
                                    red_x.append(x)
                                    red_y.append(y)
                            else:
                                outside_total += 1
                                outside_unchanged += current == vertical_source_pixels[offset]
                    vertical_red_counts.append(len(red_x))
                    rendered_width = max(red_x) - min(red_x) + 1 if red_x else 0
                    rendered_height = max(red_y) - min(red_y) + 1 if red_y else 0
                    vertical_aspect_ratios.append(rendered_height / max(1, rendered_width))
                    vertical_unchanged_ratios.append(outside_unchanged / max(1, outside_total))
                finally:
                    rgba.close()
    finally:
        vertical_source.close()

    perspective_region = (0.12, 0.20, 0.84, 0.75)
    perspective_quad = (0.16, 0.30, 0.78, 0.20, 0.84, 0.75, 0.12, 0.63)
    perspective_source_path = root / "perspective-text-source.png"
    perspective_source = Image.new("RGBA", size, (246, 244, 240, 255))
    perspective_polygon = [
        (
            perspective_quad[index] * width,
            perspective_quad[index + 1] * height,
        )
        for index in range(0, 8, 2)
    ]
    ImageDraw.Draw(perspective_source).polygon(
        perspective_polygon,
        fill=(8, 8, 8, 255),
    )
    perspective_source.save(perspective_source_path, "PNG")
    perspective_revision = _source_revision(
        perspective_source_path,
        suffix="perspective-text",
    )
    perspective_draft = TextReplacementDraft(
        region=perspective_region,
        text="透视标题 2026",
        font_family="微软雅黑",
        font_size=max(12, height // 9),
        color="#c62828",
        align="center",
        perspective_quad=perspective_quad,
    )
    polygon_mask = Image.new("1", size, 0)
    ImageDraw.Draw(polygon_mask).polygon(perspective_polygon, fill=1)
    polygon_pixels = list(polygon_mask.getdata())
    perspective_source_pixels = cast(
        list[tuple[int, int, int, int]],
        list(perspective_source.getdata()),
    )
    perspective_hashes: list[str] = []
    perspective_elapsed: list[float] = []
    perspective_red_counts: list[int] = []
    perspective_unchanged_ratios: list[float] = []
    try:
        for index in range(repetitions):
            _revision, output_path, elapsed_ms = _submit_edit(
                provider,
                perspective_revision,
                operation="replace_text",
                parameters=perspective_draft.to_parameters(),
                request_id=f"gate-text-perspective-{index}",
            )
            perspective_elapsed.append(elapsed_ms)
            with Image.open(output_path) as output:
                output.load()
                rgba = output.convert("RGBA")
                try:
                    perspective_hashes.append(_pixel_hash(rgba))
                    output_pixels = cast(
                        list[tuple[int, int, int, int]],
                        list(rgba.getdata()),
                    )
                    perspective_red_counts.append(
                        sum(
                            current[0] > 120 and current[0] > current[1] * 1.5
                            for current, inside in zip(
                                output_pixels,
                                polygon_pixels,
                                strict=True,
                            )
                            if inside
                        )
                    )
                    outside_total = 0
                    outside_unchanged = 0
                    for current, original, inside in zip(
                        output_pixels,
                        perspective_source_pixels,
                        polygon_pixels,
                        strict=True,
                    ):
                        if inside:
                            continue
                        outside_total += 1
                        outside_unchanged += current == original
                    perspective_unchanged_ratios.append(outside_unchanged / max(1, outside_total))
                finally:
                    rgba.close()
    finally:
        polygon_mask.close()
        perspective_source.close()

    curved_region = (0.08, 0.12, 0.92, 0.88)
    curved_pixel_region = (
        round(curved_region[0] * width),
        round(curved_region[1] * height),
        round(curved_region[2] * width),
        round(curved_region[3] * height),
    )
    curved_left, curved_top, curved_right, curved_bottom = curved_pixel_region
    curved_source_path = root / "curved-text-source.png"
    curved_source = Image.new("RGBA", size, (246, 244, 240, 255))
    curved_source.save(curved_source_path, "PNG")
    curved_revision = _source_revision(curved_source_path, suffix="curved-text")
    curved_draft = TextReplacementDraft(
        region=curved_region,
        text="HHHHHHH",
        font_family="微软雅黑",
        font_size=max(12, height // 5),
        color="#c62828",
        align="center",
        curve=0.8,
    )
    curved_source_pixels = cast(
        list[tuple[int, int, int, int]],
        list(curved_source.getdata()),
    )
    curved_hashes: list[str] = []
    curved_elapsed: list[float] = []
    curved_red_counts: list[int] = []
    curved_center_deltas: list[float] = []
    curved_unchanged_ratios: list[float] = []
    try:
        for index in range(repetitions):
            _revision, output_path, elapsed_ms = _submit_edit(
                provider,
                curved_revision,
                operation="replace_text",
                parameters=curved_draft.to_parameters(),
                request_id=f"gate-text-curved-{index}",
            )
            curved_elapsed.append(elapsed_ms)
            with Image.open(output_path) as output:
                output.load()
                rgba = output.convert("RGBA")
                try:
                    curved_hashes.append(_pixel_hash(rgba))
                    output_pixels = cast(
                        list[tuple[int, int, int, int]],
                        list(rgba.getdata()),
                    )
                    red_pixels: list[tuple[int, int]] = []
                    outside_total = 0
                    outside_unchanged = 0
                    for y in range(height):
                        for x in range(width):
                            offset = y * width + x
                            current = output_pixels[offset]
                            inside = (
                                curved_left <= x < curved_right and curved_top <= y < curved_bottom
                            )
                            if inside:
                                if current[0] > 120 and current[0] > current[1] * 1.5:
                                    red_pixels.append((x, y))
                            else:
                                outside_total += 1
                                outside_unchanged += current == curved_source_pixels[offset]
                    curved_red_counts.append(len(red_pixels))
                    curved_unchanged_ratios.append(outside_unchanged / max(1, outside_total))
                    if red_pixels:
                        minimum_x = min(x for x, _y in red_pixels)
                        maximum_x = max(x for x, _y in red_pixels)
                        span = maximum_x - minimum_x + 1
                        edge_y = [
                            y
                            for x, y in red_pixels
                            if x < minimum_x + span * 0.22 or x > maximum_x - span * 0.22
                        ]
                        center_y = [
                            y
                            for x, y in red_pixels
                            if minimum_x + span * 0.40 <= x <= minimum_x + span * 0.60
                        ]
                        center_delta = sum(edge_y) / max(1, len(edge_y)) - sum(center_y) / max(
                            1, len(center_y)
                        )
                    else:
                        center_delta = 0.0
                    curved_center_deltas.append(center_delta)
                finally:
                    rgba.close()
    finally:
        curved_source.close()

    styled_replacement_cases: list[dict[str, Any]] = []
    styled_size = (480, 200)
    styled_region = (0.08, 0.15, 0.92, 0.85)
    styled_pixel_region = (
        round(styled_region[0] * styled_size[0]),
        round(styled_region[1] * styled_size[1]),
        round(styled_region[2] * styled_size[0]),
        round(styled_region[3] * styled_size[1]),
    )
    styled_background = (246, 244, 240, 255)
    styled_source_path = root / "styled-text-replacement-source.png"
    styled_source = Image.new("RGBA", styled_size, styled_background)
    styled_source.save(styled_source_path, "PNG")
    styled_revision = _source_revision(styled_source_path, suffix="styled-text-replacement")
    styled_source_pixels = cast(
        list[tuple[int, int, int, int]],
        list(styled_source.getdata()),
    )
    try:
        for visual_effect in ("plain", "outline", "shadow", "gradient", "glow"):
            styled_draft = TextReplacementDraft(
                region=styled_region,
                text="EFFECT 2026",
                font_family="微软雅黑",
                font_size=56,
                color="#c62828",
                align="center",
                visual_effect=visual_effect,  # type: ignore[arg-type]
                effect_color="#1565c0",
                effect_size=6,
            )
            case_hashes: list[str] = []
            case_elapsed: list[float] = []
            primary_counts: list[int] = []
            secondary_counts: list[int] = []
            secondary_offsets: list[float] = []
            halo_counts: list[int] = []
            outside_unchanged_ratios: list[float] = []
            provenance_matches: list[bool] = []
            for index in range(repetitions):
                revision, output_path, elapsed_ms = _submit_edit(
                    provider,
                    styled_revision,
                    operation="replace_text",
                    parameters=styled_draft.to_parameters(),
                    request_id=f"gate-text-styled-{visual_effect}-{index}",
                )
                case_elapsed.append(elapsed_ms)
                provenance_matches.append(
                    revision.provenance.parameters.get("visual_effect") == visual_effect
                    and revision.provenance.parameters.get("effect_color") == "#1565c0"
                    and revision.provenance.parameters.get("effect_size") == 6
                )
                with Image.open(output_path) as output:
                    output.load()
                    rgba = output.convert("RGBA")
                    try:
                        case_hashes.append(_pixel_hash(rgba))
                        pixels = cast(
                            list[tuple[int, int, int, int]],
                            list(rgba.getdata()),
                        )
                        primary_y: list[int] = []
                        secondary_y: list[int] = []
                        primary_x: list[int] = []
                        secondary_x: list[int] = []
                        outside_total = 0
                        outside_same = 0
                        styled_left, styled_top, styled_right, styled_bottom = styled_pixel_region
                        for y in range(styled_size[1]):
                            for x in range(styled_size[0]):
                                offset = y * styled_size[0] + x
                                pixel = pixels[offset]
                                inside = (
                                    styled_left <= x < styled_right
                                    and styled_top <= y < styled_bottom
                                )
                                if not inside:
                                    outside_total += 1
                                    outside_same += pixel == styled_source_pixels[offset]
                                    continue
                                if pixel[0] > 120 and pixel[0] > pixel[2] * 1.25:
                                    primary_x.append(x)
                                    primary_y.append(y)
                                if pixel[2] > 100 and pixel[2] > pixel[0] * 1.20:
                                    secondary_x.append(x)
                                    secondary_y.append(y)
                        primary_counts.append(len(primary_y))
                        secondary_counts.append(len(secondary_y))
                        secondary_offsets.append(
                            0.0
                            if not primary_y or not secondary_y
                            else sum(secondary_y) / len(secondary_y)
                            - sum(primary_y) / len(primary_y)
                        )
                        halo_counts.append(
                            0
                            if not primary_x or not primary_y
                            else sum(
                                x < min(primary_x)
                                or x > max(primary_x)
                                or y < min(primary_y)
                                or y > max(primary_y)
                                for x, y in zip(secondary_x, secondary_y, strict=True)
                            )
                        )
                        outside_unchanged_ratios.append(outside_same / max(1, outside_total))
                    finally:
                        rgba.close()
            styled_replacement_cases.append(
                {
                    "visual_effect": visual_effect,
                    "minimum_primary_pixels": min(primary_counts),
                    "minimum_secondary_pixels": min(secondary_counts),
                    "minimum_secondary_vertical_offset": round(
                        min(secondary_offsets),
                        6,
                    ),
                    "minimum_halo_pixels": min(halo_counts),
                    "minimum_outside_unchanged": round(
                        min(outside_unchanged_ratios),
                        6,
                    ),
                    "provenance_matches": all(provenance_matches),
                    "deterministic_pixels": len(set(case_hashes)) == 1,
                    "elapsed_ms": _metric(case_elapsed),
                }
            )
    finally:
        styled_source.close()
    return {
        "region": list(region),
        "minimum_region_change": round(min(changed_ratios), 6),
        "minimum_outside_unchanged": round(min(unchanged_ratios), 6),
        "minimum_red_pixels": min(red_counts),
        "maximum_old_dark_pixels": max(old_dark_counts),
        "deterministic_pixels": len(set(hashes)) == 1,
        "elapsed_ms": _metric(elapsed),
        "vertical": {
            "region": list(vertical_region),
            "minimum_red_pixels": min(vertical_red_counts),
            "minimum_aspect_ratio": round(min(vertical_aspect_ratios), 6),
            "minimum_outside_unchanged": round(min(vertical_unchanged_ratios), 6),
            "deterministic_pixels": len(set(vertical_hashes)) == 1,
            "elapsed_ms": _metric(vertical_elapsed),
        },
        "perspective": {
            "region": list(perspective_region),
            "quad": list(perspective_quad),
            "minimum_red_pixels": min(perspective_red_counts),
            "minimum_outside_quad_unchanged": round(
                min(perspective_unchanged_ratios),
                6,
            ),
            "deterministic_pixels": len(set(perspective_hashes)) == 1,
            "elapsed_ms": _metric(perspective_elapsed),
        },
        "curved": {
            "region": list(curved_region),
            "curve": curved_draft.curve,
            "minimum_red_pixels": min(curved_red_counts),
            "minimum_center_delta": round(min(curved_center_deltas), 6),
            "minimum_outside_unchanged": round(
                min(curved_unchanged_ratios),
                6,
            ),
            "deterministic_pixels": len(set(curved_hashes)) == 1,
            "elapsed_ms": _metric(curved_elapsed),
        },
        "styled": {
            "case_count": len(styled_replacement_cases),
            "cases": styled_replacement_cases,
        },
    }


def _text_detection_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size)


def _region_overlap(
    actual: tuple[float, float, float, float],
    expected: tuple[float, float, float, float],
) -> float:
    width = max(0.0, min(actual[2], expected[2]) - max(actual[0], expected[0]))
    height = max(0.0, min(actual[3], expected[3]) - max(actual[1], expected[1]))
    intersection = width * height
    actual_area = (actual[2] - actual[0]) * (actual[3] - actual[1])
    return intersection / max(actual_area, 1e-12)


def _region_intersection_over_union(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = width * height
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / max(first_area + second_area - intersection, 1e-12)


def _hex_color_error(actual: str | None, expected: str) -> int:
    if actual is None or len(actual) != 7 or not actual.startswith("#"):
        return 255
    try:
        return max(
            abs(int(actual[index : index + 2], 16) - int(expected[index : index + 2], 16))
            for index in (1, 3, 5)
        )
    except ValueError:
        return 255


def _smooth_textured_background(size: tuple[int, int]) -> Image.Image:
    width, height = size
    y_grid, x_grid = np.mgrid[0:height, 0:width]
    x_normalized = x_grid / max(1, width - 1)
    y_normalized = y_grid / max(1, height - 1)
    texture = 7 * np.sin(x_grid / 55) + 5 * np.cos(y_grid / 31)
    channels = np.stack(
        (
            205 + 35 * x_normalized - 28 * y_normalized + texture,
            215 - 30 * x_normalized + 18 * y_normalized + texture * 0.7,
            225 - 45 * x_normalized - 10 * y_normalized + texture * 0.4,
        ),
        axis=2,
    )
    return Image.fromarray(np.clip(np.rint(channels), 0, 255).astype(np.uint8))


def _structured_textured_background(size: tuple[int, int]) -> Image.Image:
    width, height = size
    y_grid, x_grid = np.mgrid[0:height, 0:width]
    amplitude = 12
    texture = (
        amplitude * np.sin(x_grid / 4.7)
        + amplitude * 0.7 * np.cos(y_grid / 6.3)
        + amplitude * 0.45 * np.sin((x_grid + y_grid) / 9.5)
    )
    structure = np.where(x_grid < width // 2, -18, 18) + np.where(y_grid < 118, 9, -9)
    channels = np.stack(
        (
            178
            + 18 * x_grid / max(1, width - 1)
            - 12 * y_grid / max(1, height - 1)
            + texture
            + structure,
            196
            - 15 * x_grid / max(1, width - 1)
            + 10 * y_grid / max(1, height - 1)
            + texture * 0.65
            + structure * 0.45,
            208
            - 22 * x_grid / max(1, width - 1)
            - 8 * y_grid / max(1, height - 1)
            + texture * 0.35
            - structure * 0.25,
        ),
        axis=2,
    )
    return Image.fromarray(np.clip(np.rint(channels), 0, 255).astype(np.uint8))


def _photo_like_textured_background(
    size: tuple[int, int],
    *,
    seed: int = 20260806,
) -> Image.Image:
    width, height = size
    rng = np.random.default_rng(seed)
    y_grid, x_grid = np.mgrid[0:height, 0:width]
    x_normalized = x_grid / max(1, width - 1)
    y_normalized = y_grid / max(1, height - 1)
    background = np.stack(
        (
            145 + 35 * x_normalized - 18 * y_normalized,
            165 - 22 * x_normalized + 20 * y_normalized,
            178 - 30 * x_normalized - 10 * y_normalized,
        ),
        axis=2,
    )
    coarse_noise = (rng.random((18, 48, 3)) * 255).astype(np.uint8)
    coarse_image = Image.fromarray(coarse_noise).resize(size, Image.Resampling.BICUBIC)
    try:
        coarse_texture = np.asarray(coarse_image, dtype=np.float64) / 255 - 0.5
    finally:
        coarse_image.close()
    fine_texture = rng.random((height, width, 1)) - 0.5
    pixels = np.clip(
        background + 8 * (coarse_texture * 0.75 + fine_texture * 0.25),
        0,
        255,
    ).astype(np.uint8)
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image, "RGBA")
    for index in range(8):
        start_x = int(rng.integers(-50, width // 2))
        start_y = int(rng.integers(0, height))
        points = [
            (
                start_x + step * int(rng.integers(35, 75)),
                start_y + int(rng.integers(-42, 43)),
            )
            for step in range(6)
        ]
        color = (55, 92, 58, 105) if index % 2 == 0 else (105, 72, 45, 95)
        draw.line(
            points,
            fill=color,
            width=int(rng.integers(1, 5)),
            joint="curve",
        )
    return image


def _run_photo_like_text_detection_probe(
    root: Path,
    *,
    size: tuple[int, int],
    expected_region: tuple[float, float, float, float],
    repetitions: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[float]]:
    seeds = (20260806, 20260808, 20260811, 20260813)
    expectations = {
        "plain": ("#111820", None, None),
        "glow": ("#f8f8ff", "#20b8ff", 7),
    }
    cases: list[dict[str, Any]] = []
    negative_cases: list[dict[str, Any]] = []
    all_elapsed: list[float] = []
    for seed in seeds:
        negative_path = root / f"text-detection-photo-negative-{seed}.png"
        negative_source = _photo_like_textured_background(size, seed=seed)
        negative_source.save(negative_path, "PNG")
        negative_source.close()
        negative_runs = []
        negative_elapsed: list[float] = []
        for _repetition in range(repetitions):
            started = time.perf_counter()
            suggestions = detect_text_regions(negative_path, max_suggestions=5)
            duration_ms = (time.perf_counter() - started) * 1_000
            negative_elapsed.append(duration_ms)
            all_elapsed.append(duration_ms)
            negative_runs.append(suggestions)
        negative_cases.append(
            {
                "seed": seed,
                "candidate_count": len(negative_runs[0]),
                "deterministic": all(run == negative_runs[0] for run in negative_runs[1:]),
                "elapsed_ms": _metric(negative_elapsed),
            }
        )

        for expected_effect, (
            expected_fill_color,
            expected_effect_color,
            expected_effect_size,
        ) in expectations.items():
            path = root / f"text-detection-photo-{seed}-{expected_effect}.png"
            source = _photo_like_textured_background(size, seed=seed)
            if expected_effect == "plain":
                ImageDraw.Draw(source).text(
                    (70, 80),
                    "PHOTO TEXT 2026",
                    font=_text_detection_font(52),
                    fill=expected_fill_color,
                )
            else:
                mask = Image.new("L", size, 0)
                ImageDraw.Draw(mask).text(
                    (70, 80),
                    "PHOTO TEXT 2026",
                    font=_text_detection_font(52),
                    fill=255,
                )
                blurred = mask.filter(
                    ImageFilter.GaussianBlur(float(cast(int, expected_effect_size)))
                )
                boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
                glow = Image.new(
                    "RGBA",
                    size,
                    ImageColor.getcolor(expected_effect_color, "RGBA"),
                )
                try:
                    glow.putalpha(boosted)
                    rgba_source = source.convert("RGBA")
                    try:
                        rgba_source.alpha_composite(glow)
                        ImageDraw.Draw(rgba_source).text(
                            (70, 80),
                            "PHOTO TEXT 2026",
                            font=_text_detection_font(52),
                            fill=expected_fill_color,
                        )
                        rendered_source = rgba_source.convert("RGB")
                        source.close()
                        source = rendered_source
                    finally:
                        rgba_source.close()
                finally:
                    mask.close()
                    blurred.close()
                    boosted.close()
                    glow.close()
            source.save(path, "PNG")
            source.close()

            runs = []
            elapsed: list[float] = []
            for _repetition in range(repetitions):
                started = time.perf_counter()
                suggestions = detect_text_regions(path, max_suggestions=5)
                duration_ms = (time.perf_counter() - started) * 1_000
                elapsed.append(duration_ms)
                all_elapsed.append(duration_ms)
                runs.append(suggestions)
            best = runs[0][0] if runs[0] else None
            top_ranked_match = bool(
                best is not None
                and abs(best.rotation) <= 1
                and best.visual_effect == expected_effect
                and _region_overlap(best.region, expected_region) >= 0.85
            )
            cases.append(
                {
                    "seed": seed,
                    "expected_effect": expected_effect,
                    "detected": best is not None,
                    "top_ranked_match": top_ranked_match,
                    "detected_effect": None if best is None else best.visual_effect,
                    "rotation": None if best is None else best.rotation,
                    "estimated_fill_color": None if best is None else best.estimated_fill_color,
                    "fill_color_error": (
                        255
                        if best is None
                        else _hex_color_error(best.estimated_fill_color, expected_fill_color)
                    ),
                    "expected_effect_color": expected_effect_color,
                    "estimated_effect_color": (
                        None if best is None else best.estimated_effect_color
                    ),
                    "effect_color_error": (
                        0
                        if expected_effect_color is None
                        and best is not None
                        and best.estimated_effect_color is None
                        else (
                            255
                            if expected_effect_color is None or best is None
                            else _hex_color_error(
                                best.estimated_effect_color,
                                expected_effect_color,
                            )
                        )
                    ),
                    "expected_effect_size": expected_effect_size,
                    "estimated_effect_size": (None if best is None else best.estimated_effect_size),
                    "effect_size_error": (
                        0
                        if expected_effect_size is None
                        and best is not None
                        and best.estimated_effect_size is None
                        else (
                            65
                            if expected_effect_size is None
                            or best is None
                            or best.estimated_effect_size is None
                            else abs(best.estimated_effect_size - expected_effect_size)
                        )
                    ),
                    "overlap": (
                        0.0
                        if best is None
                        else round(_region_overlap(best.region, expected_region), 6)
                    ),
                    "region_width": (
                        1.0 if best is None else round(best.region[2] - best.region[0], 6)
                    ),
                    "region_height": (
                        1.0 if best is None else round(best.region[3] - best.region[1], 6)
                    ),
                    "deterministic": all(run == runs[0] for run in runs[1:]),
                    "elapsed_ms": _metric(elapsed),
                }
            )
    return cases, negative_cases, all_elapsed


def _run_photo_like_layout_probe(
    root: Path,
    *,
    size: tuple[int, int],
    repetitions: int,
) -> tuple[list[dict[str, Any]], list[float]]:
    layouts = (
        ("small", 20260806, ((0.06, 0.28, 0.85, 0.75),)),
        ("top", 20260808, ((0.03, 0.01, 0.88, 0.48),)),
        ("bottom", 20260811, ((0.03, 0.52, 0.88, 0.99),)),
        ("occluded", 20260813, ((0.04, 0.24, 0.92, 0.78),)),
        (
            "multiline",
            20260810,
            ((0.04, 0.08, 0.75, 0.45), (0.04, 0.48, 0.75, 0.82)),
        ),
    )
    cases: list[dict[str, Any]] = []
    all_elapsed: list[float] = []
    for layout, seed, expected_regions in layouts:
        path = root / f"text-detection-photo-layout-{layout}.png"
        source = _photo_like_textured_background(size, seed=seed)
        draw = ImageDraw.Draw(source)
        if layout == "small":
            draw.text((80, 92), "SMALL PHOTO 2026", font=_text_detection_font(34), fill="#111820")
        elif layout == "top":
            draw.text((60, 24), "TOP PHOTO 2026", font=_text_detection_font(42), fill="#111820")
        elif layout == "bottom":
            draw.text(
                (60, 158),
                "BOTTOM PHOTO 2026",
                font=_text_detection_font(42),
                fill="#111820",
            )
        elif layout == "occluded":
            draw.text(
                (70, 80),
                "OCCLUDED PHOTO 2026",
                font=_text_detection_font(52),
                fill="#111820",
            )
            for left in (155, 255, 355, 455, 555):
                draw.ellipse((left, 96, left + 24, 117), fill=(75, 118, 70))
            draw.line((125, 112, 620, 99), fill=(110, 72, 48), width=4)
        else:
            draw.text(
                (70, 36),
                "FIRST PHOTO LINE",
                font=_text_detection_font(34),
                fill="#111820",
            )
            draw.text(
                (70, 135),
                "SECOND PHOTO LINE",
                font=_text_detection_font(34),
                fill="#111820",
            )
        source.save(path, "PNG")
        source.close()

        runs = []
        elapsed: list[float] = []
        for _repetition in range(repetitions):
            started = time.perf_counter()
            suggestions = detect_text_regions(path, max_suggestions=8)
            duration_ms = (time.perf_counter() - started) * 1_000
            elapsed.append(duration_ms)
            all_elapsed.append(duration_ms)
            runs.append(suggestions)
        suggestions = runs[0]
        matches = []
        overlaps = []
        for expected_region in expected_regions:
            matching = [
                item
                for item in suggestions
                if abs(item.rotation) <= 1
                and item.visual_effect == "plain"
                and _region_overlap(item.region, expected_region) >= 0.85
            ]
            best = max(matching, key=lambda item: item.confidence, default=None)
            matches.append(best)
            overlaps.append(0.0 if best is None else _region_overlap(best.region, expected_region))
        matched = [item for item in matches if item is not None]
        matched_positions = [-1 if item is None else suggestions.index(item) for item in matches]
        reading_order = all(
            matched_positions[index] >= 0
            and matched_positions[index] < matched_positions[index + 1]
            for index in range(len(matched_positions) - 1)
        )
        first = suggestions[0] if suggestions else None
        cases.append(
            {
                "layout": layout,
                "required_match_count": len(expected_regions),
                "matched_count": len(matched),
                "top_ranked_match": bool(
                    first is not None and matches[0] is not None and first == matches[0]
                ),
                "reading_order": reading_order,
                "minimum_overlap": round(min(overlaps, default=0.0), 6),
                "maximum_fill_color_error": max(
                    (_hex_color_error(item.estimated_fill_color, "#111820") for item in matched),
                    default=255,
                ),
                "deterministic": all(run == runs[0] for run in runs[1:]),
                "elapsed_ms": _metric(elapsed),
            }
        )
    return cases, all_elapsed


def _run_jpeg_photo_text_detection_probe(
    root: Path,
    *,
    size: tuple[int, int],
    repetitions: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[float]]:
    qualities = (95, 75, 50)
    negative_seeds = (20260806, 20260808, 20260811, 20260813)
    layouts = (
        ("plain", 20260806, "plain", ((0.05, 0.25, 0.92, 0.75),)),
        ("glow", 20260806, "glow", ((0.05, 0.25, 0.92, 0.75),)),
        ("small", 20260806, "plain", ((0.06, 0.28, 0.85, 0.75),)),
        (
            "multiline",
            20260810,
            "plain",
            ((0.04, 0.08, 0.75, 0.45), (0.04, 0.48, 0.75, 0.82)),
        ),
        ("occluded", 20260813, "plain", ((0.04, 0.24, 0.92, 0.78),)),
    )
    cases: list[dict[str, Any]] = []
    negative_cases: list[dict[str, Any]] = []
    all_elapsed: list[float] = []
    for quality in qualities:
        for seed in negative_seeds:
            negative_path = root / f"text-detection-jpeg-negative-{quality}-{seed}.jpg"
            negative = _photo_like_textured_background(size, seed=seed)
            negative.save(
                negative_path,
                "JPEG",
                quality=quality,
                subsampling=2,
            )
            negative.close()
            runs = []
            elapsed: list[float] = []
            for _repetition in range(repetitions):
                started = time.perf_counter()
                suggestions = detect_text_regions(negative_path, max_suggestions=8)
                duration_ms = (time.perf_counter() - started) * 1_000
                elapsed.append(duration_ms)
                all_elapsed.append(duration_ms)
                runs.append(suggestions)
            negative_cases.append(
                {
                    "quality": quality,
                    "seed": seed,
                    "candidate_count": len(runs[0]),
                    "deterministic": all(run == runs[0] for run in runs[1:]),
                    "elapsed_ms": _metric(elapsed),
                }
            )

        for layout, seed, expected_effect, expected_regions in layouts:
            path = root / f"text-detection-jpeg-{quality}-{layout}.jpg"
            source = _photo_like_textured_background(size, seed=seed)
            draw = ImageDraw.Draw(source)
            if layout == "plain":
                draw.text(
                    (70, 80),
                    "PHOTO TEXT 2026",
                    font=_text_detection_font(52),
                    fill="#111820",
                )
            elif layout == "glow":
                mask = Image.new("L", size, 0)
                ImageDraw.Draw(mask).text(
                    (70, 80),
                    "PHOTO TEXT 2026",
                    font=_text_detection_font(52),
                    fill=255,
                )
                blurred = mask.filter(ImageFilter.GaussianBlur(7))
                boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
                glow = Image.new(
                    "RGBA",
                    size,
                    ImageColor.getcolor("#20b8ff", "RGBA"),
                )
                rgba = source.convert("RGBA")
                try:
                    glow.putalpha(boosted)
                    rgba.alpha_composite(glow)
                    ImageDraw.Draw(rgba).text(
                        (70, 80),
                        "PHOTO TEXT 2026",
                        font=_text_detection_font(52),
                        fill="#f8f8ff",
                    )
                    rendered = rgba.convert("RGB")
                    source.close()
                    source = rendered
                finally:
                    mask.close()
                    blurred.close()
                    boosted.close()
                    glow.close()
                    rgba.close()
            elif layout == "small":
                draw.text(
                    (80, 92),
                    "SMALL PHOTO 2026",
                    font=_text_detection_font(34),
                    fill="#111820",
                )
            elif layout == "multiline":
                draw.text(
                    (70, 36),
                    "FIRST PHOTO LINE",
                    font=_text_detection_font(34),
                    fill="#111820",
                )
                draw.text(
                    (70, 135),
                    "SECOND PHOTO LINE",
                    font=_text_detection_font(34),
                    fill="#111820",
                )
            else:
                draw.text(
                    (70, 80),
                    "OCCLUDED PHOTO 2026",
                    font=_text_detection_font(52),
                    fill="#111820",
                )
                for left in (155, 255, 355, 455, 555):
                    draw.ellipse((left, 96, left + 24, 117), fill=(75, 118, 70))
                draw.line((125, 112, 620, 99), fill=(110, 72, 48), width=4)
            source.save(path, "JPEG", quality=quality, subsampling=2)
            source.close()

            runs = []
            elapsed = []
            for _repetition in range(repetitions):
                started = time.perf_counter()
                suggestions = detect_text_regions(path, max_suggestions=8)
                duration_ms = (time.perf_counter() - started) * 1_000
                elapsed.append(duration_ms)
                all_elapsed.append(duration_ms)
                runs.append(suggestions)
            suggestions = runs[0]
            matches = []
            overlaps = []
            for expected_region in expected_regions:
                matching = [
                    item
                    for item in suggestions
                    if abs(item.rotation) <= 1
                    and item.visual_effect == expected_effect
                    and _region_overlap(item.region, expected_region) >= 0.85
                ]
                best = max(matching, key=lambda item: item.confidence, default=None)
                matches.append(best)
                overlaps.append(
                    0.0 if best is None else _region_overlap(best.region, expected_region)
                )
            matched = [item for item in matches if item is not None]
            positions = [-1 if item is None else suggestions.index(item) for item in matches]
            expected_fill = "#f8f8ff" if expected_effect == "glow" else "#111820"
            cases.append(
                {
                    "quality": quality,
                    "layout": layout,
                    "expected_effect": expected_effect,
                    "required_match_count": len(expected_regions),
                    "matched_count": len(matched),
                    "top_ranked_match": bool(
                        suggestions and matches[0] is not None and suggestions[0] == matches[0]
                    ),
                    "reading_order": all(
                        positions[index] >= 0 and positions[index] < positions[index + 1]
                        for index in range(len(positions) - 1)
                    ),
                    "minimum_overlap": round(min(overlaps, default=0.0), 6),
                    "maximum_fill_color_error": max(
                        (
                            _hex_color_error(item.estimated_fill_color, expected_fill)
                            for item in matched
                        ),
                        default=255,
                    ),
                    "maximum_effect_color_error": max(
                        (
                            0
                            if expected_effect == "plain" and item.estimated_effect_color is None
                            else _hex_color_error(
                                item.estimated_effect_color,
                                "#20b8ff",
                            )
                            for item in matched
                        ),
                        default=255,
                    ),
                    "maximum_effect_size_error": max(
                        (
                            0
                            if expected_effect == "plain" and item.estimated_effect_size is None
                            else (
                                65
                                if item.estimated_effect_size is None
                                else abs(item.estimated_effect_size - 7)
                            )
                            for item in matched
                        ),
                        default=65,
                    ),
                    "deterministic": all(run == runs[0] for run in runs[1:]),
                    "elapsed_ms": _metric(elapsed),
                }
            )
    return cases, negative_cases, all_elapsed


def _run_text_detection_probe(
    root: Path,
    size: tuple[int, int],
    repetitions: int,
) -> dict[str, Any]:
    canvas_size = (max(360, size[0]), max(240, size[1]))
    cases: list[dict[str, Any]] = []
    all_elapsed: list[float] = []
    detection_angles = (0.0, 17.0, 30.0, -30.0, 58.0, 80.0, -83.0, 90.0)
    for index, expected_angle in enumerate(detection_angles):
        source_path = root / f"text-detection-{index}.png"
        source = Image.new("RGB", canvas_size, "#f7f5f0")
        layer = Image.new("RGBA", (300, 72), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        draw.text(
            (8, 8),
            "旋转文字 TEXT 2026",
            font=_text_detection_font(32),
            fill="#202020",
        )
        rotated = layer.rotate(
            expected_angle,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )
        paste_left = max(8, (canvas_size[0] - rotated.width) // 2)
        paste_top = max(8, (canvas_size[1] - rotated.height) // 2)
        source.paste(rotated, (paste_left, paste_top), rotated)
        expected_region = (
            paste_left / canvas_size[0],
            paste_top / canvas_size[1],
            (paste_left + rotated.width) / canvas_size[0],
            (paste_top + rotated.height) / canvas_size[1],
        )
        source.save(source_path, "PNG")
        source.close()
        layer.close()
        rotated.close()

        runs = []
        elapsed: list[float] = []
        for _repetition in range(repetitions):
            started = time.perf_counter()
            suggestions = detect_text_regions(source_path, max_suggestions=8)
            duration_ms = (time.perf_counter() - started) * 1_000
            elapsed.append(duration_ms)
            all_elapsed.append(duration_ms)
            runs.append(suggestions)
        best = max(
            (
                suggestion
                for suggestion in runs[0]
                if abs(suggestion.rotation - expected_angle) <= 1
            ),
            key=lambda suggestion: _region_overlap(
                suggestion.region,
                expected_region,
            ),
            default=None,
        )
        cases.append(
            {
                "expected_angle": expected_angle,
                "detected": best is not None,
                "detected_angle": None if best is None else best.rotation,
                "angle_error": (None if best is None else abs(best.rotation - expected_angle)),
                "detected_curve": None if best is None else best.curve,
                "curve_error": None if best is None else abs(best.curve),
                "visual_effect": None if best is None else best.visual_effect,
                "estimated_fill_color": (None if best is None else best.estimated_fill_color),
                "fill_color_error": (
                    255 if best is None else _hex_color_error(best.estimated_fill_color, "#202020")
                ),
                "overlap": (
                    0.0 if best is None else round(_region_overlap(best.region, expected_region), 6)
                ),
                "deterministic": all(run == runs[0] for run in runs[1:]),
                "elapsed_ms": _metric(elapsed),
            }
        )
    curved_detection_cases: list[dict[str, Any]] = []
    for index, expected_curve in enumerate((0.0, 0.25, 0.45, 0.75, -0.45, -0.75)):
        source_path = root / f"text-detection-curved-{index}.png"
        source = Image.new("RGBA", canvas_size, "white")
        layer_width = round(canvas_size[0] * 0.85)
        layer_height = round(canvas_size[1] * 0.66)
        layer = Image.new("RGBA", (layer_width, layer_height), "white")
        _draw_replacement_text(
            layer,
            text="弧形文字弧形文字",
            font_family="微软雅黑",
            font_size=max(28, round(layer_height * 0.30)),
            color="#202020",
            align="center",
            curve=expected_curve,
        )
        paste_left = (canvas_size[0] - layer_width) // 2
        paste_top = (canvas_size[1] - layer_height) // 2
        source.alpha_composite(layer, (paste_left, paste_top))
        expected_region = (
            paste_left / canvas_size[0],
            paste_top / canvas_size[1],
            (paste_left + layer_width) / canvas_size[0],
            (paste_top + layer_height) / canvas_size[1],
        )
        rendered = source.convert("RGB")
        rendered.save(source_path, "PNG")
        rendered.close()
        source.close()
        layer.close()

        runs = []
        curved_case_elapsed: list[float] = []
        for _repetition in range(repetitions):
            started = time.perf_counter()
            suggestions = detect_text_regions(source_path, max_suggestions=20)
            duration_ms = (time.perf_counter() - started) * 1_000
            curved_case_elapsed.append(duration_ms)
            all_elapsed.append(duration_ms)
            runs.append(suggestions)
        candidates = [
            suggestion
            for suggestion in runs[0]
            if abs(suggestion.rotation) <= 1
            and suggestion.perspective_quad is None
            and _region_overlap(suggestion.region, expected_region) >= 0.85
        ]
        best = min(
            candidates,
            key=lambda suggestion: abs(suggestion.curve - expected_curve),
            default=None,
        )
        curved_detection_cases.append(
            {
                "expected_curve": expected_curve,
                "detected": best is not None,
                "detected_curve": None if best is None else best.curve,
                "curve_error": (None if best is None else abs(best.curve - expected_curve)),
                "overlap": (
                    0.0 if best is None else round(_region_overlap(best.region, expected_region), 6)
                ),
                "deterministic": all(run == runs[0] for run in runs[1:]),
                "elapsed_ms": _metric(curved_case_elapsed),
            }
        )

    styled_detection_cases: list[dict[str, Any]] = []
    styled_size = (720, 240)
    styled_expected_region = (0.05, 0.25, 0.92, 0.75)
    styled_text = "艺术字效果 TEXT 2026"
    styled_expectations = {
        "plain": ("#1c2028", None, None),
        "outline": ("#f8f8f8", "#19376e", 5),
        "shadow": ("#f5a528", "#504641", 9),
        "gradient": ("#8660b0", "#7274ba", None),
        "glow": ("#f8f8ff", "#20b8ff", 7),
    }
    for expected_effect, (
        expected_fill_color,
        expected_effect_color,
        expected_effect_size,
    ) in styled_expectations.items():
        styled_path = root / f"text-detection-styled-{expected_effect}.png"
        styled_background = (24, 28, 38) if expected_effect == "glow" else (238, 235, 228)
        styled_source = Image.new("RGB", styled_size, styled_background)
        styled_draw = ImageDraw.Draw(styled_source)
        if expected_effect == "plain":
            styled_draw.text(
                (70, 80),
                styled_text,
                font=_text_detection_font(52),
                fill=(28, 32, 40),
            )
        elif expected_effect == "outline":
            styled_draw.text(
                (70, 80),
                styled_text,
                font=_text_detection_font(52),
                fill=(248, 248, 248),
                stroke_width=5,
                stroke_fill=(25, 55, 110),
            )
        elif expected_effect == "shadow":
            styled_draw.text(
                (80, 88),
                styled_text,
                font=_text_detection_font(52),
                fill=(80, 70, 65),
            )
            styled_draw.text(
                (70, 80),
                styled_text,
                font=_text_detection_font(52),
                fill=(245, 165, 40),
            )
        elif expected_effect == "gradient":
            styled_mask = Image.new("L", styled_size, 0)
            linear_gradient = Image.linear_gradient("L")
            resized_gradient = linear_gradient.resize(styled_size)
            gradient = ImageOps.colorize(
                resized_gradient,
                black="#d02090",
                white="#20b8dc",
            )
            try:
                ImageDraw.Draw(styled_mask).text(
                    (70, 80),
                    styled_text,
                    font=_text_detection_font(52),
                    fill=255,
                    stroke_width=1,
                    stroke_fill=255,
                )
                styled_source.paste(gradient, (0, 0), styled_mask)
            finally:
                styled_mask.close()
                linear_gradient.close()
                resized_gradient.close()
                gradient.close()
        else:
            styled_mask = Image.new("L", styled_size, 0)
            ImageDraw.Draw(styled_mask).text(
                (70, 80),
                styled_text,
                font=_text_detection_font(52),
                fill=255,
            )
            blurred = styled_mask.filter(ImageFilter.GaussianBlur(7))
            boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
            glow = Image.new(
                "RGBA",
                styled_size,
                ImageColor.getcolor("#20b8ff", "RGBA"),
            )
            try:
                glow.putalpha(boosted)
                rgba_source = styled_source.convert("RGBA")
                try:
                    rgba_source.alpha_composite(glow)
                    ImageDraw.Draw(rgba_source).text(
                        (70, 80),
                        styled_text,
                        font=_text_detection_font(52),
                        fill="#f8f8ff",
                    )
                    rendered_source = rgba_source.convert("RGB")
                    styled_source.close()
                    styled_source = rendered_source
                finally:
                    rgba_source.close()
            finally:
                styled_mask.close()
                blurred.close()
                boosted.close()
                glow.close()
        styled_source.save(styled_path, "PNG")
        styled_source.close()

        styled_runs = []
        styled_elapsed: list[float] = []
        for _repetition in range(repetitions):
            started = time.perf_counter()
            suggestions = detect_text_regions(styled_path, max_suggestions=12)
            duration_ms = (time.perf_counter() - started) * 1_000
            styled_elapsed.append(duration_ms)
            all_elapsed.append(duration_ms)
            styled_runs.append(suggestions)
        styled_best = max(
            (
                suggestion
                for suggestion in styled_runs[0]
                if abs(suggestion.rotation) <= 1 and suggestion.visual_effect == expected_effect
            ),
            key=lambda suggestion: _region_overlap(
                suggestion.region,
                styled_expected_region,
            ),
            default=None,
        )
        styled_detection_cases.append(
            {
                "expected_effect": expected_effect,
                "detected": styled_best is not None,
                "detected_effect": (None if styled_best is None else styled_best.visual_effect),
                "estimated_fill_color": (
                    None if styled_best is None else styled_best.estimated_fill_color
                ),
                "fill_color_error": (
                    255
                    if styled_best is None
                    else _hex_color_error(
                        styled_best.estimated_fill_color,
                        expected_fill_color,
                    )
                ),
                "expected_effect_color": expected_effect_color,
                "estimated_effect_color": (
                    None if styled_best is None else styled_best.estimated_effect_color
                ),
                "effect_color_error": (
                    0
                    if expected_effect_color is None
                    and (styled_best is not None and styled_best.estimated_effect_color is None)
                    else (
                        255
                        if expected_effect_color is None or styled_best is None
                        else _hex_color_error(
                            styled_best.estimated_effect_color,
                            expected_effect_color,
                        )
                    )
                ),
                "expected_effect_size": expected_effect_size,
                "estimated_effect_size": (
                    None if styled_best is None else styled_best.estimated_effect_size
                ),
                "effect_size_error": (
                    0
                    if expected_effect_size is None
                    and (styled_best is not None and styled_best.estimated_effect_size is None)
                    else (
                        65
                        if expected_effect_size is None
                        or styled_best is None
                        or styled_best.estimated_effect_size is None
                        else abs(styled_best.estimated_effect_size - expected_effect_size)
                    )
                ),
                "overlap": (
                    0.0
                    if styled_best is None
                    else round(
                        _region_overlap(styled_best.region, styled_expected_region),
                        6,
                    )
                ),
                "deterministic": all(run == styled_runs[0] for run in styled_runs[1:]),
                "elapsed_ms": _metric(styled_elapsed),
            }
        )

    surface_background_cases: list[dict[str, Any]] = []
    surface_text = "复杂背景 TEXT 2026"
    surface_expectations = {
        "plain": ("#202830", None, None),
        "glow": ("#f8f8ff", "#20b8ff", 7),
    }
    for expected_effect, (
        expected_fill_color,
        expected_effect_color,
        expected_effect_size,
    ) in surface_expectations.items():
        surface_path = root / f"text-detection-surface-{expected_effect}.png"
        surface_source = _smooth_textured_background(styled_size)
        if expected_effect == "plain":
            ImageDraw.Draw(surface_source).text(
                (70, 80),
                surface_text,
                font=_text_detection_font(52),
                fill=expected_fill_color,
            )
        else:
            surface_mask = Image.new("L", styled_size, 0)
            ImageDraw.Draw(surface_mask).text(
                (70, 80),
                surface_text,
                font=_text_detection_font(52),
                fill=255,
            )
            blurred = surface_mask.filter(
                ImageFilter.GaussianBlur(float(cast(int, expected_effect_size)))
            )
            boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
            glow = Image.new(
                "RGBA",
                styled_size,
                ImageColor.getcolor(expected_effect_color, "RGBA"),
            )
            try:
                glow.putalpha(boosted)
                rgba_source = surface_source.convert("RGBA")
                try:
                    rgba_source.alpha_composite(glow)
                    ImageDraw.Draw(rgba_source).text(
                        (70, 80),
                        surface_text,
                        font=_text_detection_font(52),
                        fill=expected_fill_color,
                    )
                    rendered_source = rgba_source.convert("RGB")
                    surface_source.close()
                    surface_source = rendered_source
                finally:
                    rgba_source.close()
            finally:
                surface_mask.close()
                blurred.close()
                boosted.close()
                glow.close()
        surface_source.save(surface_path, "PNG")
        surface_source.close()

        surface_runs = []
        surface_elapsed: list[float] = []
        for _repetition in range(repetitions):
            started = time.perf_counter()
            suggestions = detect_text_regions(surface_path, max_suggestions=12)
            duration_ms = (time.perf_counter() - started) * 1_000
            surface_elapsed.append(duration_ms)
            all_elapsed.append(duration_ms)
            surface_runs.append(suggestions)
        surface_best = max(
            (
                suggestion
                for suggestion in surface_runs[0]
                if abs(suggestion.rotation) <= 1 and suggestion.visual_effect == expected_effect
            ),
            key=lambda suggestion: _region_overlap(
                suggestion.region,
                styled_expected_region,
            ),
            default=None,
        )
        surface_background_cases.append(
            {
                "expected_effect": expected_effect,
                "detected": surface_best is not None,
                "detected_effect": (None if surface_best is None else surface_best.visual_effect),
                "estimated_fill_color": (
                    None if surface_best is None else surface_best.estimated_fill_color
                ),
                "fill_color_error": (
                    255
                    if surface_best is None
                    else _hex_color_error(
                        surface_best.estimated_fill_color,
                        expected_fill_color,
                    )
                ),
                "expected_effect_color": expected_effect_color,
                "estimated_effect_color": (
                    None if surface_best is None else surface_best.estimated_effect_color
                ),
                "effect_color_error": (
                    0
                    if expected_effect_color is None
                    and (surface_best is not None and surface_best.estimated_effect_color is None)
                    else (
                        255
                        if expected_effect_color is None or surface_best is None
                        else _hex_color_error(
                            surface_best.estimated_effect_color,
                            expected_effect_color,
                        )
                    )
                ),
                "expected_effect_size": expected_effect_size,
                "estimated_effect_size": (
                    None if surface_best is None else surface_best.estimated_effect_size
                ),
                "effect_size_error": (
                    0
                    if expected_effect_size is None
                    and (surface_best is not None and surface_best.estimated_effect_size is None)
                    else (
                        65
                        if expected_effect_size is None
                        or surface_best is None
                        or surface_best.estimated_effect_size is None
                        else abs(surface_best.estimated_effect_size - expected_effect_size)
                    )
                ),
                "overlap": (
                    0.0
                    if surface_best is None
                    else round(
                        _region_overlap(surface_best.region, styled_expected_region),
                        6,
                    )
                ),
                "deterministic": all(run == surface_runs[0] for run in surface_runs[1:]),
                "elapsed_ms": _metric(surface_elapsed),
            }
        )

    structured_background_cases: list[dict[str, Any]] = []
    structured_text = "结构背景 TEXT 2026"
    structured_expectations = {
        "plain": ("#18222c", None, None),
        "glow": ("#f8f8ff", "#20b8ff", 7),
    }
    for expected_effect, (
        expected_fill_color,
        expected_effect_color,
        expected_effect_size,
    ) in structured_expectations.items():
        structured_path = root / f"text-detection-structured-{expected_effect}.png"
        structured_source = _structured_textured_background(styled_size)
        if expected_effect == "plain":
            ImageDraw.Draw(structured_source).text(
                (70, 80),
                structured_text,
                font=_text_detection_font(52),
                fill=expected_fill_color,
            )
        else:
            structured_mask = Image.new("L", styled_size, 0)
            ImageDraw.Draw(structured_mask).text(
                (70, 80),
                structured_text,
                font=_text_detection_font(52),
                fill=255,
            )
            blurred = structured_mask.filter(
                ImageFilter.GaussianBlur(float(cast(int, expected_effect_size)))
            )
            boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
            glow = Image.new(
                "RGBA",
                styled_size,
                ImageColor.getcolor(expected_effect_color, "RGBA"),
            )
            try:
                glow.putalpha(boosted)
                rgba_source = structured_source.convert("RGBA")
                try:
                    rgba_source.alpha_composite(glow)
                    ImageDraw.Draw(rgba_source).text(
                        (70, 80),
                        structured_text,
                        font=_text_detection_font(52),
                        fill=expected_fill_color,
                    )
                    rendered_source = rgba_source.convert("RGB")
                    structured_source.close()
                    structured_source = rendered_source
                finally:
                    rgba_source.close()
            finally:
                structured_mask.close()
                blurred.close()
                boosted.close()
                glow.close()
        structured_source.save(structured_path, "PNG")
        structured_source.close()

        structured_runs = []
        structured_elapsed: list[float] = []
        for _repetition in range(repetitions):
            started = time.perf_counter()
            suggestions = detect_text_regions(structured_path, max_suggestions=12)
            duration_ms = (time.perf_counter() - started) * 1_000
            structured_elapsed.append(duration_ms)
            all_elapsed.append(duration_ms)
            structured_runs.append(suggestions)
        structured_best = max(
            (
                suggestion
                for suggestion in structured_runs[0]
                if abs(suggestion.rotation) <= 1 and suggestion.visual_effect == expected_effect
            ),
            key=lambda suggestion: _region_overlap(
                suggestion.region,
                styled_expected_region,
            ),
            default=None,
        )
        structured_background_cases.append(
            {
                "expected_effect": expected_effect,
                "detected": structured_best is not None,
                "detected_effect": (
                    None if structured_best is None else structured_best.visual_effect
                ),
                "estimated_fill_color": (
                    None if structured_best is None else structured_best.estimated_fill_color
                ),
                "fill_color_error": (
                    255
                    if structured_best is None
                    else _hex_color_error(
                        structured_best.estimated_fill_color,
                        expected_fill_color,
                    )
                ),
                "expected_effect_color": expected_effect_color,
                "estimated_effect_color": (
                    None if structured_best is None else structured_best.estimated_effect_color
                ),
                "effect_color_error": (
                    0
                    if expected_effect_color is None
                    and (
                        structured_best is not None
                        and structured_best.estimated_effect_color is None
                    )
                    else (
                        255
                        if expected_effect_color is None or structured_best is None
                        else _hex_color_error(
                            structured_best.estimated_effect_color,
                            expected_effect_color,
                        )
                    )
                ),
                "expected_effect_size": expected_effect_size,
                "estimated_effect_size": (
                    None if structured_best is None else structured_best.estimated_effect_size
                ),
                "effect_size_error": (
                    0
                    if expected_effect_size is None
                    and (
                        structured_best is not None
                        and structured_best.estimated_effect_size is None
                    )
                    else (
                        65
                        if expected_effect_size is None
                        or structured_best is None
                        or structured_best.estimated_effect_size is None
                        else abs(structured_best.estimated_effect_size - expected_effect_size)
                    )
                ),
                "overlap": (
                    0.0
                    if structured_best is None
                    else round(
                        _region_overlap(structured_best.region, styled_expected_region),
                        6,
                    )
                ),
                "deterministic": all(run == structured_runs[0] for run in structured_runs[1:]),
                "elapsed_ms": _metric(structured_elapsed),
            }
        )

    (
        photo_background_cases,
        photo_negative_cases,
        photo_matrix_elapsed,
    ) = _run_photo_like_text_detection_probe(
        root,
        size=styled_size,
        expected_region=styled_expected_region,
        repetitions=repetitions,
    )
    all_elapsed.extend(photo_matrix_elapsed)
    photo_layout_cases, photo_layout_elapsed = _run_photo_like_layout_probe(
        root,
        size=styled_size,
        repetitions=repetitions,
    )
    all_elapsed.extend(photo_layout_elapsed)
    (
        jpeg_photo_cases,
        jpeg_photo_negative_cases,
        jpeg_photo_elapsed,
    ) = _run_jpeg_photo_text_detection_probe(
        root,
        size=styled_size,
        repetitions=repetitions,
    )
    all_elapsed.extend(jpeg_photo_elapsed)

    perspective_path = root / "text-detection-perspective.png"
    perspective_source = Image.new("RGBA", canvas_size, "white")
    perspective_layer_width = min(400, max(280, round(canvas_size[0] * 0.78)))
    perspective_layer_height = round(perspective_layer_width * 0.24)
    perspective_layer = Image.new(
        "RGBA",
        (perspective_layer_width, perspective_layer_height),
        (0, 0, 0, 0),
    )
    ImageDraw.Draw(perspective_layer).text(
        (8, 8),
        "透视文字 TEXT 2026",
        font=_text_detection_font(max(28, round(perspective_layer_height * 0.42))),
        fill="#202020",
    )
    canvas_width, canvas_height = canvas_size
    pixel_quad = (
        canvas_width * 0.15,
        canvas_height * 0.25,
        canvas_width * 0.82,
        canvas_height * 0.14,
        canvas_width * 0.87,
        canvas_height * 0.79,
        canvas_width * 0.11,
        canvas_height * 0.58,
    )
    perspective_warped = _warp_surface_to_quad(
        perspective_layer,
        output_size=canvas_size,
        destination=cast(Any, pixel_quad),
    )
    perspective_source.alpha_composite(perspective_warped)
    perspective_source.convert("RGB").save(perspective_path, "PNG")
    perspective_source.close()
    perspective_layer.close()
    perspective_warped.close()
    expected_perspective_region = (0.11, 0.14, 0.87, 0.79)
    perspective_runs = []
    perspective_elapsed: list[float] = []
    for _repetition in range(repetitions):
        started = time.perf_counter()
        suggestions = detect_text_regions(perspective_path, max_suggestions=8)
        duration_ms = (time.perf_counter() - started) * 1_000
        perspective_elapsed.append(duration_ms)
        all_elapsed.append(duration_ms)
        perspective_runs.append(suggestions)
    best_perspective = max(
        (
            suggestion
            for suggestion in perspective_runs[0]
            if suggestion.perspective_quad is not None
        ),
        key=lambda suggestion: _region_overlap(
            suggestion.region,
            expected_perspective_region,
        ),
        default=None,
    )
    detected_quad = None if best_perspective is None else best_perspective.perspective_quad
    scale_delta = 0.0
    if detected_quad is not None:
        left_height = detected_quad[7] - detected_quad[1]
        right_height = detected_quad[5] - detected_quad[3]
        scale_delta = abs(left_height - right_height)

    multi_plane_path = root / "text-detection-multi-plane.png"
    multi_plane_size = (600, 420)
    multi_plane_scale = (multi_plane_size[0] / 1_000, multi_plane_size[1] / 700)
    multi_plane_source = Image.new("RGBA", multi_plane_size, "white")
    multi_plane_fixtures = (
        (
            "平面一 PERSPECTIVE 2026",
            (50.0, 60.0, 460.0, 25.0, 485.0, 300.0, 25.0, 205.0),
        ),
        (
            "平面二 PERSPECTIVE 2026",
            (520.0, 380.0, 930.0, 435.0, 965.0, 550.0, 495.0, 600.0),
        ),
    )
    for text, destination in multi_plane_fixtures:
        layer = Image.new("RGBA", (264, 60), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (5, 6),
            text,
            font=_text_detection_font(23),
            fill="#202020",
        )
        scaled_destination = tuple(
            value * (multi_plane_scale[0] if index % 2 == 0 else multi_plane_scale[1])
            for index, value in enumerate(destination)
        )
        warped = _warp_surface_to_quad(
            layer,
            output_size=multi_plane_size,
            destination=cast(Any, scaled_destination),
        )
        multi_plane_source.alpha_composite(warped)
        layer.close()
        warped.close()
    multi_plane_rendered = multi_plane_source.convert("RGB")
    multi_plane_rendered.save(multi_plane_path, "PNG")
    multi_plane_rendered.close()
    multi_plane_source.close()
    multi_plane_runs = []
    multi_plane_elapsed: list[float] = []
    for _repetition in range(repetitions):
        started = time.perf_counter()
        suggestions = detect_text_regions(multi_plane_path, max_suggestions=20)
        duration_ms = (time.perf_counter() - started) * 1_000
        multi_plane_elapsed.append(duration_ms)
        all_elapsed.append(duration_ms)
        multi_plane_runs.append(suggestions)
    plane_candidates = [
        suggestion for suggestion in multi_plane_runs[0] if suggestion.perspective_quad is not None
    ]
    expected_plane_regions = (
        (0.02, 0.06, 0.50, 0.28),
        (0.49, 0.55, 0.98, 0.77),
    )
    matched_planes = [
        max(
            plane_candidates,
            key=lambda suggestion: _region_overlap(suggestion.region, expected),
            default=None,
        )
        for expected in expected_plane_regions
    ]
    plane_overlaps = [
        (0.0 if suggestion is None else round(_region_overlap(suggestion.region, expected), 6))
        for suggestion, expected in zip(matched_planes, expected_plane_regions)
    ]
    plane_scale_directions: list[float] = []
    for suggestion in matched_planes:
        quad = None if suggestion is None else suggestion.perspective_quad
        if quad is None:
            plane_scale_directions.append(0.0)
            continue
        plane_scale_directions.append(round((quad[5] - quad[3]) - (quad[7] - quad[1]), 6))
    plane_indexes = [
        (-1 if suggestion is None else plane_candidates.index(suggestion))
        for suggestion in matched_planes
    ]
    distinct_planes = min(plane_indexes) >= 0 and len(set(plane_indexes)) == len(plane_indexes)

    occluded_multi_plane_path = root / "text-detection-occluded-multi-plane.png"
    occluded_multi_plane_size = (600, 420)
    occluded_scale = (
        occluded_multi_plane_size[0] / 1_000,
        occluded_multi_plane_size[1] / 700,
    )
    occluded_source = Image.new("RGBA", occluded_multi_plane_size, "white")
    occluded_fixtures = (
        (
            "平面一 PERSPECTIVE 2026",
            (60.0, 70.0, 660.0, 40.0, 690.0, 300.0, 40.0, 250.0),
            "#283c50",
        ),
        (
            "平面二 PERSPECTIVE 2026",
            (260.0, 80.0, 900.0, 160.0, 930.0, 290.0, 220.0, 400.0),
            "#403048",
        ),
    )
    source_colors = np.asarray(
        [ImageColor.getrgb(fixture[2]) for fixture in occluded_fixtures],
        dtype=np.float64,
    )
    source_color_distance = int(np.max(np.abs(source_colors[0] - source_colors[1])))
    source_color_axes = source_colors - 255.0
    source_axis_similarity = float(
        np.dot(source_color_axes[0], source_color_axes[1])
        / (np.linalg.norm(source_color_axes[0]) * np.linalg.norm(source_color_axes[1]))
    )
    occluded_expected_regions: list[tuple[float, float, float, float]] = []
    occluded_ink_masks: list[np.ndarray] = []
    for text, destination, color in occluded_fixtures:
        layer = Image.new("RGBA", (312, 72), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (6, 7),
            text,
            font=_text_detection_font(28),
            fill=color,
        )
        scaled_destination = tuple(
            value * (occluded_scale[0] if index % 2 == 0 else occluded_scale[1])
            for index, value in enumerate(destination)
        )
        warped = _warp_surface_to_quad(
            layer,
            output_size=occluded_multi_plane_size,
            destination=cast(Any, scaled_destination),
        )
        alpha = np.asarray(warped.getchannel("A"), dtype=np.uint8)
        ink_mask = alpha > 32
        y_indexes, x_indexes = np.nonzero(ink_mask)
        occluded_expected_regions.append(
            (
                float(np.min(x_indexes)) / occluded_multi_plane_size[0],
                float(np.min(y_indexes)) / occluded_multi_plane_size[1],
                (float(np.max(x_indexes)) + 1) / occluded_multi_plane_size[0],
                (float(np.max(y_indexes)) + 1) / occluded_multi_plane_size[1],
            )
        )
        occluded_ink_masks.append(ink_mask.copy())
        occluded_source.alpha_composite(warped)
        layer.close()
        warped.close()
    occluded_pixels = int(np.count_nonzero(occluded_ink_masks[0] & occluded_ink_masks[1]))
    occluded_ink_ratio = occluded_pixels / max(
        1,
        min(int(np.count_nonzero(mask)) for mask in occluded_ink_masks),
    )
    occluded_region_iou = _region_intersection_over_union(
        occluded_expected_regions[0],
        occluded_expected_regions[1],
    )
    occluded_rendered = occluded_source.convert("RGB")
    occluded_rendered.save(occluded_multi_plane_path, "PNG")
    occluded_rendered.close()
    occluded_source.close()
    occluded_runs = []
    occluded_elapsed: list[float] = []
    for _repetition in range(repetitions):
        started = time.perf_counter()
        suggestions = detect_text_regions(occluded_multi_plane_path, max_suggestions=20)
        duration_ms = (time.perf_counter() - started) * 1_000
        occluded_elapsed.append(duration_ms)
        all_elapsed.append(duration_ms)
        occluded_runs.append(suggestions)
    occluded_candidates = [
        suggestion for suggestion in occluded_runs[0] if suggestion.perspective_quad is not None
    ]
    occluded_matches = [
        max(
            occluded_candidates,
            key=lambda suggestion: _region_intersection_over_union(
                suggestion.region,
                expected,
            ),
            default=None,
        )
        for expected in occluded_expected_regions
    ]
    occluded_indexes = [
        (-1 if suggestion is None else occluded_candidates.index(suggestion))
        for suggestion in occluded_matches
    ]
    occluded_distinct = min(occluded_indexes) >= 0 and len(set(occluded_indexes)) == 2
    occluded_coverages = [
        (0.0 if suggestion is None else _region_overlap(expected, suggestion.region))
        for suggestion, expected in zip(occluded_matches, occluded_expected_regions)
    ]
    occluded_containments = [
        (0.0 if suggestion is None else _region_overlap(suggestion.region, expected))
        for suggestion, expected in zip(occluded_matches, occluded_expected_regions)
    ]
    occluded_scale_directions: list[float] = []
    occluded_fill_errors: list[int] = []
    for suggestion, fixture in zip(occluded_matches, occluded_fixtures):
        quad = None if suggestion is None else suggestion.perspective_quad
        occluded_scale_directions.append(
            0.0 if quad is None else round((quad[5] - quad[3]) - (quad[7] - quad[1]), 6)
        )
        occluded_fill_errors.append(
            255
            if suggestion is None
            else _hex_color_error(suggestion.estimated_fill_color, fixture[2])
        )

    three_plane_path = root / "text-detection-three-plane-overlap.png"
    three_plane_size = (600, 420)
    three_plane_scale = (
        three_plane_size[0] / 1_000,
        three_plane_size[1] / 700,
    )
    three_plane_fixtures = (
        (
            "平面一 PERSPECTIVE 2026",
            (60.0, 70.0, 660.0, 40.0, 690.0, 300.0, 40.0, 250.0),
            "#16324f",
        ),
        (
            "平面三 PERSPECTIVE 2026",
            (120.0, 190.0, 780.0, 75.0, 835.0, 335.0, 90.0, 315.0),
            "#2f6b3d",
        ),
        (
            "平面二 PERSPECTIVE 2026",
            (260.0, 80.0, 900.0, 160.0, 930.0, 290.0, 220.0, 400.0),
            "#8c3a1e",
        ),
    )
    three_plane_source = Image.new("RGBA", three_plane_size, "white")
    three_plane_expected_regions: list[tuple[float, float, float, float]] = []
    three_plane_ink_masks: list[np.ndarray] = []
    for text, destination, color in three_plane_fixtures:
        layer = Image.new("RGBA", (312, 72), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (6, 7),
            text,
            font=_text_detection_font(28),
            fill=color,
        )
        scaled_destination = tuple(
            value * (three_plane_scale[0] if index % 2 == 0 else three_plane_scale[1])
            for index, value in enumerate(destination)
        )
        warped = _warp_surface_to_quad(
            layer,
            output_size=three_plane_size,
            destination=cast(Any, scaled_destination),
        )
        alpha = np.asarray(warped.getchannel("A"), dtype=np.uint8)
        ink_mask = alpha > 32
        y_indexes, x_indexes = np.nonzero(ink_mask)
        three_plane_expected_regions.append(
            (
                float(np.min(x_indexes)) / three_plane_size[0],
                float(np.min(y_indexes)) / three_plane_size[1],
                (float(np.max(x_indexes)) + 1) / three_plane_size[0],
                (float(np.max(y_indexes)) + 1) / three_plane_size[1],
            )
        )
        three_plane_ink_masks.append(ink_mask.copy())
        three_plane_source.alpha_composite(warped)
        layer.close()
        warped.close()
    three_plane_region_ious = [
        _region_intersection_over_union(
            three_plane_expected_regions[first_index],
            three_plane_expected_regions[second_index],
        )
        for first_index in range(len(three_plane_expected_regions))
        for second_index in range(first_index + 1, len(three_plane_expected_regions))
    ]
    three_plane_ink_occlusions = [
        int(
            np.count_nonzero(
                three_plane_ink_masks[first_index] & three_plane_ink_masks[second_index]
            )
        )
        / max(
            1,
            min(
                int(np.count_nonzero(three_plane_ink_masks[first_index])),
                int(np.count_nonzero(three_plane_ink_masks[second_index])),
            ),
        )
        for first_index in range(len(three_plane_ink_masks))
        for second_index in range(first_index + 1, len(three_plane_ink_masks))
    ]
    three_plane_rendered = three_plane_source.convert("RGB")
    three_plane_rendered.save(three_plane_path, "PNG")
    three_plane_rendered.close()
    three_plane_source.close()
    three_plane_runs = []
    three_plane_elapsed: list[float] = []
    for _repetition in range(repetitions):
        started = time.perf_counter()
        suggestions = detect_text_regions(three_plane_path, max_suggestions=20)
        duration_ms = (time.perf_counter() - started) * 1_000
        three_plane_elapsed.append(duration_ms)
        all_elapsed.append(duration_ms)
        three_plane_runs.append(suggestions)
    three_plane_candidates = [
        suggestion for suggestion in three_plane_runs[0] if suggestion.perspective_quad is not None
    ]
    three_plane_matches = [
        max(
            three_plane_candidates,
            key=lambda suggestion: _region_intersection_over_union(
                suggestion.region,
                expected,
            ),
            default=None,
        )
        for expected in three_plane_expected_regions
    ]
    three_plane_indexes = [
        (-1 if suggestion is None else three_plane_candidates.index(suggestion))
        for suggestion in three_plane_matches
    ]
    three_plane_distinct = min(three_plane_indexes) >= 0 and len(set(three_plane_indexes)) == len(
        three_plane_expected_regions
    )
    three_plane_coverages = [
        (0.0 if suggestion is None else _region_overlap(expected, suggestion.region))
        for suggestion, expected in zip(three_plane_matches, three_plane_expected_regions)
    ]
    three_plane_containments = [
        (0.0 if suggestion is None else _region_overlap(suggestion.region, expected))
        for suggestion, expected in zip(three_plane_matches, three_plane_expected_regions)
    ]
    three_plane_scale_directions: list[float] = []
    three_plane_fill_errors: list[int] = []
    for suggestion, fixture in zip(three_plane_matches, three_plane_fixtures):
        quad = None if suggestion is None else suggestion.perspective_quad
        three_plane_scale_directions.append(
            0.0 if quad is None else round((quad[5] - quad[3]) - (quad[7] - quad[1]), 6)
        )
        three_plane_fill_errors.append(
            255
            if suggestion is None
            else _hex_color_error(suggestion.estimated_fill_color, fixture[2])
        )

    same_color_path = root / "text-detection-same-color-occluded-planes.png"
    same_color_source = Image.new("RGBA", three_plane_size, "white")
    for text, destination, _color in three_plane_fixtures:
        layer = Image.new("RGBA", (312, 72), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (6, 7),
            text,
            font=_text_detection_font(28),
            fill="#283c50",
        )
        scaled_destination = tuple(
            value * (three_plane_scale[0] if index % 2 == 0 else three_plane_scale[1])
            for index, value in enumerate(destination)
        )
        warped = _warp_surface_to_quad(
            layer,
            output_size=three_plane_size,
            destination=cast(Any, scaled_destination),
        )
        same_color_source.alpha_composite(warped)
        layer.close()
        warped.close()
    same_color_rendered = same_color_source.convert("RGB")
    same_color_rendered.save(same_color_path, "PNG")
    same_color_rendered.close()
    same_color_source.close()
    same_color_runs = []
    same_color_elapsed: list[float] = []
    for _repetition in range(repetitions):
        started = time.perf_counter()
        suggestions = detect_text_regions(same_color_path, max_suggestions=20)
        duration_ms = (time.perf_counter() - started) * 1_000
        same_color_elapsed.append(duration_ms)
        all_elapsed.append(duration_ms)
        same_color_runs.append(suggestions)
    same_color_candidate_count = sum(
        suggestion.perspective_quad is not None for suggestion in same_color_runs[0]
    )
    return {
        "case_count": len(cases),
        "cases": cases,
        "perspective": {
            "detected": best_perspective is not None,
            "candidate_count": len(perspective_runs[0]),
            "candidate_angles": [suggestion.rotation for suggestion in perspective_runs[0]],
            "quadrilateral_candidate_count": sum(
                suggestion.perspective_quad is not None for suggestion in perspective_runs[0]
            ),
            "detected_angle": (None if best_perspective is None else best_perspective.rotation),
            "overlap": (
                0.0
                if best_perspective is None
                else round(
                    _region_overlap(best_perspective.region, expected_perspective_region),
                    6,
                )
            ),
            "scale_delta": round(scale_delta, 6),
            "deterministic": all(run == perspective_runs[0] for run in perspective_runs[1:]),
            "elapsed_ms": _metric(perspective_elapsed),
        },
        "multi_plane": {
            "detected": all(suggestion is not None for suggestion in matched_planes),
            "candidate_count": len(plane_candidates),
            "candidate_regions": [suggestion.region for suggestion in plane_candidates],
            "overlaps": plane_overlaps,
            "distinct_planes": distinct_planes,
            "reading_order": distinct_planes and plane_indexes == sorted(plane_indexes),
            "opposite_scale_directions": (
                plane_scale_directions[0] > 0 and plane_scale_directions[1] < 0
            ),
            "scale_directions": plane_scale_directions,
            "deterministic": all(run == multi_plane_runs[0] for run in multi_plane_runs[1:]),
            "elapsed_ms": _metric(multi_plane_elapsed),
        },
        "occluded_multi_plane": {
            "detected": all(suggestion is not None for suggestion in occluded_matches),
            "candidate_count": len(occluded_candidates),
            "candidate_regions": [suggestion.region for suggestion in occluded_candidates],
            "distinct_planes": occluded_distinct,
            "reading_order": (occluded_distinct and occluded_indexes == sorted(occluded_indexes)),
            "expected_region_iou": round(occluded_region_iou, 6),
            "ink_occlusion_ratio": round(occluded_ink_ratio, 6),
            "source_color_distance": source_color_distance,
            "source_axis_similarity": round(source_axis_similarity, 6),
            "coverages": [round(value, 6) for value in occluded_coverages],
            "containments": [round(value, 6) for value in occluded_containments],
            "opposite_scale_directions": (
                occluded_scale_directions[0] > 0 and occluded_scale_directions[1] < 0
            ),
            "scale_directions": occluded_scale_directions,
            "fill_color_errors": occluded_fill_errors,
            "deterministic": all(run == occluded_runs[0] for run in occluded_runs[1:]),
            "elapsed_ms": _metric(occluded_elapsed),
        },
        "three_plane_overlap": {
            "detected": all(suggestion is not None for suggestion in three_plane_matches),
            "candidate_count": len(three_plane_candidates),
            "candidate_regions": [suggestion.region for suggestion in three_plane_candidates],
            "distinct_planes": three_plane_distinct,
            "reading_order": (
                three_plane_distinct and three_plane_indexes == sorted(three_plane_indexes)
            ),
            "pairwise_region_ious": [round(value, 6) for value in three_plane_region_ious],
            "pairwise_ink_occlusions": [round(value, 6) for value in three_plane_ink_occlusions],
            "coverages": [round(value, 6) for value in three_plane_coverages],
            "containments": [round(value, 6) for value in three_plane_containments],
            "divergent_scale_directions": (
                max(three_plane_scale_directions) > 0 and min(three_plane_scale_directions) < 0
            ),
            "scale_directions": three_plane_scale_directions,
            "fill_color_errors": three_plane_fill_errors,
            "deterministic": all(run == three_plane_runs[0] for run in three_plane_runs[1:]),
            "elapsed_ms": _metric(three_plane_elapsed),
            "same_color_safety": {
                "perspective_candidate_count": same_color_candidate_count,
                "deterministic": all(run == same_color_runs[0] for run in same_color_runs[1:]),
                "elapsed_ms": _metric(same_color_elapsed),
            },
        },
        "curved": {
            "case_count": len(curved_detection_cases),
            "cases": curved_detection_cases,
        },
        "styled": {
            "case_count": len(styled_detection_cases),
            "cases": styled_detection_cases,
        },
        "surface_background": {
            "case_count": len(surface_background_cases),
            "cases": surface_background_cases,
        },
        "structured_background": {
            "case_count": len(structured_background_cases),
            "cases": structured_background_cases,
        },
        "photo_background": {
            "case_count": len(photo_background_cases),
            "cases": photo_background_cases,
            "negative_case_count": len(photo_negative_cases),
            "negative_cases": photo_negative_cases,
            "negative_candidate_count": sum(
                int(case["candidate_count"]) for case in photo_negative_cases
            ),
            "negative_deterministic": all(
                bool(case["deterministic"]) for case in photo_negative_cases
            ),
            "negative_elapsed_ms": {
                "unit": "ms",
                "count": sum(int(case["elapsed_ms"]["count"]) for case in photo_negative_cases),
                "p95": max(float(case["elapsed_ms"]["p95"]) for case in photo_negative_cases),
            },
            "layout_case_count": len(photo_layout_cases),
            "layout_cases": photo_layout_cases,
            "jpeg_case_count": len(jpeg_photo_cases),
            "jpeg_cases": jpeg_photo_cases,
            "jpeg_negative_case_count": len(jpeg_photo_negative_cases),
            "jpeg_negative_cases": jpeg_photo_negative_cases,
            "jpeg_negative_candidate_count": sum(
                int(case["candidate_count"]) for case in jpeg_photo_negative_cases
            ),
            "jpeg_negative_deterministic": all(
                bool(case["deterministic"]) for case in jpeg_photo_negative_cases
            ),
        },
        "elapsed_ms": _metric(all_elapsed),
    }


def _run_font_match_probe(root: Path, repetitions: int) -> dict[str, Any]:
    candidates = installed_font_candidates(("微软雅黑", "宋体", "黑体", "等线"))
    cases: list[dict[str, Any]] = []
    all_elapsed: list[float] = []
    for index, expected in enumerate(candidates[:2]):
        source_path = root / f"font-match-{index}.png"
        image = Image.new("RGB", (300, 120), "#f7f5f0")
        draw = ImageDraw.Draw(image)
        draw.text(
            (18, 20),
            "轻量图片",
            font=ImageFont.truetype(str(expected.path), 56),
            fill="#202020",
        )
        image.save(source_path, "PNG")
        image.close()
        runs: list[tuple[tuple[str, float], ...]] = []
        elapsed: list[float] = []
        for _repetition in range(repetitions):
            started = time.perf_counter()
            suggestions = match_fonts(
                source_path,
                region=(0.0, 0.0, 1.0, 1.0),
                text="轻量图片",
                candidates=candidates,
                maximum_results=len(candidates),
            )
            duration_ms = (time.perf_counter() - started) * 1_000
            elapsed.append(duration_ms)
            all_elapsed.append(duration_ms)
            runs.append(tuple((item.font_family, item.score) for item in suggestions))
        cases.append(
            {
                "expected_font": expected.family,
                "top_font": runs[0][0][0],
                "top_score": runs[0][0][1],
                "top1_correct": runs[0][0][0] == expected.family,
                "deterministic": all(run == runs[0] for run in runs[1:]),
                "elapsed_ms": _metric(elapsed),
            }
        )
    return {
        "candidate_fonts": [candidate.family for candidate in candidates],
        "case_count": len(cases),
        "cases": cases,
        "elapsed_ms": _metric(all_elapsed or [0.0]),
    }


def _selection_quality(
    actual: tuple[float, float, float, float],
    expected: tuple[float, float, float, float],
) -> tuple[float, float]:
    width = max(0.0, min(actual[2], expected[2]) - max(actual[0], expected[0]))
    height = max(0.0, min(actual[3], expected[3]) - max(actual[1], expected[1]))
    intersection = width * height
    actual_area = (actual[2] - actual[0]) * (actual[3] - actual[1])
    expected_area = (expected[2] - expected[0]) * (expected[3] - expected[1])
    coverage = intersection / max(expected_area, 1e-12)
    iou = intersection / max(actual_area + expected_area - intersection, 1e-12)
    return (coverage, iou)


def _run_smart_selection_probe(root: Path, repetitions: int) -> dict[str, Any]:
    canvas_size = (300, 180)
    fixtures: list[
        tuple[
            str,
            Image.Image,
            tuple[float, float, float, float],
            str,
            int,
            int,
            int,
        ]
    ] = []

    centered = Image.new("RGB", canvas_size, "#f5f5f5")
    ImageDraw.Draw(centered).rounded_rectangle(
        (84, 38, 214, 150),
        radius=18,
        fill="#2468d8",
    )
    fixtures.append(
        (
            "centered_contrast",
            centered,
            (84 / 300, 38 / 180, 215 / 300, 151 / 180),
            "contrast",
            1,
            0,
            0,
        )
    )

    edge = Image.new("RGB", canvas_size)
    for x in range(edge.width):
        shade = round(185 + x * 55 / (edge.width - 1))
        ImageDraw.Draw(edge).line((x, 0, x, edge.height - 1), fill=(shade, shade, shade))
    ImageDraw.Draw(edge).rectangle((0, 42, 82, 144), fill="#1f63dc")
    fixtures.append(
        (
            "edge_gradient",
            edge,
            (0, 42 / 180, 83 / 300, 145 / 180),
            "contrast",
            1,
            0,
            0,
        )
    )

    alpha = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    ImageDraw.Draw(alpha).ellipse((90, 34, 220, 154), fill=(220, 60, 80, 180))
    fixtures.append(
        (
            "transparent_alpha",
            alpha,
            (90 / 300, 34 / 180, 221 / 300, 155 / 180),
            "alpha",
            1,
            0,
            0,
        )
    )

    multiple = Image.new("RGB", canvas_size, "#fafafa")
    draw = ImageDraw.Draw(multiple)
    draw.rectangle((24, 30, 144, 150), fill="#245fd2")
    draw.ellipse((224, 62, 288, 126), fill="#dc3c50")
    fixtures.append(
        (
            "multiple_subjects",
            multiple,
            (24 / 300, 30 / 180, 145 / 300, 151 / 180),
            "contrast",
            2,
            0,
            0,
        )
    )

    touching = Image.new("RGB", canvas_size, "#fafafa")
    draw = ImageDraw.Draw(touching)
    draw.rectangle((30, 35, 135, 130), fill="#245fd2")
    draw.ellipse((110, 45, 210, 135), fill="#dc3c50")
    fixtures.append(
        (
            "touching_color_split",
            touching,
            (30 / 300, 35 / 180, 211 / 300, 136 / 180),
            "contrast",
            3,
            2,
            0,
        )
    )

    same_color_touching = Image.new("RGB", canvas_size, "#fafafa")
    draw = ImageDraw.Draw(same_color_touching)
    draw.ellipse((30, 40, 135, 145), fill="#245fd2")
    draw.ellipse((130, 40, 235, 145), fill="#245fd2")
    fixtures.append(
        (
            "same_color_shape_split",
            same_color_touching,
            (30 / 300, 40 / 180, 236 / 300, 146 / 180),
            "contrast",
            3,
            0,
            2,
        )
    )

    same_color_overlap = Image.new("RGB", canvas_size, "#fafafa")
    draw = ImageDraw.Draw(same_color_overlap)
    draw.ellipse((30, 40, 135, 145), fill="#245fd2")
    draw.ellipse((108, 40, 213, 145), fill="#245fd2")
    fixtures.append(
        (
            "same_color_moderate_overlap",
            same_color_overlap,
            (30 / 300, 40 / 180, 214 / 300, 146 / 180),
            "contrast",
            3,
            0,
            2,
        )
    )

    cases: list[dict[str, Any]] = []
    all_elapsed: list[float] = []
    try:
        for index, (
            name,
            image,
            expected,
            expected_source,
            expected_min_candidates,
            expected_min_color_split,
            expected_min_shape_split,
        ) in enumerate(fixtures):
            source_path = root / f"smart-selection-{index}.png"
            image.save(source_path, "PNG")
            runs = []
            elapsed: list[float] = []
            for _repetition in range(repetitions):
                started = time.perf_counter()
                suggestions = detect_smart_selection(source_path)
                duration_ms = (time.perf_counter() - started) * 1_000
                elapsed.append(duration_ms)
                all_elapsed.append(duration_ms)
                runs.append(suggestions)
            best = runs[0][0] if runs[0] else None
            coverage, iou = (
                (0.0, 0.0) if best is None else _selection_quality(best.region, expected)
            )
            color_split = [item for item in runs[0] if item.selection_kind == "color_split"]
            shape_split = [item for item in runs[0] if item.selection_kind == "shape_split"]
            split_probe_exclusive = expected_min_color_split == 0
            if expected_min_color_split:
                split_masks = [
                    decode_selection_mask(
                        item.mask_rle or "",
                        item.mask_size or (0, 0),
                        target_size=canvas_size,
                    )
                    for item in color_split
                ]
                try:
                    blue_membership = [
                        cast(int, mask.getpixel((60, 80))) > 0 for mask in split_masks
                    ]
                    red_membership = [
                        cast(int, mask.getpixel((175, 80))) > 0 for mask in split_masks
                    ]
                    split_probe_exclusive = (
                        sum(blue_membership) == 1
                        and sum(red_membership) == 1
                        and blue_membership != red_membership
                    )
                finally:
                    for split_mask in split_masks:
                        split_mask.close()
            shape_probe_exclusive = expected_min_shape_split == 0
            if expected_min_shape_split:
                shape_masks = [
                    decode_selection_mask(
                        item.mask_rle or "",
                        item.mask_size or (0, 0),
                        target_size=canvas_size,
                    )
                    for item in shape_split
                ]
                try:
                    left_membership = [
                        cast(int, mask.getpixel((75, 90))) > 0 for mask in shape_masks
                    ]
                    right_membership = [
                        cast(int, mask.getpixel((190, 90))) > 0 for mask in shape_masks
                    ]
                    shape_probe_exclusive = (
                        sum(left_membership) == 1
                        and sum(right_membership) == 1
                        and left_membership != right_membership
                    )
                finally:
                    for shape_mask in shape_masks:
                        shape_mask.close()
            cases.append(
                {
                    "name": name,
                    "detected": best is not None,
                    "expected_source": expected_source,
                    "source": None if best is None else best.source,
                    "expected_min_candidates": expected_min_candidates,
                    "candidate_count": len(runs[0]),
                    "expected_min_color_split": expected_min_color_split,
                    "color_split_candidate_count": len(color_split),
                    "split_probe_exclusive": split_probe_exclusive,
                    "expected_min_shape_split": expected_min_shape_split,
                    "shape_split_candidate_count": len(shape_split),
                    "shape_probe_exclusive": shape_probe_exclusive,
                    "coverage": round(coverage, 6),
                    "iou": round(iou, 6),
                    "deterministic": all(run == runs[0] for run in runs[1:]),
                    "elapsed_ms": _metric(elapsed),
                }
            )
    finally:
        for (
            _name,
            image,
            _expected,
            _source,
            _count,
            _color_split_count,
            _shape_split_count,
        ) in fixtures:
            image.close()
    return {
        "case_count": len(cases),
        "cases": cases,
        "elapsed_ms": _metric(all_elapsed),
    }


def _curved_ocr_baseline_delta(image: Image.Image) -> float:
    rgb = image.convert("RGB")
    try:
        pixels = rgb.load()
        if pixels is None:
            return 0.0
        ink = [
            (x, y)
            for y in range(rgb.height)
            for x in range(rgb.width)
            if max(cast(tuple[int, int, int], pixels[x, y])) < 100
        ]
    finally:
        rgb.close()
    if not ink:
        return 0.0
    left = min(x for x, _y in ink)
    right = max(x for x, _y in ink)
    span = right - left + 1
    bands: list[float] = []
    for start, end in ((0.05, 0.25), (0.40, 0.60), (0.75, 0.95)):
        values = [y for x, y in ink if left + span * start <= x <= left + span * end]
        bands.append(sum(values) / max(1, len(values)))
    return (bands[0] + bands[2]) / 2 - bands[1]


def _run_curved_ocr_rectification_probe(repetitions: int) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    all_elapsed: list[float] = []
    for curve in (0.45, 0.75, -0.45, -0.75):
        source = Image.new("RGBA", (680, 170), "white")
        try:
            _draw_replacement_text(
                source,
                text="弧形文字测试二零二六",
                font_family="微软雅黑",
                font_size=70,
                color="#202020",
                align="center",
                curve=curve,
            )
            input_delta = abs(_curved_ocr_baseline_delta(source))
            hashes: list[str] = []
            residuals: list[float] = []
            elapsed: list[float] = []
            rgb = source.convert("RGB")
            try:
                for _repetition in range(repetitions):
                    started = time.perf_counter()
                    rectified = _rectify_curved_baseline(rgb, curve)
                    duration_ms = (time.perf_counter() - started) * 1_000
                    try:
                        elapsed.append(duration_ms)
                        all_elapsed.append(duration_ms)
                        rgba = rectified.convert("RGBA")
                        try:
                            hashes.append(_pixel_hash(rgba))
                        finally:
                            rgba.close()
                        residuals.append(abs(_curved_ocr_baseline_delta(rectified)))
                    finally:
                        rectified.close()
            finally:
                rgb.close()
            maximum_residual = max(residuals)
            cases.append(
                {
                    "curve": curve,
                    "input_delta_px": round(input_delta, 6),
                    "maximum_residual_delta_px": round(maximum_residual, 6),
                    "maximum_residual_ratio": round(
                        maximum_residual / max(input_delta, 1e-9),
                        6,
                    ),
                    "deterministic_pixels": len(set(hashes)) == 1,
                    "elapsed_ms": _metric(elapsed),
                }
            )
        finally:
            source.close()
    return {
        "case_count": len(cases),
        "cases": cases,
        "elapsed_ms": _metric(all_elapsed),
    }


def evaluate_smart_edit_gate(
    result: dict[str, Any],
    thresholds: SmartEditGateThresholds | None = None,
) -> list[str]:
    limits = thresholds or SmartEditGateThresholds()
    violations: list[str] = []
    upscale = result["upscale"]
    for scale in (2, 4):
        data = upscale["scales"].get(str(scale))
        if data is None:
            violations.append(f"缺少 {scale}× 高清样本")
            continue
        if any(size != data["expected_size"] for size in data["output_sizes"]):
            violations.append(f"{scale}× 高清输出尺寸不正确")
        if any(mode != "RGBA" for mode in data["modes"]):
            violations.append(f"{scale}× 高清未保留 RGBA")
        if float(data["maximum_alpha_mae"]) > limits.maximum_upscale_alpha_mae:
            violations.append(
                f"{scale}× Alpha MAE {data['maximum_alpha_mae']} > "
                f"{limits.maximum_upscale_alpha_mae}"
            )
        if not bool(data["deterministic_pixels"]):
            violations.append(f"{scale}× 高清像素不可复现")
        if float(data["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
            violations.append(f"{scale}× 高清 P95 超过性能门槛")

    background = result["remove_background"]
    if int(background["case_count"]) < limits.minimum_background_cases:
        violations.append(
            f"去背景样本只有 {background['case_count']} 个，"
            f"要求至少 {limits.minimum_background_cases} 个"
        )
    for index, case in enumerate(background["cases"], start=1):
        if float(case["minimum_foreground_recall"]) < limits.minimum_foreground_recall:
            violations.append(f"去背景样本 {index} 前景召回率不足")
        if float(case["minimum_background_removal"]) < limits.minimum_background_removal:
            violations.append(f"去背景样本 {index} 背景去除率不足")
        if not bool(case["deterministic_pixels"]):
            violations.append(f"去背景样本 {index} 像素不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
            violations.append(f"去背景样本 {index} P95 超过性能门槛")

    edge_background = result["edge_background"]
    if int(edge_background["case_count"]) < limits.minimum_edge_background_cases:
        violations.append("贴边去背景样本数量不足")
    for index, case in enumerate(edge_background["cases"], start=1):
        if float(case["maximum_foreground_alpha_mae"]) > limits.maximum_edge_foreground_alpha_mae:
            violations.append(f"贴边去背景样本 {index} 前景 Alpha 误差过大")
        if float(case["minimum_background_removal"]) < limits.minimum_background_removal:
            violations.append(f"贴边去背景样本 {index} 渐变背景去除率不足")
        if not bool(case["deterministic_pixels"]):
            violations.append(f"贴边去背景样本 {index} 像素不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
            violations.append(f"贴边去背景样本 {index} P95 超过性能门槛")

    replacement = result["background_replacement"]
    if int(replacement["case_count"]) < limits.minimum_background_replacement_cases:
        violations.append("背景替换样本数量不足")
    for index, case in enumerate(replacement["cases"], start=1):
        expected_background = case["expected_background_pixel"]
        if any(pixel != expected_background for pixel in case["background_pixels"]):
            violations.append(f"背景替换样本 {index} 背景像素不正确")
        expected_subject = case["expected_subject_pixel"]
        if any(pixel != expected_subject for pixel in case["subject_pixels"]):
            violations.append(f"背景替换样本 {index} 主体像素不正确")
        if any(mode != "RGBA" for mode in case["modes"]):
            violations.append(f"背景替换样本 {index} 未输出 RGBA")
        if any(extrema != [255, 255] for extrema in case["alpha_extrema"]):
            violations.append(f"背景替换样本 {index} 输出并非完全不透明")
        if not bool(case["source_had_transparency"]):
            violations.append(f"背景替换样本 {index} 没有消费透明源图")
        expected_sha256 = case["expected_background_sha256"]
        if any(value != expected_sha256 for value in case["background_sha256"]):
            violations.append(f"背景替换样本 {index} 背景哈希不正确")
        if not bool(case["deterministic_pixels"]):
            violations.append(f"背景替换样本 {index} 像素不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
            violations.append(f"背景替换样本 {index} P95 超过性能门槛")

    repair = result["region_repair"]
    if float(repair["maximum_region_mae"]) > limits.maximum_region_repair_mae:
        violations.append("物体擦除修复区域像素误差过大")
    if float(repair["maximum_alpha_mae"]) > limits.maximum_region_repair_mae:
        violations.append("物体擦除修复区域 Alpha 误差过大")
    if float(repair["minimum_outside_unchanged"]) < limits.minimum_region_repair_outside_unchanged:
        violations.append("物体擦除意外修改了选择区域外像素")
    if not bool(repair["source_lineage_matches"]):
        violations.append("物体擦除结果源修订血缘错误")
    if not bool(repair["deterministic_pixels"]):
        violations.append("物体擦除像素不可复现")
    if float(repair["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
        violations.append("物体擦除 P95 超过性能门槛")

    mask_repair = result["mask_repair"]
    if float(mask_repair["minimum_selected_change"]) < limits.minimum_mask_selected_change:
        violations.append("像素蒙版修复对所选主体修改不足")
    if float(mask_repair["minimum_outside_mask_unchanged"]) < limits.minimum_mask_outside_unchanged:
        violations.append("像素蒙版修复意外修改了蒙版外像素")
    if (
        float(mask_repair["minimum_unselected_subject_unchanged"])
        < limits.minimum_mask_unselected_subject_unchanged
    ):
        violations.append("像素蒙版修复误改了未选择主体")
    if (
        float(mask_repair["minimum_manual_exclusion_unchanged"])
        < limits.minimum_mask_manual_exclusion_unchanged
    ):
        violations.append("像素蒙版修复误改了画笔移出的主体区域")
    if not bool(mask_repair["source_lineage_matches"]):
        violations.append("像素蒙版修复结果源修订血缘错误")
    if not bool(mask_repair["provenance_stores_identity_only"]):
        violations.append("像素蒙版修复血缘记录泄露原始蒙版载荷")
    if not bool(mask_repair["deterministic_pixels"]):
        violations.append("像素蒙版修复结果不可复现")
    if float(mask_repair["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
        violations.append("像素蒙版修复 P95 超过性能门槛")

    refinement = result["alpha_refinement"]
    if int(refinement["preserve_alpha"]) != 255:
        violations.append("保留主体画笔没有恢复 Alpha")
    if int(refinement["remove_alpha"]) != 0:
        violations.append("删除背景画笔没有清除 Alpha")
    if not bool(refinement["source_lineage_matches"]):
        violations.append("边缘细化结果源修订血缘错误")

    text = result["text_replacement"]
    if float(text["minimum_region_change"]) < limits.minimum_text_region_change:
        violations.append("改文字区域变化不足")
    if int(text["minimum_red_pixels"]) < limits.minimum_text_red_pixels:
        violations.append("改文字没有渲染足够的新文字像素")
    if float(text["minimum_outside_unchanged"]) < limits.minimum_outside_region_unchanged:
        violations.append("改文字意外修改了选择区域外像素")
    if not bool(text["deterministic_pixels"]):
        violations.append("改文字像素不可复现")
    if float(text["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
        violations.append("改文字 P95 超过性能门槛")
    vertical_text = text["vertical"]
    if int(vertical_text["minimum_red_pixels"]) < limits.minimum_vertical_text_red_pixels:
        violations.append("竖排改文字没有渲染足够的新文字像素")
    if float(vertical_text["minimum_aspect_ratio"]) < limits.minimum_vertical_text_aspect_ratio:
        violations.append("竖排改文字被裁切或未保持纵向布局")
    if float(vertical_text["minimum_outside_unchanged"]) < limits.minimum_outside_region_unchanged:
        violations.append("竖排改文字意外修改了选择区域外像素")
    if not bool(vertical_text["deterministic_pixels"]):
        violations.append("竖排改文字像素不可复现")
    if float(vertical_text["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
        violations.append("竖排改文字 P95 超过性能门槛")
    perspective_text = text["perspective"]
    if int(perspective_text["minimum_red_pixels"]) < limits.minimum_perspective_text_red_pixels:
        violations.append("透视改文字没有渲染足够的新文字像素")
    if (
        float(perspective_text["minimum_outside_quad_unchanged"])
        < limits.minimum_outside_region_unchanged
    ):
        violations.append("透视改文字意外修改了四边形外像素")
    if not bool(perspective_text["deterministic_pixels"]):
        violations.append("透视改文字像素不可复现")
    if float(perspective_text["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
        violations.append("透视改文字 P95 超过性能门槛")
    curved_text = text["curved"]
    if int(curved_text["minimum_red_pixels"]) < limits.minimum_curved_text_red_pixels:
        violations.append("弧形改文字没有渲染足够的新文字像素")
    if float(curved_text["minimum_center_delta"]) < limits.minimum_curved_text_center_delta:
        violations.append("弧形改文字没有形成可测量的中心拱起")
    if float(curved_text["minimum_outside_unchanged"]) < limits.minimum_outside_region_unchanged:
        violations.append("弧形改文字意外修改了选择区域外像素")
    if not bool(curved_text["deterministic_pixels"]):
        violations.append("弧形改文字像素不可复现")
    if float(curved_text["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
        violations.append("弧形改文字 P95 超过性能门槛")

    styled_text = text["styled"]
    styled_cases = styled_text["cases"]
    if int(styled_text["case_count"]) < limits.minimum_styled_text_replacement_cases:
        violations.append("受控字效替换样本数量不足")
    observed_effects = [case["visual_effect"] for case in styled_cases]
    if sorted(observed_effects) != ["glow", "gradient", "outline", "plain", "shadow"]:
        violations.append("受控字效替换样式覆盖不完整或重复")
    for case in styled_cases:
        visual_effect = case["visual_effect"]
        if int(case["minimum_primary_pixels"]) < limits.minimum_styled_text_primary_pixels:
            violations.append(f"受控{visual_effect}字效替换主文字像素不足")
        secondary_pixels = int(case["minimum_secondary_pixels"])
        if visual_effect == "plain":
            if secondary_pixels != 0:
                violations.append("普通文字替换意外渲染了辅助色字效")
        elif secondary_pixels < limits.minimum_styled_text_secondary_pixels:
            violations.append(f"受控{visual_effect}字效替换辅助色像素不足")
        if visual_effect == "shadow" and float(case["minimum_secondary_vertical_offset"]) < 2:
            violations.append("受控投影文字替换没有形成可测量的偏移")
        if visual_effect == "gradient" and float(case["minimum_secondary_vertical_offset"]) < 8:
            violations.append("受控渐变文字替换没有形成正确的纵向颜色方向")
        if (
            visual_effect == "glow"
            and int(case["minimum_halo_pixels"]) < limits.minimum_glow_halo_pixels
        ):
            violations.append("受控发光文字替换没有在主文字边界外形成光晕")
        if float(case["minimum_outside_unchanged"]) < limits.minimum_outside_region_unchanged:
            violations.append(f"受控{visual_effect}字效替换误改了选择区域外像素")
        if not bool(case["provenance_matches"]):
            violations.append(f"受控{visual_effect}字效替换来源参数错误")
        if not bool(case["deterministic_pixels"]):
            violations.append(f"受控{visual_effect}字效替换像素不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_styled_text_replacement_p95_ms:
            violations.append(f"受控{visual_effect}字效替换 P95 超过性能门槛")

    detection = result["text_detection"]
    if int(detection["case_count"]) < limits.minimum_text_detection_cases:
        violations.append("文字区域检测样本数量不足")
    for index, case in enumerate(detection["cases"], start=1):
        if not bool(case["detected"]):
            violations.append(f"文字区域检测样本 {index} 未检出")
            continue
        if float(case["overlap"]) < limits.minimum_text_detection_overlap:
            violations.append(f"文字区域检测样本 {index} 区域重叠率不足")
        if float(case["angle_error"]) > limits.maximum_text_detection_angle_error:
            violations.append(f"文字区域检测样本 {index} 角度误差过大")
        if float(case["curve_error"]) > limits.maximum_curved_text_curve_error:
            violations.append(f"直线文字区域检测样本 {index} 被误判为弧形")
        if case["visual_effect"] != "plain":
            violations.append(f"直线文字区域检测样本 {index} 被误判为复杂字效")
        if int(case["fill_color_error"]) > limits.maximum_text_fill_color_error:
            violations.append(f"直线文字区域检测样本 {index} 文字色估计误差过大")
        if not bool(case["deterministic"]):
            violations.append(f"文字区域检测样本 {index} 结果不可复现")
    if float(detection["elapsed_ms"]["p95"]) > limits.maximum_text_detection_p95_ms:
        violations.append("文字区域检测 P95 超过性能门槛")
    perspective_detection = detection["perspective"]
    if not bool(perspective_detection["detected"]):
        violations.append("受控透视文字区域未检出四边形")
    if float(perspective_detection["overlap"]) < limits.minimum_text_detection_overlap:
        violations.append("受控透视文字区域重叠率不足")
    if float(perspective_detection["scale_delta"]) < limits.minimum_perspective_scale_delta:
        violations.append("受控透视文字四边形未保留尺度变化")
    if not bool(perspective_detection["deterministic"]):
        violations.append("受控透视文字检测结果不可复现")

    multi_plane_detection = detection["multi_plane"]
    if not bool(multi_plane_detection["detected"]):
        violations.append("受控双平面文字未完整检出")
    if int(multi_plane_detection["candidate_count"]) < limits.minimum_multi_plane_candidates:
        violations.append("受控双平面文字四边形候选数量不足")
    if not bool(multi_plane_detection["distinct_planes"]):
        violations.append("受控双平面文字被错误合并为同一候选")
    plane_overlaps = [float(value) for value in multi_plane_detection["overlaps"]]
    if len(plane_overlaps) != 2 or min(plane_overlaps) < limits.minimum_multi_plane_overlap:
        violations.append("受控双平面文字区域重叠率不足")
    if not bool(multi_plane_detection["reading_order"]):
        violations.append("受控双平面文字未按阅读顺序排列")
    scale_directions = [float(value) for value in multi_plane_detection["scale_directions"]]
    if (
        len(scale_directions) != 2
        or scale_directions[0] < limits.minimum_multi_plane_scale_delta
        or scale_directions[1] > -limits.minimum_multi_plane_scale_delta
        or not bool(multi_plane_detection["opposite_scale_directions"])
    ):
        violations.append("受控双平面文字未保留相反透视尺度方向")
    if not bool(multi_plane_detection["deterministic"]):
        violations.append("受控双平面文字检测结果不可复现")
    if float(multi_plane_detection["elapsed_ms"]["p95"]) > (limits.maximum_text_detection_p95_ms):
        violations.append("受控双平面文字检测 P95 超过性能门槛")

    occluded_multi_plane = detection["occluded_multi_plane"]
    if not bool(occluded_multi_plane["detected"]):
        violations.append("受控局部遮挡双色双平面文字未完整检出")
    if (
        int(occluded_multi_plane["candidate_count"])
        < limits.minimum_occluded_multi_plane_candidates
    ):
        violations.append("受控局部遮挡双色双平面四边形候选数量不足")
    if not bool(occluded_multi_plane["distinct_planes"]):
        violations.append("受控局部遮挡双色双平面被错误合并")
    if (
        float(occluded_multi_plane["expected_region_iou"])
        < limits.minimum_occluded_multi_plane_region_iou
    ):
        violations.append("受控局部遮挡双色双平面区域没有形成有效交叠")
    if (
        float(occluded_multi_plane["ink_occlusion_ratio"])
        < limits.minimum_occluded_multi_plane_ink_occlusion
    ):
        violations.append("受控局部遮挡双色双平面字形遮挡量不足")
    source_color_distance = int(occluded_multi_plane["source_color_distance"])
    if not (
        limits.minimum_occluded_multi_plane_source_color_distance
        <= source_color_distance
        <= limits.maximum_occluded_multi_plane_source_color_distance
    ):
        violations.append("受控低色差局部遮挡双平面源主色色差不在门禁范围")
    source_axis_similarity = float(occluded_multi_plane["source_axis_similarity"])
    if not (
        limits.minimum_occluded_multi_plane_axis_similarity
        <= source_axis_similarity
        < limits.maximum_occluded_multi_plane_axis_similarity
    ):
        violations.append("受控低色差局部遮挡双平面色轴相似度不在门禁范围")
    occluded_coverages = [float(value) for value in occluded_multi_plane["coverages"]]
    if (
        len(occluded_coverages) != 2
        or min(occluded_coverages) < limits.minimum_occluded_multi_plane_coverage
    ):
        violations.append("受控局部遮挡双色双平面文字覆盖率不足")
    occluded_containments = [float(value) for value in occluded_multi_plane["containments"]]
    if (
        len(occluded_containments) != 2
        or min(occluded_containments) < limits.minimum_occluded_multi_plane_containment
    ):
        violations.append("受控局部遮挡双色双平面候选范围过宽")
    if not bool(occluded_multi_plane["reading_order"]):
        violations.append("受控局部遮挡双色双平面未按阅读顺序排列")
    occluded_directions = [float(value) for value in occluded_multi_plane["scale_directions"]]
    if (
        len(occluded_directions) != 2
        or occluded_directions[0] < limits.minimum_occluded_multi_plane_scale_delta
        or occluded_directions[1] > -limits.minimum_occluded_multi_plane_scale_delta
        or not bool(occluded_multi_plane["opposite_scale_directions"])
    ):
        violations.append("受控局部遮挡双色双平面未保留相反透视尺度方向")
    occluded_fill_errors = [int(value) for value in occluded_multi_plane["fill_color_errors"]]
    if (
        len(occluded_fill_errors) != 2
        or max(occluded_fill_errors) > limits.maximum_occluded_multi_plane_fill_color_error
    ):
        violations.append("受控局部遮挡双色双平面主色估计误差过大")
    if not bool(occluded_multi_plane["deterministic"]):
        violations.append("受控局部遮挡双色双平面检测结果不可复现")
    if float(occluded_multi_plane["elapsed_ms"]["p95"]) > limits.maximum_text_detection_p95_ms:
        violations.append("受控局部遮挡双色双平面检测 P95 超过性能门槛")

    three_plane = detection["three_plane_overlap"]
    if not bool(three_plane["detected"]):
        violations.append("受控局部遮挡异色三平面文字未完整检出")
    if int(three_plane["candidate_count"]) < limits.minimum_three_plane_candidates:
        violations.append("受控局部遮挡异色三平面四边形候选数量不足")
    if not bool(three_plane["distinct_planes"]):
        violations.append("受控局部遮挡异色三平面被错误合并")
    if not bool(three_plane["reading_order"]):
        violations.append("受控局部遮挡异色三平面未按阅读顺序排列")
    three_plane_region_ious = [float(value) for value in three_plane["pairwise_region_ious"]]
    if (
        len(three_plane_region_ious) != 3
        or min(three_plane_region_ious) < limits.minimum_three_plane_pairwise_region_iou
    ):
        violations.append("受控局部遮挡异色三平面区域没有形成两两交叠")
    three_plane_ink_occlusions = [float(value) for value in three_plane["pairwise_ink_occlusions"]]
    if (
        len(three_plane_ink_occlusions) != 3
        or min(three_plane_ink_occlusions) < limits.minimum_three_plane_pairwise_ink_occlusion
        or max(three_plane_ink_occlusions) < limits.minimum_three_plane_peak_ink_occlusion
    ):
        violations.append("受控局部遮挡异色三平面字形遮挡量不足")
    three_plane_coverages = [float(value) for value in three_plane["coverages"]]
    if (
        len(three_plane_coverages) != 3
        or min(three_plane_coverages) < limits.minimum_three_plane_coverage
    ):
        violations.append("受控局部遮挡异色三平面文字覆盖率不足")
    three_plane_containments = [float(value) for value in three_plane["containments"]]
    if (
        len(three_plane_containments) != 3
        or min(three_plane_containments) < limits.minimum_three_plane_containment
    ):
        violations.append("受控局部遮挡异色三平面候选范围过宽")
    three_plane_directions = [float(value) for value in three_plane["scale_directions"]]
    if (
        len(three_plane_directions) != 3
        or max(three_plane_directions) < limits.minimum_three_plane_scale_delta
        or min(three_plane_directions) > -limits.minimum_three_plane_scale_delta
        or not bool(three_plane["divergent_scale_directions"])
    ):
        violations.append("受控局部遮挡异色三平面未保留不同透视尺度方向")
    three_plane_fill_errors = [int(value) for value in three_plane["fill_color_errors"]]
    if (
        len(three_plane_fill_errors) != 3
        or max(three_plane_fill_errors) > limits.maximum_three_plane_fill_color_error
    ):
        violations.append("受控局部遮挡异色三平面主色估计误差过大")
    if not bool(three_plane["deterministic"]):
        violations.append("受控局部遮挡异色三平面检测结果不可复现")
    if float(three_plane["elapsed_ms"]["p95"]) > limits.maximum_text_detection_p95_ms:
        violations.append("受控局部遮挡异色三平面检测 P95 超过性能门槛")
    same_color_safety = three_plane["same_color_safety"]
    if (
        int(same_color_safety["perspective_candidate_count"])
        > limits.maximum_same_color_occluded_plane_candidates
    ):
        violations.append("同色遮挡多平面被启发式错误拆分")
    if not bool(same_color_safety["deterministic"]):
        violations.append("同色遮挡多平面安全回退结果不可复现")
    if float(same_color_safety["elapsed_ms"]["p95"]) > limits.maximum_text_detection_p95_ms:
        violations.append("同色遮挡多平面安全回退 P95 超过性能门槛")

    curved_detection = detection["curved"]
    if int(curved_detection["case_count"]) < limits.minimum_curved_text_detection_cases:
        violations.append("受控弧形文字检测样本数量不足")
    for index, case in enumerate(curved_detection["cases"], start=1):
        if not bool(case["detected"]):
            violations.append(f"受控弧形文字检测样本 {index} 未检出")
            continue
        if float(case["overlap"]) < limits.minimum_curved_text_detection_overlap:
            violations.append(f"受控弧形文字检测样本 {index} 区域重叠率不足")
        if float(case["curve_error"]) > limits.maximum_curved_text_curve_error:
            violations.append(f"受控弧形文字检测样本 {index} 弧度误差过大")
        if not bool(case["deterministic"]):
            violations.append(f"受控弧形文字检测样本 {index} 结果不可复现")

    styled_detection = detection["styled"]
    if int(styled_detection["case_count"]) < limits.minimum_styled_text_detection_cases:
        violations.append("受控复杂字效检测样本数量不足")
    for index, case in enumerate(styled_detection["cases"], start=1):
        if not bool(case["detected"]):
            violations.append(f"受控复杂字效检测样本 {index} 未检出或分类错误")
            continue
        if case["detected_effect"] != case["expected_effect"]:
            violations.append(f"受控复杂字效检测样本 {index} 分类错误")
        if int(case["fill_color_error"]) > limits.maximum_styled_text_color_error:
            violations.append(f"受控复杂字效检测样本 {index} 主色参数估计误差过大")
        if int(case["effect_color_error"]) > limits.maximum_styled_text_color_error:
            violations.append(f"受控复杂字效检测样本 {index} 辅助色参数估计误差过大")
        if int(case["effect_size_error"]) > limits.maximum_styled_text_effect_size_error:
            violations.append(f"受控复杂字效检测样本 {index} 强度参数估计误差过大")
        if float(case["overlap"]) < limits.minimum_styled_text_detection_overlap:
            violations.append(f"受控复杂字效检测样本 {index} 区域重叠率不足")
        if not bool(case["deterministic"]):
            violations.append(f"受控复杂字效检测样本 {index} 结果不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_styled_text_detection_p95_ms:
            violations.append(f"受控复杂字效检测样本 {index} P95 超过性能门槛")

    surface_detection = detection["surface_background"]
    if int(surface_detection["case_count"]) < limits.minimum_surface_background_text_cases:
        violations.append("受控非均匀背景文字检测样本数量不足")
    for index, case in enumerate(surface_detection["cases"], start=1):
        if not bool(case["detected"]):
            violations.append(f"受控非均匀背景文字检测样本 {index} 未检出或分类错误")
            continue
        if case["detected_effect"] != case["expected_effect"]:
            violations.append(f"受控非均匀背景文字检测样本 {index} 分类错误")
        if int(case["fill_color_error"]) > limits.maximum_surface_background_fill_color_error:
            violations.append(f"受控非均匀背景文字检测样本 {index} 主色参数估计误差过大")
        if int(case["effect_color_error"]) > limits.maximum_surface_background_effect_color_error:
            violations.append(f"受控非均匀背景文字检测样本 {index} 辅助色参数估计误差过大")
        if int(case["effect_size_error"]) > limits.maximum_surface_background_effect_size_error:
            violations.append(f"受控非均匀背景文字检测样本 {index} 强度参数估计误差过大")
        if float(case["overlap"]) < limits.minimum_surface_background_text_overlap:
            violations.append(f"受控非均匀背景文字检测样本 {index} 区域重叠率不足")
        if not bool(case["deterministic"]):
            violations.append(f"受控非均匀背景文字检测样本 {index} 结果不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_surface_background_text_p95_ms:
            violations.append(f"受控非均匀背景文字检测样本 {index} P95 超过性能门槛")

    structured_detection = detection["structured_background"]
    if int(structured_detection["case_count"]) < limits.minimum_structured_background_text_cases:
        violations.append("受控结构纹理背景文字检测样本数量不足")
    for index, case in enumerate(structured_detection["cases"], start=1):
        if not bool(case["detected"]):
            violations.append(f"受控结构纹理背景文字检测样本 {index} 未检出或分类错误")
            continue
        if case["detected_effect"] != case["expected_effect"]:
            violations.append(f"受控结构纹理背景文字检测样本 {index} 分类错误")
        if int(case["fill_color_error"]) > limits.maximum_structured_background_fill_color_error:
            violations.append(f"受控结构纹理背景文字检测样本 {index} 主色参数估计误差过大")
        if (
            int(case["effect_color_error"])
            > limits.maximum_structured_background_effect_color_error
        ):
            violations.append(f"受控结构纹理背景文字检测样本 {index} 辅助色参数估计误差过大")
        if int(case["effect_size_error"]) > limits.maximum_structured_background_effect_size_error:
            violations.append(f"受控结构纹理背景文字检测样本 {index} 强度参数估计误差过大")
        if float(case["overlap"]) < limits.minimum_structured_background_text_overlap:
            violations.append(f"受控结构纹理背景文字检测样本 {index} 区域重叠率不足")
        if not bool(case["deterministic"]):
            violations.append(f"受控结构纹理背景文字检测样本 {index} 结果不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_structured_background_text_p95_ms:
            violations.append(f"受控结构纹理背景文字检测样本 {index} P95 超过性能门槛")

    photo_detection = detection["photo_background"]
    if int(photo_detection["case_count"]) < limits.minimum_photo_background_text_cases:
        violations.append("受控照片式背景文字检测样本数量不足")
    if int(photo_detection["negative_case_count"]) < limits.minimum_photo_background_negative_cases:
        violations.append("受控照片式无文字样本数量不足")
    if (
        int(photo_detection["negative_candidate_count"])
        > limits.maximum_photo_background_negative_candidates
    ):
        violations.append("受控照片式无文字样本产生误报候选")
    if not bool(photo_detection["negative_deterministic"]):
        violations.append("受控照片式无文字样本结果不可复现")
    if (
        float(photo_detection["negative_elapsed_ms"]["p95"])
        > limits.maximum_photo_background_text_p95_ms
    ):
        violations.append("受控照片式无文字样本 P95 超过性能门槛")
    for index, case in enumerate(photo_detection["cases"], start=1):
        if not bool(case["detected"]):
            violations.append(f"受控照片式背景文字检测样本 {index} 未检出")
            continue
        if not bool(case["top_ranked_match"]):
            violations.append(f"受控照片式背景文字检测样本 {index} 首选候选错误")
        if case["detected_effect"] != case["expected_effect"]:
            violations.append(f"受控照片式背景文字检测样本 {index} 分类错误")
        if int(case["fill_color_error"]) > limits.maximum_photo_background_fill_color_error:
            violations.append(f"受控照片式背景文字检测样本 {index} 主色误差过大")
        if int(case["effect_color_error"]) > limits.maximum_photo_background_effect_color_error:
            violations.append(f"受控照片式背景文字检测样本 {index} 辅助色误差过大")
        if int(case["effect_size_error"]) > limits.maximum_photo_background_effect_size_error:
            violations.append(f"受控照片式背景文字检测样本 {index} 强度误差过大")
        if float(case["overlap"]) < limits.minimum_photo_background_text_overlap:
            violations.append(f"受控照片式背景文字检测样本 {index} 区域重叠率不足")
        if float(case["region_width"]) > limits.maximum_photo_background_region_width:
            violations.append(f"受控照片式背景文字检测样本 {index} 区域过宽")
        if float(case["region_height"]) > limits.maximum_photo_background_region_height:
            violations.append(f"受控照片式背景文字检测样本 {index} 区域过高")
        if not bool(case["deterministic"]):
            violations.append(f"受控照片式背景文字检测样本 {index} 结果不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_photo_background_text_p95_ms:
            violations.append(f"受控照片式背景文字检测样本 {index} P95 超过性能门槛")

    if int(photo_detection["layout_case_count"]) < limits.minimum_photo_layout_cases:
        violations.append("受控照片排版文字检测样本数量不足")
    for index, case in enumerate(photo_detection["layout_cases"], start=1):
        if int(case["matched_count"]) < int(case["required_match_count"]):
            violations.append(f"受控照片排版文字检测样本 {index} 存在漏检")
        if not bool(case["top_ranked_match"]):
            violations.append(f"受控照片排版文字检测样本 {index} 首选候选错误")
        if not bool(case["reading_order"]):
            violations.append(f"受控照片排版文字检测样本 {index} 阅读顺序错误")
        if float(case["minimum_overlap"]) < limits.minimum_photo_layout_overlap:
            violations.append(f"受控照片排版文字检测样本 {index} 区域重叠率不足")
        if int(case["maximum_fill_color_error"]) > limits.maximum_photo_layout_fill_color_error:
            violations.append(f"受控照片排版文字检测样本 {index} 主色误差过大")
        if not bool(case["deterministic"]):
            violations.append(f"受控照片排版文字检测样本 {index} 结果不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_photo_background_text_p95_ms:
            violations.append(f"受控照片排版文字检测样本 {index} P95 超过性能门槛")

    if int(photo_detection["jpeg_case_count"]) < limits.minimum_jpeg_photo_cases:
        violations.append("受控 JPEG 照片文字检测样本数量不足")
    if int(photo_detection["jpeg_negative_case_count"]) < limits.minimum_jpeg_photo_negative_cases:
        violations.append("受控 JPEG 无文字负样本数量不足")
    if (
        int(photo_detection["jpeg_negative_candidate_count"])
        > limits.maximum_jpeg_photo_negative_candidates
    ):
        violations.append("受控 JPEG 无文字负样本产生误报候选")
    if not bool(photo_detection["jpeg_negative_deterministic"]):
        violations.append("受控 JPEG 无文字负样本结果不可复现")
    for index, case in enumerate(photo_detection["jpeg_cases"], start=1):
        if int(case["matched_count"]) < int(case["required_match_count"]):
            violations.append(f"受控 JPEG 照片文字检测样本 {index} 存在漏检")
        if not bool(case["top_ranked_match"]):
            violations.append(f"受控 JPEG 照片文字检测样本 {index} 首选候选错误")
        if not bool(case["reading_order"]):
            violations.append(f"受控 JPEG 照片文字检测样本 {index} 阅读顺序错误")
        if float(case["minimum_overlap"]) < limits.minimum_jpeg_photo_overlap:
            violations.append(f"受控 JPEG 照片文字检测样本 {index} 区域重叠率不足")
        if int(case["maximum_fill_color_error"]) > limits.maximum_jpeg_photo_fill_color_error:
            violations.append(f"受控 JPEG 照片文字检测样本 {index} 主色误差过大")
        if int(case["maximum_effect_color_error"]) > limits.maximum_jpeg_photo_effect_color_error:
            violations.append(f"受控 JPEG 照片文字检测样本 {index} 辅助色误差过大")
        if int(case["maximum_effect_size_error"]) > limits.maximum_jpeg_photo_effect_size_error:
            violations.append(f"受控 JPEG 照片文字检测样本 {index} 字效强度误差过大")
        if not bool(case["deterministic"]):
            violations.append(f"受控 JPEG 照片文字检测样本 {index} 结果不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_photo_background_text_p95_ms:
            violations.append(f"受控 JPEG 照片文字检测样本 {index} P95 超过性能门槛")

    curved_ocr = result["curved_ocr_rectification"]
    if int(curved_ocr["case_count"]) < limits.minimum_curved_ocr_rectification_cases:
        violations.append("弧形文字 OCR 校直样本数量不足")
    for index, case in enumerate(curved_ocr["cases"], start=1):
        if float(case["input_delta_px"]) < limits.minimum_curved_ocr_input_delta_px:
            violations.append(f"弧形文字 OCR 校直样本 {index} 输入弧度不足")
        if float(case["maximum_residual_delta_px"]) > limits.maximum_curved_ocr_residual_delta_px:
            violations.append(f"弧形文字 OCR 校直样本 {index} 残余弧度过大")
        if float(case["maximum_residual_ratio"]) > limits.maximum_curved_ocr_residual_ratio:
            violations.append(f"弧形文字 OCR 校直样本 {index} 改善比例不足")
        if not bool(case["deterministic_pixels"]):
            violations.append(f"弧形文字 OCR 校直样本 {index} 像素不可复现")
        if float(case["elapsed_ms"]["p95"]) > limits.maximum_curved_ocr_p95_ms:
            violations.append(f"弧形文字 OCR 校直样本 {index} P95 超过性能门槛")

    font_matching = result["font_matching"]
    if int(font_matching["case_count"]) < limits.minimum_font_match_cases:
        violations.append("字体推荐样本数量不足")
    for index, case in enumerate(font_matching["cases"], start=1):
        if not bool(case["top1_correct"]):
            violations.append(f"字体推荐样本 {index} Top-1 不正确")
        if not bool(case["deterministic"]):
            violations.append(f"字体推荐样本 {index} 结果不可复现")
    if float(font_matching["elapsed_ms"]["p95"]) > limits.maximum_font_match_p95_ms:
        violations.append("字体推荐 P95 超过性能门槛")

    selection = result["smart_selection"]
    if int(selection["case_count"]) < limits.minimum_smart_selection_cases:
        violations.append("智能选区样本数量不足")
    for index, case in enumerate(selection["cases"], start=1):
        if not bool(case["detected"]):
            violations.append(f"智能选区样本 {index} 未检出")
            continue
        if case["source"] != case["expected_source"]:
            violations.append(f"智能选区样本 {index} 分析来源不正确")
        if int(case["candidate_count"]) < int(case["expected_min_candidates"]):
            violations.append(f"智能选区样本 {index} 候选数量不足")
        if int(case["color_split_candidate_count"]) < int(case["expected_min_color_split"]):
            violations.append(f"智能选区样本 {index} 颜色分离候选数量不足")
        if not bool(case["split_probe_exclusive"]):
            violations.append(f"智能选区样本 {index} 颜色分离蒙版不互斥")
        if int(case["shape_split_candidate_count"]) < int(case["expected_min_shape_split"]):
            violations.append(f"智能选区样本 {index} 形状分离候选数量不足")
        if not bool(case["shape_probe_exclusive"]):
            violations.append(f"智能选区样本 {index} 形状分离蒙版不互斥")
        if float(case["coverage"]) < limits.minimum_smart_selection_coverage:
            violations.append(f"智能选区样本 {index} 主体覆盖率不足")
        if float(case["iou"]) < limits.minimum_smart_selection_iou:
            violations.append(f"智能选区样本 {index} 区域重叠率不足")
        if not bool(case["deterministic"]):
            violations.append(f"智能选区样本 {index} 结果不可复现")
    if float(selection["elapsed_ms"]["p95"]) > limits.maximum_smart_selection_p95_ms:
        violations.append("智能选区 P95 超过性能门槛")
    return violations


def run(
    profile_name: str = "smoke",
    *,
    image_size: tuple[int, int] | None = None,
    repetitions: int | None = None,
) -> dict[str, Any]:
    if profile_name not in PROFILES:
        raise ValueError(f"未知智能改图门禁 profile：{profile_name}")
    profile = dict(PROFILES[profile_name])
    if image_size is not None:
        profile["image_size"] = image_size
    if repetitions is not None:
        profile["repetitions"] = repetitions
    size = tuple(profile["image_size"])
    if len(size) != 2 or any(isinstance(value, bool) or int(value) < 32 for value in size):
        raise ValueError("image_size 必须包含两个不小于 32 的整数")
    normalized_size = (int(size[0]), int(size[1]))
    repeat_count = profile["repetitions"]
    if isinstance(repeat_count, bool) or not isinstance(repeat_count, int) or repeat_count < 2:
        raise ValueError("repetitions 必须是不小于 2 的整数")
    backgrounds = tuple(tuple(color) for color in profile["backgrounds"])

    with tempfile.TemporaryDirectory(prefix="light-image-editor-smart-edit-gate-") as raw:
        root = Path(raw)
        provider = MockProvider(output_directory=root / "outputs")
        upscale = _run_upscale_probe(root, provider, normalized_size, repeat_count)
        remove_background, removal_revision = _run_background_probe(
            root,
            provider,
            normalized_size,
            repeat_count,
            backgrounds,
        )
        edge_background = _run_edge_background_probe(
            root,
            provider,
            normalized_size,
            repeat_count,
        )
        alpha_refinement = _run_alpha_refinement_probe(provider, removal_revision)
        background_replacement = _run_background_replacement_probe(
            root,
            removal_revision,
            repeat_count,
        )
        region_repair = _run_region_repair_probe(
            root,
            provider,
            normalized_size,
            repeat_count,
        )
        mask_repair = _run_mask_repair_probe(
            root,
            provider,
            normalized_size,
            repeat_count,
        )
        text_replacement = _run_text_probe(
            root,
            provider,
            normalized_size,
            repeat_count,
        )
        text_detection = _run_text_detection_probe(
            root,
            normalized_size,
            repeat_count,
        )
        curved_ocr_rectification = _run_curved_ocr_rectification_probe(repeat_count)
        font_matching = _run_font_match_probe(root, repeat_count)
        smart_selection = _run_smart_selection_probe(root, repeat_count)

    thresholds = SmartEditGateThresholds(minimum_background_cases=len(backgrounds))
    result: dict[str, Any] = {
        "schema_version": 1,
        "application_version": __version__,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "profile": profile_name,
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "pillow": pillow_version,
        },
        "fixtures": {
            "image_size": list(normalized_size),
            "repetitions": repeat_count,
            "background_count": len(backgrounds),
            "backgrounds": [list(color) for color in backgrounds],
        },
        "upscale": upscale,
        "remove_background": remove_background,
        "edge_background": edge_background,
        "alpha_refinement": alpha_refinement,
        "background_replacement": background_replacement,
        "region_repair": region_repair,
        "mask_repair": mask_repair,
        "text_replacement": text_replacement,
        "text_detection": text_detection,
        "curved_ocr_rectification": curved_ocr_rectification,
        "font_matching": font_matching,
        "smart_selection": smart_selection,
        "thresholds": asdict(thresholds),
        "scope_limit": (
            "离线确定性基线；文字区域检测以10°粗搜和1°局部精修覆盖"
            "-90°～+90°受控普通印刷体，精确竖排方向需要用户选择，"
            "受控单平面、空间分离双平面、低色差不同色轴双平面及三种可区分色轴且有限"
            "局部遮挡的三平面梯形透视可输出独立四角并按阅读顺序切换；完全同色样本"
            "安全回退为合并候选，同色深度分层、重度遮挡和不可见文字恢复不在基线内，"
            "去背景贴边/渐变/半透明样本为受控合成图，不代表复杂毛发、"
            "像素蒙版来自离线颜色/Alpha 连通域与可见轮廓厚度启发式，只覆盖"
            "受控异色及同色可见中等宽接触分离，不代表语义分割、深度遮挡或不可见像素"
            "恢复；受控弧形普通印刷体只验证单行二次基线检测、手动弧度替换排版及"
            "OCR 前的几何校直；受控字效可分类水平普通印刷体的纯色、描边、偏移投影、"
            "线性渐变和扩散发光，并对受控样本估计可校正的可见主色、辅助色及原图像素强度；"
            "受控平滑光照渐变与低频纹理背景使用局部稳健二次 RGB 表面分离纯色和发光文字，"
            "受控周期性高频纹理与正交亮度结构使用局部中值背景和全局色向端点联合分析，"
            "受控照片式合成背景使用局部对比度、自适应行列密度、空间覆盖和重复实心颜色证据，"
            "从多随机种子的多尺度纹理与密集枝状边缘中分离纯色/发光文字，要求正确文字排在"
            "首选候选，并以同背景无字样本累计零候选约束误报；JPEG 仅验证单次 4:2:0 编码"
            "质量 95/75/50 下的水平纯色、发光、小字号、双行和有限局部遮挡；不代表真实照片"
            "语义内容、复杂自然对象、重复转码、更低质量或强遮挡下的精确参数还原，也不代表"
            "任意色轴、层数或重度遮挡的多平面文字、多层/非均匀发光、纹理/三维/"
            "非线性变形艺术字、弧形文字真实识别准确率、复杂纹理修复或生产 OCR 质量"
        ),
    }
    result["violations"] = evaluate_smart_edit_gate(result, thresholds)
    result["passed"] = not result["violations"]
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="smoke")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-fail", action="store_true", help="记录失败但返回成功退出码")
    arguments = parser.parse_args()
    if (arguments.width is None) != (arguments.height is None):
        parser.error("--width 与 --height 必须同时提供")
    return arguments


def main() -> int:
    arguments = _parse_args()
    size = None if arguments.width is None else (arguments.width, arguments.height)
    result = run(
        arguments.profile,
        image_size=size,
        repetitions=arguments.repetitions,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if arguments.output is not None:
        destination = arguments.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized + "\n", encoding="utf-8")
    return 0 if result["passed"] or arguments.no_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
