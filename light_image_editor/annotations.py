"""Serializable non-destructive annotation objects and geometry helpers."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from typing import Any

ANNOTATION_KINDS = frozenset(
    {
        "text",
        "arrow",
        "line",
        "rectangle",
        "ellipse",
        "freehand",
        "mosaic",
        "blur",
        "redact",
        "number",
    }
)


def hit_radius_px(dpi: float, logical_pixels: int = 8) -> int:
    """Return a physical hit radius that remains usable at high DPI."""

    if not math.isfinite(dpi) or dpi <= 0:
        dpi = 96.0
    return max(logical_pixels, round(logical_pixels * dpi / 96.0))


def parse_crop_ratio(value: str) -> float | None:
    text = value.strip()
    if not text or text == "自由":
        return None
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError("裁剪比例应使用“宽:高”，例如 3:2。")
    try:
        width, height = (float(part.strip()) for part in parts)
    except ValueError as error:
        raise ValueError("裁剪比例只能包含数字，例如 3:2。") from error
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValueError("裁剪比例的宽和高必须大于 0。")
    ratio = width / height
    if not 0.02 <= ratio <= 50:
        raise ValueError("裁剪比例超出 1:50 到 50:1 的安全范围。")
    return ratio


@dataclass(frozen=True)
class AnnotationObject:
    id: str
    kind: str
    x1: float
    y1: float
    x2: float
    y2: float
    points: tuple[tuple[float, float], ...] = ()
    text: str = ""
    color: str = "#ff3b30"
    fill_color: str | None = None
    stroke_width: int = 4
    opacity: int = 100
    font_size: int = 5
    number: int = 1
    mosaic_size: int = 12
    blur_radius: int = 8

    @classmethod
    def create(
        cls,
        kind: str,
        box: tuple[float, float, float, float],
        **changes: Any,
    ) -> AnnotationObject:
        return cls.from_json(
            {
                "id": str(uuid.uuid4()),
                "kind": kind,
                "box": list(box),
                **changes,
            }
        )

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AnnotationObject:
        kind = str(data.get("kind", ""))
        if kind not in ANNOTATION_KINDS:
            raise ValueError(f"未知标注类型：{kind}")
        object_id = str(data.get("id", ""))
        uuid.UUID(object_id)
        box = data.get("box", [0.0, 0.0, 0.0, 0.0])
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError("标注边界必须包含四个坐标。")
        x1, y1, x2, y2 = (_unit_float(value, "标注坐标") for value in box)
        raw_points = data.get("points", [])
        if not isinstance(raw_points, list) or len(raw_points) > 20_000:
            raise ValueError("自由画笔点列表无效或过长。")
        points = tuple(
            (_unit_float(point[0], "画笔坐标"), _unit_float(point[1], "画笔坐标"))
            for point in raw_points
            if isinstance(point, list) and len(point) == 2
        )
        if len(points) != len(raw_points):
            raise ValueError("自由画笔点格式无效。")
        color = _hex_color(data.get("color", "#ff3b30"), "描边颜色")
        raw_fill = data.get("fill_color")
        fill_color = None if raw_fill in {None, ""} else _hex_color(raw_fill, "填充颜色")
        return cls(
            id=object_id,
            kind=kind,
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            points=points,
            text=str(data.get("text", ""))[:10_000],
            color=color,
            fill_color=fill_color,
            stroke_width=_bounded_int(data.get("stroke_width", 4), 1, 100, "描边宽度"),
            opacity=_bounded_int(data.get("opacity", 100), 1, 100, "透明度"),
            font_size=_bounded_int(data.get("font_size", 5), 1, 30, "字号"),
            number=_bounded_int(data.get("number", 1), 1, 9999, "序号"),
            mosaic_size=_bounded_int(data.get("mosaic_size", 12), 2, 100, "马赛克块大小"),
            blur_radius=_bounded_int(data.get("blur_radius", 8), 1, 100, "模糊半径"),
        )

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        if self.points:
            xs = [point[0] for point in self.points]
            ys = [point[1] for point in self.points]
            return (min(xs), min(ys), max(xs), max(ys))
        return (
            min(self.x1, self.x2),
            min(self.y1, self.y2),
            max(self.x1, self.x2),
            max(self.y1, self.y2),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "box": [self.x1, self.y1, self.x2, self.y2],
            "points": [[x, y] for x, y in self.points],
            "text": self.text,
            "color": self.color,
            "fill_color": self.fill_color,
            "stroke_width": self.stroke_width,
            "opacity": self.opacity,
            "font_size": self.font_size,
            "number": self.number,
            "mosaic_size": self.mosaic_size,
            "blur_radius": self.blur_radius,
        }

    def with_patch(self, patch: dict[str, Any]) -> AnnotationObject:
        payload = self.to_json()
        if "box" in patch:
            payload["box"] = patch["box"]
        for key in payload.keys() - {"id", "kind", "box"}:
            if key in patch:
                payload[key] = patch[key]
        return AnnotationObject.from_json(payload)

    def translated(self, dx: float, dy: float) -> AnnotationObject:
        left, top, right, bottom = self.bounds
        dx = max(-left, min(1.0 - right, dx))
        dy = max(-top, min(1.0 - bottom, dy))
        points = tuple((x + dx, y + dy) for x, y in self.points)
        return replace(
            self,
            x1=self.x1 + dx,
            y1=self.y1 + dy,
            x2=self.x2 + dx,
            y2=self.y2 + dy,
            points=points,
        )

    def resized(
        self,
        box: tuple[float, float, float, float],
    ) -> AnnotationObject:
        left, top, right, bottom = (_unit_float(value, "标注坐标") for value in box)
        old_left, old_top, old_right, old_bottom = self.bounds
        old_width = max(1e-9, old_right - old_left)
        old_height = max(1e-9, old_bottom - old_top)
        new_left, new_right = sorted((left, right))
        new_top, new_bottom = sorted((top, bottom))
        points = tuple(
            (
                new_left + (x - old_left) / old_width * (new_right - new_left),
                new_top + (y - old_top) / old_height * (new_bottom - new_top),
            )
            for x, y in self.points
        )
        return replace(
            self,
            x1=left,
            y1=top,
            x2=right,
            y2=bottom,
            points=points,
        )


def object_at(
    objects: tuple[AnnotationObject, ...],
    point: tuple[float, float],
    tolerance: float,
) -> AnnotationObject | None:
    x, y = point
    for annotation in reversed(objects):
        left, top, right, bottom = annotation.bounds
        if (
            left - tolerance <= x <= right + tolerance
            and top - tolerance <= y <= bottom + tolerance
        ):
            if annotation.kind in {"line", "arrow", "freehand"}:
                if _distance_to_annotation(annotation, point) > tolerance:
                    continue
            return annotation
    return None


def _distance_to_annotation(annotation: AnnotationObject, point: tuple[float, float]) -> float:
    points = annotation.points or ((annotation.x1, annotation.y1), (annotation.x2, annotation.y2))
    return min(
        _distance_to_segment(point, start, end)
        for start, end in zip(points, points[1:], strict=False)
    )


def _distance_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    amount = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + amount * dx), py - (y1 + amount * dy))


def _unit_float(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{label}必须位于 0 到 1。")
    return number


def _bounded_int(value: Any, minimum: int, maximum: int, label: str) -> int:
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}必须位于 {minimum} 到 {maximum}。")
    return number


def _hex_color(value: Any, label: str) -> str:
    text = str(value).lower()
    if len(text) != 7 or not text.startswith("#"):
        raise ValueError(f"{label}必须使用 #RRGGBB 格式。")
    try:
        int(text[1:], 16)
    except ValueError as error:
        raise ValueError(f"{label}必须使用 #RRGGBB 格式。") from error
    return text
