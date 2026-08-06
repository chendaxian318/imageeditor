"""Shared normalized quadrilateral validation for perspective text workflows."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import cast

type NormalizedQuad = tuple[float, float, float, float, float, float, float, float]


def validate_normalized_quad(
    value: Sequence[float],
    *,
    region: tuple[float, float, float, float] | None = None,
) -> NormalizedQuad:
    if len(value) != 8:
        raise ValueError("透视文字四边形必须包含八个归一化坐标")
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(float(item))
        for item in value
    ):
        raise ValueError("透视文字四边形坐标必须是有限数字")
    normalized = tuple(float(item) for item in value)
    if any(not 0 <= item <= 1 for item in normalized):
        raise ValueError("透视文字四边形必须位于图片范围内")
    points = [(normalized[index], normalized[index + 1]) for index in range(0, 8, 2)]
    crosses: list[float] = []
    for index in range(4):
        first = points[index]
        second = points[(index + 1) % 4]
        third = points[(index + 2) % 4]
        crosses.append(
            (second[0] - first[0]) * (third[1] - second[1])
            - (second[1] - first[1]) * (third[0] - second[0])
        )
    if min(abs(cross) for cross in crosses) < 1e-6 or not (
        all(cross > 0 for cross in crosses) or all(cross < 0 for cross in crosses)
    ):
        raise ValueError("透视文字四角必须按左上、右上、右下、左下组成凸四边形")
    area = abs(
        sum(
            points[index][0] * points[(index + 1) % 4][1]
            - points[(index + 1) % 4][0] * points[index][1]
            for index in range(4)
        )
        / 2
    )
    if area < 0.0001:
        raise ValueError("透视文字四边形过小")
    if region is not None:
        left, top, right, bottom = region
        if any(
            not left - 1e-6 <= x <= right + 1e-6 or not top - 1e-6 <= y <= bottom + 1e-6
            for x, y in points
        ):
            raise ValueError("透视文字四边形必须位于文字区域内")
    return cast(NormalizedQuad, normalized)


def quad_bounds(quad: NormalizedQuad) -> tuple[float, float, float, float]:
    x_values = quad[0::2]
    y_values = quad[1::2]
    return (min(x_values), min(y_values), max(x_values), max(y_values))


__all__ = ["NormalizedQuad", "quad_bounds", "validate_normalized_quad"]
