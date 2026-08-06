"""Validated editable parameters for deterministic picture-text replacement."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Literal

from .assets import Scalar
from .perspective_quad import NormalizedQuad, validate_normalized_quad

SMART_TEXT_FONT_FAMILIES = (
    "微软雅黑",
    "微软雅黑 Light",
    "等线",
    "宋体",
    "黑体",
)
SMART_TEXT_ALIGNMENTS = ("left", "center", "right")
TextVisualEffect = Literal["plain", "outline", "shadow", "gradient", "glow"]
SMART_TEXT_VISUAL_EFFECTS: tuple[TextVisualEffect, ...] = (
    "plain",
    "outline",
    "shadow",
    "gradient",
    "glow",
)
_HEX_COLOR = re.compile(r"#[0-9a-f]{6}")


@dataclass(frozen=True, slots=True)
class TextReplacementDraft:
    """One editable text replacement object before it is materialized."""

    region: tuple[float, float, float, float]
    text: str
    font_family: str = "微软雅黑"
    font_size: int = 32
    color: str = "#202124"
    align: str = "center"
    rotation: float = 0.0
    curve: float = 0.0
    perspective_quad: NormalizedQuad | None = None
    visual_effect: TextVisualEffect = "plain"
    effect_color: str = "#ffffff"
    effect_size: int = 4

    def __post_init__(self) -> None:
        if len(self.region) != 4:
            raise ValueError("文字区域必须包含四个归一化坐标")
        left, top, right, bottom = self.region
        if not all(math.isfinite(value) for value in self.region):
            raise ValueError("文字区域坐标必须是有限数字")
        if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
            raise ValueError("文字区域必须位于图片范围内且不能为空")
        if right - left < 0.002 or bottom - top < 0.002:
            raise ValueError("文字区域过小")
        normalized_text = self.text.strip()
        if not normalized_text or len(normalized_text) > 500:
            raise ValueError("替换文字必须包含 1 到 500 个字符")
        object.__setattr__(self, "text", normalized_text)
        if self.font_family not in SMART_TEXT_FONT_FAMILIES:
            raise ValueError("不支持该中文字体")
        if (
            isinstance(self.font_size, bool)
            or not isinstance(self.font_size, int)
            or not 8 <= self.font_size <= 512
        ):
            raise ValueError("文字字号必须是 8 到 512 的整数")
        normalized_color = self.color.strip().lower()
        if _HEX_COLOR.fullmatch(normalized_color) is None:
            raise ValueError("文字颜色必须使用 #RRGGBB 格式")
        object.__setattr__(self, "color", normalized_color)
        if self.align not in SMART_TEXT_ALIGNMENTS:
            raise ValueError("文字对齐方式无效")
        if (
            isinstance(self.rotation, bool)
            or not isinstance(self.rotation, (int, float))
            or not math.isfinite(float(self.rotation))
            or not -180 <= float(self.rotation) <= 180
        ):
            raise ValueError("文字旋转角度必须位于 -180 到 180 度")
        object.__setattr__(self, "rotation", float(self.rotation))
        if (
            isinstance(self.curve, bool)
            or not isinstance(self.curve, (int, float))
            or not math.isfinite(float(self.curve))
            or not -1 <= float(self.curve) <= 1
        ):
            raise ValueError("文字弧度必须位于 -100% 到 100%")
        object.__setattr__(self, "curve", float(self.curve))
        if abs(self.curve) > 1e-9 and "\n" in self.text:
            raise ValueError("弧形文字暂不支持多行，请使用单行文字")
        if self.perspective_quad is not None:
            object.__setattr__(
                self,
                "perspective_quad",
                validate_normalized_quad(self.perspective_quad, region=self.region),
            )
        if self.visual_effect not in SMART_TEXT_VISUAL_EFFECTS:
            raise ValueError("文字字效必须是普通、描边、投影、渐变或发光")
        normalized_effect_color = self.effect_color.strip().lower()
        if _HEX_COLOR.fullmatch(normalized_effect_color) is None:
            raise ValueError("文字字效辅助色必须使用 #RRGGBB 格式")
        object.__setattr__(self, "effect_color", normalized_effect_color)
        if (
            isinstance(self.effect_size, bool)
            or not isinstance(self.effect_size, int)
            or not 0 <= self.effect_size <= 64
        ):
            raise ValueError("文字字效强度必须是 0 到 64 的整数")
        if self.visual_effect in {"outline", "shadow", "glow"} and self.effect_size < 1:
            raise ValueError("描边、投影或发光强度必须至少为 1 像素")

    def with_text(self, text: str) -> TextReplacementDraft:
        return replace(self, text=text)

    def to_parameters(self) -> tuple[tuple[str, Scalar], ...]:
        left, top, right, bottom = self.region
        parameters: list[tuple[str, Scalar]] = [
            ("operation", "replace_text"),
            ("region_left", left),
            ("region_top", top),
            ("region_right", right),
            ("region_bottom", bottom),
            ("text", self.text),
            ("font_family", self.font_family),
            ("font_size", self.font_size),
            ("color", self.color),
            ("align", self.align),
            ("rotation", self.rotation),
            ("curve", self.curve),
            ("visual_effect", self.visual_effect),
            ("effect_color", self.effect_color),
            ("effect_size", self.effect_size),
        ]
        if self.perspective_quad is not None:
            names = (
                "perspective_tl_x",
                "perspective_tl_y",
                "perspective_tr_x",
                "perspective_tr_y",
                "perspective_br_x",
                "perspective_br_y",
                "perspective_bl_x",
                "perspective_bl_y",
            )
            parameters.extend(zip(names, self.perspective_quad, strict=True))
        return tuple(parameters)


class TextReplacementHistory:
    """Bounded undo/redo history for user-corrected text replacement objects."""

    def __init__(self, *, limit: int = 100) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 2:
            raise ValueError("文字替换历史上限必须至少为 2")
        self.limit = limit
        self._entries: list[TextReplacementDraft] = []
        self._cursor = -1

    @property
    def current(self) -> TextReplacementDraft | None:
        return None if self._cursor < 0 else self._entries[self._cursor]

    @property
    def can_undo(self) -> bool:
        return self._cursor > 0

    @property
    def can_redo(self) -> bool:
        return 0 <= self._cursor + 1 < len(self._entries)

    @property
    def length(self) -> int:
        return len(self._entries)

    def push(self, draft: TextReplacementDraft) -> bool:
        if not isinstance(draft, TextReplacementDraft):
            raise TypeError("draft 必须是 TextReplacementDraft")
        if draft == self.current:
            return False
        del self._entries[self._cursor + 1 :]
        self._entries.append(draft)
        if len(self._entries) > self.limit:
            excess = len(self._entries) - self.limit
            del self._entries[:excess]
        self._cursor = len(self._entries) - 1
        return True

    def undo(self) -> TextReplacementDraft | None:
        if not self.can_undo:
            return None
        self._cursor -= 1
        return self.current

    def redo(self) -> TextReplacementDraft | None:
        if not self.can_redo:
            return None
        self._cursor += 1
        return self.current

    def reset(self) -> None:
        self._entries.clear()
        self._cursor = -1


__all__ = [
    "SMART_TEXT_ALIGNMENTS",
    "SMART_TEXT_FONT_FAMILIES",
    "SMART_TEXT_VISUAL_EFFECTS",
    "TextReplacementDraft",
    "TextReplacementHistory",
    "TextVisualEffect",
]
