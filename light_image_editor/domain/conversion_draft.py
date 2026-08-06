"""Immutable, timeline-independent editing state for conversion recipes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from .recipes import ExportProfile, TransformRecipe, TransformStep

_CANONICAL_STEP_ORDER = {
    "rotate": 0,
    "flip:horizontal": 1,
    "flip:vertical": 2,
    "crop": 3,
    "watermark": 4,
    "resize": 5,
}


def _step_key(step: TransformStep) -> str:
    if step.kind == "flip":
        return f"flip:{step.params['axis']}"
    return step.kind


def _frozen_recipe(recipe: TransformRecipe) -> TransformRecipe:
    return TransformRecipe.from_json(recipe.to_json())


def _positive_text(value: str, label: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    try:
        number = int(text)
    except ValueError as error:
        raise ValueError(f"{label}必须是正整数") from error
    if number <= 0:
        raise ValueError(f"{label}必须大于 0")
    return number


@dataclass(frozen=True, slots=True)
class ConversionDraft:
    """A conversion recipe under edit, isolated from ``Document.timeline``.

    Imported recipes retain their declared step order exactly. Replacing an
    existing operation keeps its position. A newly enabled operation is inserted
    according to the application's canonical conversion pipeline.
    """

    recipe: TransformRecipe = TransformRecipe(name="当前转换设置")

    def __post_init__(self) -> None:
        if not isinstance(self.recipe, TransformRecipe):
            raise TypeError("recipe 必须是 TransformRecipe")
        object.__setattr__(self, "recipe", _frozen_recipe(self.recipe))

    @classmethod
    def from_recipe(cls, recipe: TransformRecipe) -> ConversionDraft:
        return cls(recipe)

    @property
    def name(self) -> str:
        return self.recipe.name

    @property
    def steps(self) -> tuple[TransformStep, ...]:
        return self.recipe.steps

    @property
    def export_profile(self) -> ExportProfile:
        return self.recipe.export_profile

    @property
    def rotation(self) -> int:
        step = self._find("rotate")
        return 0 if step is None else int(step.params["degrees"])

    @property
    def flip_horizontal(self) -> bool:
        return self._find("flip:horizontal") is not None

    @property
    def flip_vertical(self) -> bool:
        return self._find("flip:vertical") is not None

    @property
    def crop_box(self) -> tuple[float, float, float, float] | None:
        step = self._find("crop")
        if step is None:
            return None
        values = step.params["box"]
        return (
            float(values[0]),
            float(values[1]),
            float(values[2]),
            float(values[3]),
        )

    @property
    def resize_params(self) -> dict[str, Any]:
        step = self._find("resize")
        return {} if step is None else dict(step.params)

    @property
    def watermark_params(self) -> dict[str, Any]:
        step = self._find("watermark")
        return {} if step is None else dict(step.params)

    def to_recipe(self) -> TransformRecipe:
        return _frozen_recipe(self.recipe)

    def with_name(self, name: str) -> ConversionDraft:
        return self._with_recipe(replace(self.recipe, name=name))

    def with_export_profile(self, profile: ExportProfile) -> ConversionDraft:
        if not isinstance(profile, ExportProfile):
            raise TypeError("profile 必须是 ExportProfile")
        return self._with_recipe(replace(self.recipe, export_profile=profile))

    def update_export_profile(self, **changes: Any) -> ConversionDraft:
        return self.with_export_profile(replace(self.export_profile, **changes))

    def with_rotation(self, degrees: int) -> ConversionDraft:
        if isinstance(degrees, bool) or not isinstance(degrees, int):
            raise ValueError("转图旋转角度必须是整数")
        normalized = degrees % 360
        step = None if normalized == 0 else TransformStep.rotate(normalized)
        return self._replace_step("rotate", step)

    def with_flip(
        self,
        axis: Literal["horizontal", "vertical"],
        enabled: bool,
    ) -> ConversionDraft:
        if not isinstance(enabled, bool):
            raise ValueError("翻转 enabled 必须是布尔值")
        key = f"flip:{axis}"
        step = TransformStep.flip(axis) if enabled else None
        return self._replace_step(key, step)

    def with_crop(
        self,
        box: tuple[float, float, float, float] | None,
    ) -> ConversionDraft:
        step = None if box is None else TransformStep.crop(box)
        return self._replace_step("crop", step)

    def with_resize(
        self,
        *,
        exact_width: int | None = None,
        exact_height: int | None = None,
        max_width: int | None = None,
        max_height: int | None = None,
        allow_upscale: bool | None = None,
    ) -> ConversionDraft:
        values = (exact_width, exact_height, max_width, max_height)
        if all(value is None for value in values):
            return self._replace_step("resize", None)
        step = TransformStep.resize(
            exact_width=exact_width,
            exact_height=exact_height,
            max_width=max_width,
            max_height=max_height,
            allow_upscale=allow_upscale,
        )
        return self._replace_step("resize", step)

    def with_watermark(
        self,
        text: str,
        *,
        position: str = "右下",
        opacity: int = 35,
        size_percent: int = 5,
    ) -> ConversionDraft:
        normalized = text.strip()
        step = (
            None
            if not normalized
            else TransformStep.watermark(
                normalized,
                position=position,
                opacity=opacity,
                size_percent=size_percent,
            )
        )
        return self._replace_step("watermark", step)

    def _find(self, key: str) -> TransformStep | None:
        return next((step for step in self.steps if _step_key(step) == key), None)

    def _replace_step(
        self,
        key: str,
        replacement: TransformStep | None,
    ) -> ConversionDraft:
        steps = list(self.steps)
        existing_index = next(
            (index for index, step in enumerate(steps) if _step_key(step) == key),
            None,
        )
        if existing_index is not None:
            if replacement is None:
                steps.pop(existing_index)
            else:
                steps[existing_index] = replacement
        elif replacement is not None:
            rank = _CANONICAL_STEP_ORDER[key]
            insertion = len(steps)
            for index, candidate in enumerate(steps):
                candidate_rank = _CANONICAL_STEP_ORDER[_step_key(candidate)]
                if candidate_rank > rank:
                    insertion = index
                    break
            steps.insert(insertion, replacement)
        return self._with_recipe(replace(self.recipe, steps=tuple(steps)))

    def _with_recipe(self, recipe: TransformRecipe) -> ConversionDraft:
        return ConversionDraft(recipe)


@dataclass(frozen=True, slots=True)
class ConversionControlState:
    """Validated text/control projection for editing a conversion draft in a UI."""

    width_text: str = ""
    height_text: str = ""
    max_side_text: str = ""
    allow_upscale: bool = False
    watermark_text: str = ""
    watermark_position: str = "右下"
    watermark_opacity: int = 35
    watermark_size_percent: int = 5
    output_format: str = "JPEG"
    quality: int = 85
    target_size_text: str = ""
    preserve_metadata: bool = True
    rename_template: str = "{stem}_{index:04d}"
    conflict_policy: str = "unique"
    preserve_animation: bool = True
    backup_on_overwrite: bool = True
    transparency_background: str | None = None
    transparency_background_explicit: bool = False

    @classmethod
    def from_draft(cls, draft: ConversionDraft) -> ConversionControlState:
        if not isinstance(draft, ConversionDraft):
            raise TypeError("draft 必须是 ConversionDraft")
        resize = draft.resize_params
        exact_width = resize.get("exact_width")
        exact_height = resize.get("exact_height")
        max_width = resize.get("max_width")
        max_height = resize.get("max_height")
        if max_width is not None and max_width == max_height:
            width_text = ""
            height_text = ""
            max_side_text = str(max_width)
        else:
            width_text = str(exact_width if exact_width is not None else max_width or "")
            height_text = str(exact_height if exact_height is not None else max_height or "")
            max_side_text = ""
        watermark = draft.watermark_params
        profile = draft.export_profile
        return cls(
            width_text=width_text,
            height_text=height_text,
            max_side_text=max_side_text,
            allow_upscale=bool(resize.get("allow_upscale", exact_width is not None)),
            watermark_text=str(watermark.get("text", "")),
            watermark_position=str(watermark.get("position", "右下")),
            watermark_opacity=int(watermark.get("opacity", 35)),
            watermark_size_percent=int(watermark.get("size_percent", 5)),
            output_format=profile.output_format,
            quality=profile.quality,
            target_size_text=(
                "" if profile.target_size_kb is None else str(profile.target_size_kb)
            ),
            preserve_metadata=profile.preserve_metadata,
            rename_template=profile.rename_template,
            conflict_policy=profile.conflict_policy,
            preserve_animation=profile.preserve_animation,
            transparency_background=profile.transparency_background,
            backup_on_overwrite=profile.backup_on_overwrite,
            transparency_background_explicit=True,
        )

    def apply_to(self, draft: ConversionDraft) -> ConversionDraft:
        if not isinstance(draft, ConversionDraft):
            raise TypeError("draft 必须是 ConversionDraft")
        if not isinstance(self.transparency_background_explicit, bool):
            raise ValueError("透明背景显式标志必须是布尔值")
        width = _positive_text(self.width_text, "宽度")
        height = _positive_text(self.height_text, "高度")
        max_side = _positive_text(self.max_side_text, "最长边")
        target_size_kb = _positive_text(self.target_size_text, "目标 KB")
        if max_side is not None and (width is not None or height is not None):
            raise ValueError("最长边不能和宽度或高度同时填写")
        if max_side is not None:
            updated = draft.with_resize(
                max_width=max_side,
                max_height=max_side,
                allow_upscale=self.allow_upscale,
            )
        elif width is not None and height is not None:
            updated = draft.with_resize(
                exact_width=width,
                exact_height=height,
                allow_upscale=True,
            )
        elif width is not None or height is not None:
            updated = draft.with_resize(
                max_width=width,
                max_height=height,
                allow_upscale=self.allow_upscale,
            )
        else:
            updated = draft.with_resize()
        updated = updated.with_watermark(
            self.watermark_text,
            position=self.watermark_position,
            opacity=self.watermark_opacity,
            size_percent=self.watermark_size_percent,
        )
        return updated.with_export_profile(
            ExportProfile(
                output_format=self.output_format,
                quality=self.quality,
                target_size_kb=target_size_kb,
                preserve_metadata=self.preserve_metadata,
                rename_template=self.rename_template,
                conflict_policy=self.conflict_policy,
                preserve_animation=self.preserve_animation,
                transparency_background=(
                    self.transparency_background
                    if self.transparency_background_explicit
                    else updated.export_profile.transparency_background
                ),
                allow_in_place=updated.export_profile.allow_in_place,
                backup_on_overwrite=self.backup_on_overwrite,
            )
        )


class ConversionDraftHistory:
    """Small bounded undo/redo history owned exclusively by conversion mode."""

    def __init__(self, initial: ConversionDraft | None = None, *, limit: int = 200) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 2:
            raise ValueError("conversion draft history limit 必须至少为 2")
        self.limit = limit
        self._entries = [initial or ConversionDraft()]
        self._cursor = 0

    @property
    def current(self) -> ConversionDraft:
        return self._entries[self._cursor]

    @property
    def can_undo(self) -> bool:
        return self._cursor > 0

    @property
    def can_redo(self) -> bool:
        return self._cursor + 1 < len(self._entries)

    @property
    def length(self) -> int:
        return len(self._entries)

    def push(self, draft: ConversionDraft) -> bool:
        if not isinstance(draft, ConversionDraft):
            raise TypeError("draft 必须是 ConversionDraft")
        if draft == self.current:
            return False
        del self._entries[self._cursor + 1 :]
        self._entries.append(draft)
        if len(self._entries) > self.limit:
            excess = len(self._entries) - self.limit
            del self._entries[:excess]
        self._cursor = len(self._entries) - 1
        return True

    def undo(self) -> ConversionDraft | None:
        if not self.can_undo:
            return None
        self._cursor -= 1
        return self.current

    def redo(self) -> ConversionDraft | None:
        if not self.can_redo:
            return None
        self._cursor += 1
        return self.current

    def reset(self, draft: ConversionDraft | None = None) -> ConversionDraft:
        self._entries = [draft or ConversionDraft()]
        self._cursor = 0
        return self.current


__all__ = ["ConversionControlState", "ConversionDraft", "ConversionDraftHistory"]
