"""Deterministic non-destructive image rendering and analysis."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from image_ops import apply_text_watermark, watermark_font

from .annotations import AnnotationObject
from .commands import EditCommand, EditTimeline

CurvePoints = tuple[tuple[float, float], ...]
IDENTITY_CURVE: CurvePoints = ((0.0, 0.0), (1.0, 1.0))


@dataclass(frozen=True)
class GeometrySpec:
    rotation: int = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    fine_angle: float = 0.0
    auto_crop: bool = True
    background: str = "#ffffff"
    perspective: tuple[float, float, float, float, float, float, float, float] | None = None
    canvas_margins: tuple[int, int, int, int] = (0, 0, 0, 0)
    canvas_background: str = "#ffffff"


@dataclass(frozen=True)
class ColorSpec:
    brightness: int = 0
    contrast: int = 0
    saturation: int = 0
    grayscale: bool = False
    auto_contrast: bool = False
    temperature: int = 0
    tint: int = 0
    shadows: int = 0
    highlights: int = 0
    denoise: int = 0
    sharpen: int = 0
    level_black: int = 0
    level_white: int = 255
    level_gamma: float = 1.0
    curves: dict[str, CurvePoints] = field(
        default_factory=lambda: {
            "rgb": IDENTITY_CURVE,
            "red": IDENTITY_CURVE,
            "green": IDENTITY_CURVE,
            "blue": IDENTITY_CURVE,
        }
    )


@dataclass(frozen=True)
class AnnotationSpec:
    watermark_text: str = ""
    watermark_position: str = "右下"
    watermark_opacity: int = 35
    watermark_size_percent: int = 5


@dataclass(frozen=True)
class SizeSpec:
    exact_width: int | None = None
    exact_height: int | None = None
    max_width: int | None = None
    max_height: int | None = None


@dataclass(frozen=True)
class RenderPlan:
    geometry: GeometrySpec = GeometrySpec()
    crop_box: tuple[float, float, float, float] | None = None
    color: ColorSpec = field(default_factory=ColorSpec)
    annotation: AnnotationSpec = AnnotationSpec()
    annotations: tuple[AnnotationObject, ...] = ()
    size: SizeSpec = SizeSpec()


@dataclass(frozen=True)
class StraightenResult:
    angle: float
    confidence: float
    reliable: bool


def reduce_timeline(timeline: EditTimeline) -> RenderPlan:
    plan = RenderPlan()
    for command in timeline.active_commands:
        plan = apply_command_to_plan(plan, command)
    return plan


def apply_command_to_plan(plan: RenderPlan, command: EditCommand) -> RenderPlan:
    params = command.params
    if command.kind == "rotate":
        degrees = _bounded_int(params.get("degrees", 0), -360, 360, "旋转角度")
        geometry = replace(plan.geometry, rotation=(plan.geometry.rotation + degrees) % 360)
        return replace(plan, geometry=geometry)
    if command.kind == "flip_horizontal":
        return replace(
            plan,
            geometry=replace(plan.geometry, flip_horizontal=not plan.geometry.flip_horizontal),
        )
    if command.kind == "flip_vertical":
        return replace(
            plan,
            geometry=replace(plan.geometry, flip_vertical=not plan.geometry.flip_vertical),
        )
    if command.kind == "fine_rotate":
        angle = _bounded_float(params.get("angle", 0.0), -45.0, 45.0, "精细旋转")
        background = str(params.get("background", "#ffffff"))
        _parse_hex_color(background)
        return replace(
            plan,
            geometry=replace(
                plan.geometry,
                fine_angle=angle,
                auto_crop=bool(params.get("auto_crop", True)),
                background=background,
            ),
        )
    if command.kind == "perspective":
        raw_quad = params.get("quad")
        if raw_quad is None:
            return replace(plan, geometry=replace(plan.geometry, perspective=None))
        if not isinstance(raw_quad, list) or len(raw_quad) != 8:
            raise ValueError("透视四边形必须包含八个归一化坐标。")
        quad = tuple(float(value) for value in raw_quad)
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in quad):
            raise ValueError("透视坐标必须位于 0 到 1。")
        _validate_perspective_quad(quad)
        return replace(plan, geometry=replace(plan.geometry, perspective=quad))  # type: ignore[arg-type]
    if command.kind == "canvas_expand":
        raw_margins = params.get("margins", [0, 0, 0, 0])
        if not isinstance(raw_margins, list) or len(raw_margins) != 4:
            raise ValueError("画布边距必须包含左、上、右、下四个值。")
        margins = tuple(_bounded_int(value, 0, 10_000, "画布边距") for value in raw_margins)
        background = str(params.get("background", "#ffffff"))
        _parse_hex_color(background)
        return replace(
            plan,
            geometry=replace(
                plan.geometry,
                canvas_margins=margins,  # type: ignore[arg-type]
                canvas_background=background,
            ),
        )
    if command.kind == "crop":
        raw_box = params.get("box")
        if raw_box is None:
            return replace(plan, crop_box=None)
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            raise ValueError("裁剪区域必须包含四个归一化坐标。")
        box = (
            float(raw_box[0]),
            float(raw_box[1]),
            float(raw_box[2]),
            float(raw_box[3]),
        )
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in box):
            raise ValueError("裁剪坐标必须位于 0 到 1。")
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("裁剪区域无效。")
        return replace(plan, crop_box=box)
    if command.kind == "adjust":
        name = str(params.get("name", ""))
        if not hasattr(plan.color, name) or name in {"curves"}:
            raise ValueError(f"未知颜色调整：{name}")
        value: Any = params.get("value", 0)
        if name in {"grayscale", "auto_contrast"}:
            value = bool(value)
        else:
            value = _bounded_int(value, -100, 100, name)
            if name in {"denoise", "sharpen"} and value < 0:
                raise ValueError(f"{name} 强度必须位于 0 到 100。")
        return replace(plan, color=replace(plan.color, **{name: value}))
    if command.kind == "levels":
        black = _bounded_int(params.get("black", 0), 0, 254, "黑场")
        white = _bounded_int(params.get("white", 255), 1, 255, "白场")
        gamma = _bounded_float(params.get("gamma", 1.0), 0.1, 10.0, "Gamma")
        if black >= white:
            raise ValueError("黑场必须小于白场。")
        return replace(
            plan,
            color=replace(
                plan.color,
                level_black=black,
                level_white=white,
                level_gamma=gamma,
            ),
        )
    if command.kind == "curve":
        channel = str(params.get("channel", "rgb"))
        if channel not in {"rgb", "red", "green", "blue"}:
            raise ValueError(f"未知曲线通道：{channel}")
        points = _curve_points(params.get("points", []))
        curves = dict(plan.color.curves)
        curves[channel] = points
        return replace(plan, color=replace(plan.color, curves=curves))
    if command.kind == "watermark":
        annotation = AnnotationSpec(
            watermark_text=str(params.get("text", "")),
            watermark_position=str(params.get("position", "右下")),
            watermark_opacity=_bounded_int(params.get("opacity", 35), 0, 100, "水印透明度"),
            watermark_size_percent=_bounded_int(params.get("size_percent", 5), 1, 30, "水印字号"),
        )
        return replace(plan, annotation=annotation)
    if command.kind == "annotation_add":
        annotation_object = AnnotationObject.from_json(dict(params.get("object", {})))
        if any(item.id == annotation_object.id for item in plan.annotations):
            raise ValueError("标注对象 ID 重复。")
        return replace(plan, annotations=(*plan.annotations, annotation_object))
    if command.kind == "annotation_update":
        object_id = str(params.get("id", ""))
        patch = params.get("patch", {})
        if not isinstance(patch, dict):
            raise ValueError("标注更新参数必须是对象。")
        found = False
        updated_objects: list[AnnotationObject] = []
        for current_object in plan.annotations:
            if current_object.id == object_id:
                updated_objects.append(current_object.with_patch(patch))
                found = True
            else:
                updated_objects.append(current_object)
        if not found:
            raise ValueError("找不到要更新的标注对象。")
        return replace(plan, annotations=tuple(updated_objects))
    if command.kind == "annotation_delete":
        object_id = str(params.get("id", ""))
        remaining_objects = tuple(item for item in plan.annotations if item.id != object_id)
        if len(remaining_objects) == len(plan.annotations):
            raise ValueError("找不到要删除的标注对象。")
        return replace(plan, annotations=remaining_objects)
    if command.kind == "resize":
        size = SizeSpec(
            exact_width=_optional_positive_int(params.get("exact_width")),
            exact_height=_optional_positive_int(params.get("exact_height")),
            max_width=_optional_positive_int(params.get("max_width")),
            max_height=_optional_positive_int(params.get("max_height")),
        )
        if (size.exact_width is None) != (size.exact_height is None):
            raise ValueError("精确尺寸必须同时提供宽度和高度。")
        return replace(plan, size=size)
    if command.kind == "export":
        return plan
    raise ValueError(f"渲染器不支持命令：{command.kind}")


def render_image(
    base_image: Image.Image,
    timeline_or_plan: EditTimeline | RenderPlan,
    *,
    preview_max_size: tuple[int, int] | None = None,
) -> Image.Image:
    plan = (
        reduce_timeline(timeline_or_plan)
        if isinstance(timeline_or_plan, EditTimeline)
        else timeline_or_plan
    )
    result = _apply_geometry(base_image, plan.geometry)
    result = _apply_crop(result, plan.crop_box)
    result = _apply_color(result, plan.color)
    result = _apply_annotations(result, plan.annotations)
    if plan.annotation.watermark_text.strip():
        result = apply_text_watermark(
            result,
            plan.annotation.watermark_text,
            plan.annotation.watermark_position,
            plan.annotation.watermark_opacity,
            plan.annotation.watermark_size_percent,
        )
    result = _apply_size(result, plan.size)
    if preview_max_size is not None:
        result.thumbnail(preview_max_size, Image.Resampling.LANCZOS)
    return result


def histogram(image: Image.Image, max_pixels: int = 1_000_000) -> dict[str, tuple[int, ...]]:
    sample = image
    owns_sample = False
    if image.width * image.height > max_pixels:
        scale = math.sqrt(max_pixels / (sample.width * sample.height))
        sample = sample.resize(
            (max(1, round(sample.width * scale)), max(1, round(sample.height * scale))),
            Image.Resampling.BILINEAR,
            reducing_gap=2.0,
        )
        owns_sample = True
    if sample.mode != "RGB":
        converted = sample.convert("RGB")
        if owns_sample:
            sample.close()
        sample = converted
        owns_sample = True
    try:
        pixels = np.asarray(sample, dtype=np.uint8)
        red = np.bincount(pixels[..., 0].ravel(), minlength=256)
        green = np.bincount(pixels[..., 1].ravel(), minlength=256)
        blue = np.bincount(pixels[..., 2].ravel(), minlength=256)
        luma = np.clip(
            np.rint(pixels[..., 0] * 0.2126 + pixels[..., 1] * 0.7152 + pixels[..., 2] * 0.0722),
            0,
            255,
        ).astype(np.uint8)
        luminance = np.bincount(luma.ravel(), minlength=256)
        return {
            "luma": tuple(int(value) for value in luminance),
            "red": tuple(int(value) for value in red),
            "green": tuple(int(value) for value in green),
            "blue": tuple(int(value) for value in blue),
        }
    finally:
        if owns_sample:
            sample.close()


def detect_straighten_angle(image: Image.Image) -> StraightenResult:
    sample = ImageOps.grayscale(image)
    if max(sample.size) > 1600:
        sample.thumbnail((1600, 1600), Image.Resampling.BILINEAR)
    array = np.asarray(sample, dtype=np.float32) / 255.0
    if min(array.shape) < 16:
        return StraightenResult(0.0, 0.0, False)
    gy, gx = np.gradient(array)
    magnitude = np.hypot(gx, gy)
    if int(np.count_nonzero(magnitude > 0.02)) < 64:
        return StraightenResult(0.0, 0.0, False)
    edge_image = Image.fromarray(np.rint(np.clip(magnitude * 4.0, 0.0, 1.0) * 255).astype(np.uint8))

    def projection_score(angle: float) -> float:
        rotated = edge_image.rotate(
            angle,
            resample=Image.Resampling.BILINEAR,
            expand=False,
            fillcolor=0,
        )
        edges = np.asarray(rotated, dtype=np.float32) / 255.0
        return float(np.var(edges.sum(axis=0)) + np.var(edges.sum(axis=1)))

    coarse_angles = np.arange(-15.0, 15.001, 0.25)
    coarse_scores = np.array([projection_score(float(angle)) for angle in coarse_angles])
    coarse_best = float(coarse_angles[int(np.argmax(coarse_scores))])
    fine_angles = np.arange(coarse_best - 0.25, coarse_best + 0.251, 0.05)
    fine_scores = np.array([projection_score(float(angle)) for angle in fine_angles])
    correction = round(float(fine_angles[int(np.argmax(fine_scores))]), 2)
    baseline = float(np.median(coarse_scores))
    confidence = float(np.max(fine_scores) / max(baseline, 1e-6))
    reliable = confidence >= 1.5 and abs(correction) >= 0.05
    return StraightenResult(correction, confidence, reliable)


def _apply_geometry(image: Image.Image, spec: GeometrySpec) -> Image.Image:
    result = image.copy()
    rotation = spec.rotation % 360
    if rotation == 90:
        result = result.transpose(Image.Transpose.ROTATE_270)
    elif rotation == 180:
        result = result.transpose(Image.Transpose.ROTATE_180)
    elif rotation == 270:
        result = result.transpose(Image.Transpose.ROTATE_90)
    elif rotation:
        result = result.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
    if spec.flip_horizontal:
        result = ImageOps.mirror(result)
    if spec.flip_vertical:
        result = ImageOps.flip(result)
    if abs(spec.fine_angle) >= 0.005:
        original_size = result.size
        fill = (0, 0, 0, 0) if "A" in result.getbands() else _parse_hex_color(spec.background)
        result = result.rotate(
            -spec.fine_angle,
            expand=True,
            resample=Image.Resampling.BICUBIC,
            fillcolor=fill,
        )
        if spec.auto_crop:
            crop_width, crop_height = _largest_rotated_rect(
                original_size[0], original_size[1], math.radians(spec.fine_angle)
            )
            crop_width = max(1, min(result.width, int(crop_width)))
            crop_height = max(1, min(result.height, int(crop_height)))
            left = (result.width - crop_width) // 2
            top = (result.height - crop_height) // 2
            result = result.crop((left, top, left + crop_width, top + crop_height))
    if spec.perspective is not None:
        tlx, tly, trx, try_, brx, bry, blx, bly = spec.perspective
        quad = (
            tlx * result.width,
            tly * result.height,
            blx * result.width,
            bly * result.height,
            brx * result.width,
            bry * result.height,
            trx * result.width,
            try_ * result.height,
        )
        result = result.transform(
            result.size,
            Image.Transform.QUAD,
            quad,
            resample=Image.Resampling.BICUBIC,
        )
    left, top, right, bottom = spec.canvas_margins
    if any(spec.canvas_margins):
        fill = _parse_hex_color(spec.canvas_background)
        mode = "RGBA" if "A" in result.getbands() else "RGB"
        background = (*fill, 255) if mode == "RGBA" else fill
        expanded = Image.new(
            mode, (result.width + left + right, result.height + top + bottom), background
        )
        expanded.paste(result, (left, top), result if "A" in result.getbands() else None)
        result = expanded
    return result


def _apply_annotations(
    image: Image.Image, annotations: tuple[AnnotationObject, ...]
) -> Image.Image:
    if not annotations:
        return image
    original_mode = image.mode
    result = image.convert("RGBA")
    overlay = Image.new("RGBA", result.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    overlay_dirty = False
    for annotation in annotations:
        if annotation.kind in {"mosaic", "blur"}:
            if overlay_dirty:
                result = Image.alpha_composite(result, overlay)
                overlay = Image.new("RGBA", result.size, (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                overlay_dirty = False
            result = _apply_region_effect(result, annotation)
            continue
        _draw_annotation(draw, result.size, annotation)
        overlay_dirty = True
    if overlay_dirty:
        result = Image.alpha_composite(result, overlay)
    if original_mode == "RGBA":
        return result
    if original_mode == "LA":
        return result.convert("LA")
    return result.convert(original_mode if original_mode != "P" else "RGB")


def _apply_region_effect(image: Image.Image, annotation: AnnotationObject) -> Image.Image:
    left, top, right, bottom = _pixel_box(annotation.bounds, image.size)
    if right <= left or bottom <= top:
        return image
    patch = image.crop((left, top, right, bottom))
    if annotation.kind == "mosaic":
        block = max(2, annotation.mosaic_size)
        tiny = patch.resize(
            (max(1, patch.width // block), max(1, patch.height // block)),
            Image.Resampling.BILINEAR,
        )
        affected = tiny.resize(patch.size, Image.Resampling.NEAREST)
    else:
        affected = patch.filter(ImageFilter.GaussianBlur(annotation.blur_radius))
    if annotation.opacity < 100:
        affected = Image.blend(patch, affected, annotation.opacity / 100.0)
    output = image.copy()
    output.paste(affected, (left, top))
    return output


def _draw_annotation(
    draw: ImageDraw.ImageDraw,
    image_size: tuple[int, int],
    annotation: AnnotationObject,
) -> None:
    width, height = image_size
    box = _pixel_box(annotation.bounds, image_size)
    stroke = max(1, round(annotation.stroke_width * min(image_size) / 1000))
    color = _rgba(annotation.color, annotation.opacity)
    fill = _rgba(annotation.fill_color, annotation.opacity) if annotation.fill_color else None
    if annotation.kind == "line":
        draw.line(_pixel_line(annotation, image_size), fill=color, width=stroke)
    elif annotation.kind == "arrow":
        line = _pixel_line(annotation, image_size)
        draw.line(line, fill=color, width=stroke)
        _draw_arrow_head(draw, line, color, stroke)
    elif annotation.kind == "rectangle":
        draw.rectangle(box, outline=color, fill=fill, width=stroke)
    elif annotation.kind == "ellipse":
        draw.ellipse(box, outline=color, fill=fill, width=stroke)
    elif annotation.kind == "freehand":
        points = [(round(x * width), round(y * height)) for x, y in annotation.points]
        if len(points) >= 2:
            draw.line(points, fill=color, width=stroke, joint="curve")
    elif annotation.kind == "redact":
        draw.rectangle(box, fill=fill or color)
    elif annotation.kind == "text":
        font = watermark_font(image_size, annotation.font_size)
        draw.text((box[0], box[1]), annotation.text, font=font, fill=color, stroke_width=0)
    elif annotation.kind == "number":
        font = watermark_font(image_size, annotation.font_size)
        diameter = max(20, min(box[2] - box[0], box[3] - box[1]))
        circle = (box[0], box[1], box[0] + diameter, box[1] + diameter)
        draw.ellipse(circle, fill=fill or color, outline=color, width=stroke)
        label = str(annotation.number)
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        draw.text(
            (
                circle[0] + (diameter - text_width) / 2,
                circle[1] + (diameter - text_height) / 2 - text_box[1],
            ),
            label,
            font=font,
            fill=(255, 255, 255, color[3]),
        )


def _pixel_box(
    box: tuple[float, float, float, float], image_size: tuple[int, int]
) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    width, height = image_size
    return (
        max(0, min(width, round(left * width))),
        max(0, min(height, round(top * height))),
        max(0, min(width, round(right * width))),
        max(0, min(height, round(bottom * height))),
    )


def _pixel_line(
    annotation: AnnotationObject, image_size: tuple[int, int]
) -> tuple[int, int, int, int]:
    width, height = image_size
    return (
        round(annotation.x1 * width),
        round(annotation.y1 * height),
        round(annotation.x2 * width),
        round(annotation.y2 * height),
    )


def _draw_arrow_head(
    draw: ImageDraw.ImageDraw,
    line: tuple[int, int, int, int],
    color: tuple[int, int, int, int],
    stroke: int,
) -> None:
    x1, y1, x2, y2 = line
    angle = math.atan2(y2 - y1, x2 - x1)
    length = max(10, stroke * 4)
    spread = math.pi / 6
    points = [
        (x2, y2),
        (x2 - length * math.cos(angle - spread), y2 - length * math.sin(angle - spread)),
        (x2 - length * math.cos(angle + spread), y2 - length * math.sin(angle + spread)),
    ]
    draw.polygon(points, fill=color)


def _rgba(color: str | None, opacity: int) -> tuple[int, int, int, int]:
    red, green, blue = _parse_hex_color(color or "#000000")
    return (red, green, blue, max(1, min(100, opacity)) * 255 // 100)


def _validate_perspective_quad(quad: tuple[float, ...]) -> None:
    points = [(quad[index], quad[index + 1]) for index in range(0, 8, 2)]
    crosses: list[float] = []
    for index in range(4):
        first = points[index]
        second = points[(index + 1) % 4]
        third = points[(index + 2) % 4]
        crosses.append(
            (second[0] - first[0]) * (third[1] - second[1])
            - (second[1] - first[1]) * (third[0] - second[0])
        )
    if min(abs(value) for value in crosses) < 1e-4 or not (
        all(value > 0 for value in crosses) or all(value < 0 for value in crosses)
    ):
        raise ValueError("透视四角必须按左上、右上、右下、左下组成凸四边形。")
    area = abs(
        sum(
            points[index][0] * points[(index + 1) % 4][1]
            - points[(index + 1) % 4][0] * points[index][1]
            for index in range(4)
        )
        / 2
    )
    if area < 0.005:
        raise ValueError("透视区域过小。")


def _apply_crop(
    image: Image.Image, crop_box: tuple[float, float, float, float] | None
) -> Image.Image:
    if crop_box is None:
        return image
    left = max(0, min(image.width - 1, round(crop_box[0] * image.width)))
    top = max(0, min(image.height - 1, round(crop_box[1] * image.height)))
    right = max(left + 1, min(image.width, round(crop_box[2] * image.width)))
    bottom = max(top + 1, min(image.height, round(crop_box[3] * image.height)))
    return image.crop((left, top, right, bottom))


def _apply_color(image: Image.Image, spec: ColorSpec) -> Image.Image:
    if spec == ColorSpec():
        return image
    had_alpha = "A" in image.getbands()
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8).copy() if had_alpha else None
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0

    if spec.denoise:
        rgb = _edge_preserving_denoise(rgb, spec.denoise / 100.0)

    temperature = spec.temperature / 100.0
    tint = spec.tint / 100.0
    gains = np.array(
        [
            2.0 ** (0.25 * temperature + 0.10 * tint),
            2.0 ** (-0.20 * tint),
            2.0 ** (-0.25 * temperature + 0.10 * tint),
        ],
        dtype=np.float32,
    )
    rgb *= gains

    rgb = np.clip(
        (rgb - spec.level_black / 255.0) / max((spec.level_white - spec.level_black) / 255.0, 1e-6),
        0.0,
        1.0,
    )
    rgb = np.power(rgb, 1.0 / spec.level_gamma)
    rgb = _apply_curves(rgb, spec.curves)

    luma = np.sum(rgb * np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), axis=2)
    shadows = spec.shadows / 100.0 * 0.5
    highlights = spec.highlights / 100.0 * 0.5
    rgb += shadows * np.square(1.0 - luma)[..., None]
    rgb += highlights * np.square(luma)[..., None]

    if spec.auto_contrast:
        low = np.percentile(rgb, 0.5, axis=(0, 1))
        high = np.percentile(rgb, 99.5, axis=(0, 1))
        rgb = (rgb - low) / np.maximum(high - low, 1e-6)
    rgb *= max(0.0, 1.0 + spec.brightness / 100.0)
    rgb = (rgb - 0.5) * max(0.0, 1.0 + spec.contrast / 100.0) + 0.5
    luma = np.sum(rgb * np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), axis=2)
    rgb = luma[..., None] + (rgb - luma[..., None]) * max(0.0, 1.0 + spec.saturation / 100.0)
    if spec.grayscale:
        rgb = np.repeat(luma[..., None], 3, axis=2)
    rgb = np.clip(rgb, 0.0, 1.0)

    if spec.sharpen:
        current = Image.fromarray(np.rint(rgb * 255.0).astype(np.uint8))
        radius = 0.8 + 1.2 * spec.sharpen / 100.0
        blurred = (
            np.asarray(current.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255.0
        )
        detail = rgb - blurred
        amount = 2.0 * spec.sharpen / 100.0
        rgb = np.where(np.abs(detail) < 2.0 / 255.0, rgb, rgb + detail * amount)
        rgb = np.clip(rgb, 0.0, 1.0)

    output = Image.fromarray(np.rint(rgb * 255.0).astype(np.uint8))
    if alpha is not None:
        output.putalpha(Image.fromarray(alpha))
    return output


def _apply_curves(rgb: np.ndarray, curves: dict[str, CurvePoints]) -> np.ndarray:
    output = rgb
    master_x, master_y = zip(*curves.get("rgb", IDENTITY_CURVE), strict=True)
    output = np.interp(output, master_x, master_y).astype(np.float32)
    for index, channel in enumerate(("red", "green", "blue")):
        points = curves.get(channel, IDENTITY_CURVE)
        x_values, y_values = zip(*points, strict=True)
        output[..., index] = np.interp(output[..., index], x_values, y_values)
    return output


def _edge_preserving_denoise(rgb: np.ndarray, strength: float) -> np.ndarray:
    padded = np.pad(rgb, ((1, 1), (1, 1), (0, 0)), mode="reflect")
    accumulator = rgb.copy()
    weights = np.ones(rgb.shape[:2], dtype=np.float32)
    sigma = 0.025 + 0.125 * strength
    for y_offset in range(3):
        for x_offset in range(3):
            if x_offset == 1 and y_offset == 1:
                continue
            neighbor = padded[
                y_offset : y_offset + rgb.shape[0],
                x_offset : x_offset + rgb.shape[1],
            ]
            distance = np.mean(np.square(neighbor - rgb), axis=2)
            spatial = 0.75 if x_offset == 1 or y_offset == 1 else 0.5
            weight = np.exp(-distance / (2.0 * sigma * sigma)) * spatial
            accumulator += neighbor * weight[..., None]
            weights += weight
    filtered = accumulator / weights[..., None]
    return rgb * (1.0 - strength) + filtered * strength


def _apply_size(image: Image.Image, spec: SizeSpec) -> Image.Image:
    result = image
    if spec.exact_width and spec.exact_height:
        return result.resize((spec.exact_width, spec.exact_height), Image.Resampling.LANCZOS)
    if spec.max_width or spec.max_height:
        result = result.copy()
        result.thumbnail(
            (spec.max_width or result.width, spec.max_height or result.height),
            Image.Resampling.LANCZOS,
        )
    return result


def _curve_points(raw: Any) -> CurvePoints:
    if not isinstance(raw, list) or not 2 <= len(raw) <= 32:
        raise ValueError("曲线必须包含 2 到 32 个控制点。")
    points = tuple((float(item[0]), float(item[1])) for item in raw)
    if points[0] != (0.0, 0.0) or points[-1] != (1.0, 1.0):
        raise ValueError("曲线端点必须固定在 (0,0) 和 (1,1)。")
    if any(not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0) for x, y in points):
        raise ValueError("曲线控制点必须位于 0 到 1。")
    if any(points[index][0] >= points[index + 1][0] for index in range(len(points) - 1)):
        raise ValueError("曲线控制点 X 坐标必须严格递增。")
    return points


def _largest_rotated_rect(width: int, height: int, angle: float) -> tuple[float, float]:
    if width <= 0 or height <= 0:
        return (0.0, 0.0)
    angle = abs(angle) % math.pi
    if angle > math.pi / 2:
        angle = math.pi - angle
    if angle < 1e-9:
        return (float(width), float(height))
    sin_a = abs(math.sin(angle))
    cos_a = abs(math.cos(angle))
    width_is_longer = width >= height
    side_long = width if width_is_longer else height
    side_short = height if width_is_longer else width
    if side_short <= 2.0 * sin_a * cos_a * side_long or abs(sin_a - cos_a) < 1e-9:
        half_short = 0.5 * side_short
        crop_width = half_short / sin_a if width_is_longer else half_short / cos_a
        crop_height = half_short / cos_a if width_is_longer else half_short / sin_a
    else:
        cos_2a = cos_a * cos_a - sin_a * sin_a
        crop_width = (width * cos_a - height * sin_a) / cos_2a
        crop_height = (height * cos_a - width * sin_a) / cos_2a
    return (abs(crop_width), abs(crop_height))


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    if len(value) != 7 or not value.startswith("#"):
        raise ValueError("背景色必须使用 #RRGGBB 格式。")
    try:
        return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]
    except ValueError as error:
        raise ValueError("背景色必须使用 #RRGGBB 格式。") from error


def _bounded_int(value: Any, minimum: int, maximum: int, label: str) -> int:
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}必须位于 {minimum} 到 {maximum}。")
    return number


def _bounded_float(value: Any, minimum: float, maximum: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{label}必须位于 {minimum} 到 {maximum}。")
    return number


def _optional_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    number = int(value)
    if number <= 0 or number > 100_000:
        raise ValueError("尺寸必须位于 1 到 100000。")
    return number
