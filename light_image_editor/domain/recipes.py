"""Versioned domain model for deterministic image-conversion recipes."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    from ..batch import BatchRecipe


TRANSFORM_RECIPE_SCHEMA_VERSION = 1
RECIPE_SCHEMA_VERSION = TRANSFORM_RECIPE_SCHEMA_VERSION
SUPPORTED_OUTPUT_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "AVIF", "BMP", "TIFF", "GIF"})
SUPPORTED_CONFLICT_POLICIES = frozenset({"error", "overwrite", "unique"})

_TEMPLATE_FIELD = re.compile(r"\{(stem|name|ext|index|date|datetime)(?::([^{}]+))?\}")
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WATERMARK_POSITIONS = frozenset({"左上", "右上", "左下", "右下", "居中"})
_HEX_BACKGROUND = re.compile(r"#[0-9a-fA-F]{6}")
_MAX_DIMENSION = 100_000


def _reject_unknown_keys(payload: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(payload) - allowed
    if unknown:
        names = "、".join(sorted(unknown))
        raise ValueError(f"{label}包含未知字段：{names}")


def _strict_int(value: Any, label: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}必须是整数")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ValueError(f"{label}必须大于等于 {minimum}")
        raise ValueError(f"{label}必须位于 {minimum} 到 {maximum} 之间")
    return value


def _strict_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须是数字")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label}必须是有限数字")
    return number


def _strict_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label}必须是布尔值")
    return value


def _normalize_format(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("输出格式必须是字符串")
    normalized = value.strip().upper()
    if normalized == "JPG":
        normalized = "JPEG"
    if normalized not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(f"不支持的输出格式：{value}")
    return normalized


def _normalize_conflict_policy(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("同名冲突策略必须是字符串")
    normalized = value.strip().lower()
    if normalized not in SUPPORTED_CONFLICT_POLICIES:
        raise ValueError(f"未知的同名冲突策略：{value}")
    return normalized


def _normalize_transparency_background(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _HEX_BACKGROUND.fullmatch(value) is None:
        raise ValueError("透明背景色必须是 #RRGGBB，或使用 null 保留透明")
    return value.lower()


def _validate_rename_template(template: Any) -> str:
    if not isinstance(template, str):
        raise ValueError("重命名模板必须是字符串")
    if not template.strip():
        raise ValueError("重命名模板不能为空")
    if len(template) > 512:
        raise ValueError("重命名模板不能超过 512 个字符")

    sample_values: dict[str, object] = {
        "stem": "sample",
        "name": "sample.jpg",
        "ext": "jpg",
        "index": 1,
        "date": datetime(2026, 1, 2).strftime("%Y%m%d"),
        "datetime": datetime(2026, 1, 2, 3, 4, 5).strftime("%Y%m%d-%H%M%S"),
    }

    def replacement(match: re.Match[str]) -> str:
        field_name, specification = match.groups()
        value = sample_values[field_name]
        if specification:
            try:
                return format(value, specification)
            except (TypeError, ValueError) as error:
                raise ValueError(f"模板变量格式无效：{match.group(0)}") from error
        return str(value)

    rendered = _TEMPLATE_FIELD.sub(replacement, template)
    if "{" in rendered or "}" in rendered:
        raise ValueError("模板包含未知变量；支持 name、stem、ext、index、date、datetime")
    rendered = _INVALID_FILENAME.sub("_", rendered).strip(" .")
    if not rendered:
        raise ValueError("重命名模板生成了空文件名")
    return template


def _optional_dimension(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _strict_int(value, label, 1, _MAX_DIMENSION)


def _normalize_step_params(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    if kind == "crop":
        _reject_unknown_keys(raw, {"box"}, "裁切步骤")
        box = raw.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError("裁切框必须包含左、上、右、下四个归一化坐标")
        normalized = [_strict_number(value, "裁切坐标") for value in box]
        if not all(0.0 <= value <= 1.0 for value in normalized):
            raise ValueError("裁切坐标必须位于 0 到 1 之间")
        if normalized[2] <= normalized[0] or normalized[3] <= normalized[1]:
            raise ValueError("裁切框的右边和下边必须分别大于左边和上边")
        return {"box": normalized}

    if kind == "rotate":
        _reject_unknown_keys(raw, {"degrees"}, "旋转步骤")
        degrees = _strict_int(raw.get("degrees"), "旋转角度", -360_000, 360_000)
        if degrees % 90:
            raise ValueError("转图旋转角度必须是 90 度的整数倍")
        degrees %= 360
        if degrees == 0:
            raise ValueError("旋转步骤不能是无效果的 0 度")
        return {"degrees": degrees}

    if kind == "flip":
        _reject_unknown_keys(raw, {"axis"}, "翻转步骤")
        axis = raw.get("axis")
        if axis not in {"horizontal", "vertical"}:
            raise ValueError("翻转方向必须是 horizontal 或 vertical")
        return {"axis": axis}

    if kind == "resize":
        allowed = {"exact_width", "exact_height", "max_width", "max_height", "allow_upscale"}
        _reject_unknown_keys(raw, allowed, "尺寸步骤")
        exact_width = _optional_dimension(raw.get("exact_width"), "精确宽度")
        exact_height = _optional_dimension(raw.get("exact_height"), "精确高度")
        max_width = _optional_dimension(raw.get("max_width"), "最大宽度")
        max_height = _optional_dimension(raw.get("max_height"), "最大高度")
        has_exact = exact_width is not None or exact_height is not None
        has_maximum = max_width is not None or max_height is not None
        if has_exact and (exact_width is None or exact_height is None):
            raise ValueError("精确尺寸必须同时提供宽度和高度")
        if has_exact and has_maximum:
            raise ValueError("精确尺寸不能和最大尺寸同时设置")
        if not has_exact and not has_maximum:
            raise ValueError("尺寸步骤至少需要一项精确尺寸或最大尺寸")

        # Exact resizing promises the requested dimensions and therefore must be
        # permitted to enlarge a smaller source. Bound-based resizing defaults to
        # Pillow's existing thumbnail behavior and never enlarges unless requested.
        allow_upscale = _strict_bool(raw.get("allow_upscale", has_exact), "允许放大")
        if has_exact and not allow_upscale:
            raise ValueError("精确尺寸必须允许放大，否则无法保证目标宽高")

        normalized_resize: dict[str, Any] = {"allow_upscale": allow_upscale}
        for key, value in (
            ("exact_width", exact_width),
            ("exact_height", exact_height),
            ("max_width", max_width),
            ("max_height", max_height),
        ):
            if value is not None:
                normalized_resize[key] = value
        return normalized_resize

    if kind == "watermark":
        allowed = {"text", "position", "opacity", "size_percent"}
        _reject_unknown_keys(raw, allowed, "水印步骤")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("水印文字不能为空")
        if len(text) > 2_000:
            raise ValueError("水印文字不能超过 2,000 个字符")
        position = raw.get("position", "右下")
        if position not in _WATERMARK_POSITIONS:
            raise ValueError("水印位置无效")
        opacity = _strict_int(raw.get("opacity", 35), "水印透明度", 0, 100)
        size_percent = _strict_int(raw.get("size_percent", 5), "水印字号", 1, 30)
        return {
            "text": text.strip(),
            "position": position,
            "opacity": opacity,
            "size_percent": size_percent,
        }

    raise ValueError(f"未知的转图步骤：{kind}")


@dataclass(frozen=True)
class TransformStep:
    """One deterministic operation in a conversion recipe.

    Crop coordinates are normalized to the image entering this step. Step order is
    significant and is retained exactly in JSON.
    """

    kind: str
    params: dict[str, Any] = field(default_factory=dict)

    SUPPORTED_KINDS: ClassVar[frozenset[str]] = frozenset(
        {"crop", "rotate", "flip", "resize", "watermark"}
    )

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str):
            raise ValueError("转图步骤类型必须是字符串")
        kind = self.kind.strip().lower()
        if kind not in self.SUPPORTED_KINDS:
            raise ValueError(f"未知的转图步骤：{self.kind}")
        if not isinstance(self.params, dict):
            raise ValueError("转图步骤参数必须是对象")
        normalized = _normalize_step_params(kind, dict(self.params))
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "params", normalized)

    @classmethod
    def crop(cls, box: tuple[float, float, float, float]) -> TransformStep:
        return cls("crop", {"box": list(box)})

    @classmethod
    def rotate(cls, degrees: int) -> TransformStep:
        return cls("rotate", {"degrees": degrees})

    @classmethod
    def flip(cls, axis: Literal["horizontal", "vertical"]) -> TransformStep:
        return cls("flip", {"axis": axis})

    @classmethod
    def resize(
        cls,
        *,
        exact_width: int | None = None,
        exact_height: int | None = None,
        max_width: int | None = None,
        max_height: int | None = None,
        allow_upscale: bool | None = None,
    ) -> TransformStep:
        params: dict[str, Any] = {
            "exact_width": exact_width,
            "exact_height": exact_height,
            "max_width": max_width,
            "max_height": max_height,
        }
        if allow_upscale is not None:
            params["allow_upscale"] = allow_upscale
        return cls("resize", params)

    @classmethod
    def watermark(
        cls,
        text: str,
        *,
        position: str = "右下",
        opacity: int = 35,
        size_percent: int = 5,
    ) -> TransformStep:
        return cls(
            "watermark",
            {
                "text": text,
                "position": position,
                "opacity": opacity,
                "size_percent": size_percent,
            },
        )

    def validate(self) -> None:
        _normalize_step_params(self.kind, dict(self.params))

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "params": copy.deepcopy(self.params)}

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> TransformStep:
        if not isinstance(payload, dict):
            raise ValueError("转图步骤必须是对象")
        _reject_unknown_keys(payload, {"kind", "params"}, "转图步骤")
        if "kind" not in payload or "params" not in payload:
            raise ValueError("转图步骤必须包含 kind 和 params")
        return cls(kind=payload["kind"], params=payload["params"])


@dataclass(frozen=True)
class ExportProfile:
    """Output encoding, metadata, naming and conflict semantics."""

    output_format: str = "JPEG"
    quality: int = 85
    target_size_kb: int | None = None
    preserve_metadata: bool = True
    rename_template: str = "{stem}_{index:04d}"
    conflict_policy: str = "unique"
    preserve_animation: bool = True
    allow_in_place: bool = False
    backup_on_overwrite: bool = False
    transparency_background: str | None = None

    _JSON_FIELDS: ClassVar[set[str]] = {
        "output_format",
        "quality",
        "target_size_kb",
        "preserve_metadata",
        "rename_template",
        "conflict_policy",
        "preserve_animation",
        "transparency_background",
        "allow_in_place",
        "backup_on_overwrite",
    }

    def __post_init__(self) -> None:
        output_format = _normalize_format(self.output_format)
        quality = _strict_int(self.quality, "输出质量", 1, 100)
        target_size_kb = (
            None if self.target_size_kb is None else _strict_int(self.target_size_kb, "目标 KB", 1)
        )
        if target_size_kb is not None and output_format not in {"JPEG", "WEBP"}:
            raise ValueError("目标 KB 仅适用于 JPEG 和 WEBP")
        preserve_metadata = _strict_bool(self.preserve_metadata, "保留元数据")
        rename_template = _validate_rename_template(self.rename_template)
        conflict_policy = _normalize_conflict_policy(self.conflict_policy)
        preserve_animation = _strict_bool(self.preserve_animation, "保留动画")
        transparency_background = _normalize_transparency_background(self.transparency_background)
        allow_in_place = _strict_bool(self.allow_in_place, "允许原位覆盖")
        backup_on_overwrite = _strict_bool(self.backup_on_overwrite, "覆盖前创建备份")

        object.__setattr__(self, "output_format", output_format)
        object.__setattr__(self, "quality", quality)
        object.__setattr__(self, "target_size_kb", target_size_kb)
        object.__setattr__(self, "preserve_metadata", preserve_metadata)
        object.__setattr__(self, "rename_template", rename_template)
        object.__setattr__(self, "conflict_policy", conflict_policy)
        object.__setattr__(self, "preserve_animation", preserve_animation)
        object.__setattr__(self, "transparency_background", transparency_background)
        object.__setattr__(self, "allow_in_place", allow_in_place)
        object.__setattr__(self, "backup_on_overwrite", backup_on_overwrite)

    def validate(self) -> None:
        self.__post_init__()

    def to_json(self) -> dict[str, Any]:
        return {
            "output_format": self.output_format,
            "quality": self.quality,
            "target_size_kb": self.target_size_kb,
            "preserve_metadata": self.preserve_metadata,
            "rename_template": self.rename_template,
            "conflict_policy": self.conflict_policy,
            "preserve_animation": self.preserve_animation,
            "transparency_background": self.transparency_background,
            "allow_in_place": self.allow_in_place,
            "backup_on_overwrite": self.backup_on_overwrite,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> ExportProfile:
        if not isinstance(payload, dict):
            raise ValueError("导出配置必须是对象")
        _reject_unknown_keys(payload, cls._JSON_FIELDS, "导出配置")
        return cls(**payload)


@dataclass(frozen=True)
class TransformRecipe:
    """A complete, versioned and JSON-safe conversion recipe."""

    name: str = "未命名配方"
    steps: tuple[TransformStep, ...] = ()
    export_profile: ExportProfile = ExportProfile()
    schema_version: int = TRANSFORM_RECIPE_SCHEMA_VERSION

    _JSON_FIELDS: ClassVar[set[str]] = {"schema_version", "name", "steps", "export"}

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise ValueError("配方 schema_version 必须是整数")
        if self.schema_version != TRANSFORM_RECIPE_SCHEMA_VERSION:
            raise ValueError(f"不支持的转图配方版本：{self.schema_version}")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("配方名称不能为空")
        name = self.name.strip()
        if len(name) > 120 or any(ord(character) < 32 for character in name):
            raise ValueError("配方名称无效或超过 120 个字符")
        if not isinstance(self.steps, (list, tuple)):
            raise ValueError("配方步骤必须是列表或元组")
        steps = tuple(self.steps)
        if len(steps) > 32:
            raise ValueError("转图配方不能超过 32 个步骤")
        if not all(isinstance(step, TransformStep) for step in steps):
            raise ValueError("配方步骤必须全部是 TransformStep")
        if not isinstance(self.export_profile, ExportProfile):
            raise ValueError("配方导出配置必须是 ExportProfile")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "steps", steps)
        self._validate_step_uniqueness(steps)

    @staticmethod
    def _validate_step_uniqueness(steps: tuple[TransformStep, ...]) -> None:
        seen_singletons: set[str] = set()
        seen_flip_axes: set[str] = set()
        for step in steps:
            if step.kind == "flip":
                axis = str(step.params["axis"])
                if axis in seen_flip_axes:
                    raise ValueError(f"配方包含重复的 {axis} 翻转步骤")
                seen_flip_axes.add(axis)
                continue
            if step.kind in seen_singletons:
                raise ValueError(f"配方包含重复的 {step.kind} 步骤")
            seen_singletons.add(step.kind)

    def validate(self) -> None:
        self.__post_init__()
        for step in self.steps:
            step.validate()
        self.export_profile.validate()

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "steps": [step.to_json() for step in self.steps],
            "export": self.export_profile.to_json(),
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> TransformRecipe:
        if not isinstance(payload, dict):
            raise ValueError("转图配方必须是对象")
        _reject_unknown_keys(payload, cls._JSON_FIELDS, "转图配方")
        required = {"schema_version", "name", "steps", "export"}
        missing = required - set(payload)
        if missing:
            names = "、".join(sorted(missing))
            raise ValueError(f"转图配方缺少字段：{names}")
        raw_steps = payload["steps"]
        if not isinstance(raw_steps, list):
            raise ValueError("转图配方 steps 必须是数组")
        return cls(
            schema_version=payload["schema_version"],
            name=payload["name"],
            steps=tuple(TransformStep.from_json(step) for step in raw_steps),
            export_profile=ExportProfile.from_json(payload["export"]),
        )

    @classmethod
    def from_batch_recipe(
        cls,
        recipe: BatchRecipe,
        *,
        name: str = "批量转换",
    ) -> TransformRecipe:
        return from_batch_recipe(recipe, name=name)


def from_batch_recipe(recipe: BatchRecipe, *, name: str = "批量转换") -> TransformRecipe:
    """Map the existing batch recipe without changing or importing it at module load."""

    from ..batch import BatchRecipe as ExistingBatchRecipe

    if not isinstance(recipe, ExistingBatchRecipe):
        raise ValueError("recipe 必须是现有 BatchRecipe")
    recipe.validate()

    steps: list[TransformStep] = []
    if recipe.max_width is not None or recipe.max_height is not None:
        steps.append(TransformStep.resize(max_width=recipe.max_width, max_height=recipe.max_height))
    if recipe.watermark_text.strip():
        steps.append(
            TransformStep.watermark(
                recipe.watermark_text,
                position=recipe.watermark_position,
                opacity=recipe.watermark_opacity,
                size_percent=recipe.watermark_size_percent,
            )
        )

    return TransformRecipe(
        name=name,
        steps=tuple(steps),
        export_profile=ExportProfile(
            output_format=recipe.output_format,
            quality=recipe.quality,
            target_size_kb=None,
            preserve_metadata=not recipe.strip_metadata,
            rename_template=recipe.rename_template,
            conflict_policy=recipe.conflict_policy,
            preserve_animation=recipe.preserve_animation,
            allow_in_place=recipe.allow_in_place,
            backup_on_overwrite=False,
        ),
    )
