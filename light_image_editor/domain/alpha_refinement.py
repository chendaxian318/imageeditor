"""Validated, serializable preserve/remove brush strokes for alpha refinement."""

from __future__ import annotations

import json
from dataclasses import dataclass

MAX_ALPHA_STROKES = 100
MAX_ALPHA_POINTS = 10_000
MAX_ALPHA_STROKES_JSON_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class AlphaBrushStroke:
    mode: str
    radius: float
    points: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if self.mode not in {"preserve", "remove"}:
            raise ValueError("Alpha 画笔模式必须是 preserve 或 remove")
        if (
            isinstance(self.radius, bool)
            or not isinstance(self.radius, (int, float))
            or not 0.001 <= float(self.radius) <= 0.25
        ):
            raise ValueError("Alpha 画笔半径必须在 0.001 到 0.25 之间")
        normalized_points = tuple(self.points)
        if not normalized_points or len(normalized_points) > MAX_ALPHA_POINTS:
            raise ValueError("Alpha 画笔必须包含 1 到 10,000 个点")
        for point in normalized_points:
            if len(point) != 2:
                raise ValueError("Alpha 画笔点必须包含横纵坐标")
            x, y = point
            if (
                isinstance(x, bool)
                or isinstance(y, bool)
                or not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or not 0 <= float(x) <= 1
                or not 0 <= float(y) <= 1
            ):
                raise ValueError("Alpha 画笔点必须位于归一化画布范围内")
        object.__setattr__(self, "radius", float(self.radius))
        object.__setattr__(
            self,
            "points",
            tuple((float(x), float(y)) for x, y in normalized_points),
        )


def serialize_alpha_strokes(strokes: tuple[AlphaBrushStroke, ...]) -> str:
    normalized = tuple(strokes)
    if not normalized or len(normalized) > MAX_ALPHA_STROKES:
        raise ValueError("Alpha 细化必须包含 1 到 100 条笔划")
    if sum(len(stroke.points) for stroke in normalized) > MAX_ALPHA_POINTS:
        raise ValueError("Alpha 细化点总数不能超过 10,000")
    payload = [
        {
            "mode": stroke.mode,
            "radius": stroke.radius,
            "points": stroke.points,
        }
        for stroke in normalized
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_ALPHA_STROKES_JSON_BYTES:
        raise ValueError("Alpha 细化数据超过安全上限")
    return encoded


def parse_alpha_strokes(payload: str) -> tuple[AlphaBrushStroke, ...]:
    if not isinstance(payload, str) or not payload:
        raise ValueError("Alpha 细化数据不能为空")
    if len(payload.encode("utf-8")) > MAX_ALPHA_STROKES_JSON_BYTES:
        raise ValueError("Alpha 细化数据超过安全上限")
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError("Alpha 细化数据不是有效 JSON") from error
    if not isinstance(decoded, list) or not decoded or len(decoded) > MAX_ALPHA_STROKES:
        raise ValueError("Alpha 细化必须包含 1 到 100 条笔划")
    strokes: list[AlphaBrushStroke] = []
    for item in decoded:
        if not isinstance(item, dict) or set(item) != {"mode", "radius", "points"}:
            raise ValueError("Alpha 笔划字段无效")
        raw_points = item["points"]
        if not isinstance(raw_points, list):
            raise ValueError("Alpha 笔划点必须是数组")
        points: list[tuple[float, float]] = []
        for point in raw_points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("Alpha 画笔点必须包含横纵坐标")
            points.append((point[0], point[1]))
        strokes.append(
            AlphaBrushStroke(
                mode=item["mode"],
                radius=item["radius"],
                points=tuple(points),
            )
        )
    if sum(len(stroke.points) for stroke in strokes) > MAX_ALPHA_POINTS:
        raise ValueError("Alpha 细化点总数不能超过 10,000")
    return tuple(strokes)


__all__ = ["AlphaBrushStroke", "parse_alpha_strokes", "serialize_alpha_strokes"]
