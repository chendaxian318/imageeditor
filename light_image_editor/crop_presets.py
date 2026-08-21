"""Versioned, portable crop-ratio and output-size preset configuration."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from math import gcd
from pathlib import Path
from typing import Any

PRESET_SCHEMA_VERSION = 1
MAX_RATIO_PRESETS = 64
MAX_SIZES_PER_RATIO = 64
MAX_DIMENSION = 100_000
MAX_PRESET_FILE_BYTES = 512 * 1024


def _positive_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field}必须是正整数")
    if value < 1 or value > MAX_DIMENSION:
        raise ValueError(f"{field}必须在 1 到 {MAX_DIMENSION} 之间")
    return value


@dataclass(frozen=True)
class CropRatioPreset:
    """One normalized crop ratio and the exact-size buttons assigned to it."""

    width: int
    height: int
    sizes: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        width = _positive_integer(self.width, "比例宽度")
        height = _positive_integer(self.height, "比例高度")
        divisor = gcd(width, height)
        width //= divisor
        height //= divisor
        normalized_sizes: list[tuple[int, int]] = []
        for raw_size in self.sizes:
            if not isinstance(raw_size, (tuple, list)) or len(raw_size) != 2:
                raise ValueError("尺寸必须包含宽度和高度")
            size_width = _positive_integer(raw_size[0], "尺寸宽度")
            size_height = _positive_integer(raw_size[1], "尺寸高度")
            if size_width * height != size_height * width:
                raise ValueError(
                    f"尺寸 {size_width} × {size_height} 与比例 {width}:{height} 不一致"
                )
            size = (size_width, size_height)
            if size in normalized_sizes:
                raise ValueError(f"尺寸 {size_width} × {size_height} 重复")
            normalized_sizes.append(size)
        if len(normalized_sizes) > MAX_SIZES_PER_RATIO:
            raise ValueError(f"每个比例最多保存 {MAX_SIZES_PER_RATIO} 个尺寸")
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "sizes", tuple(normalized_sizes))

    @property
    def label(self) -> str:
        return f"{self.width}:{self.height}"

    @property
    def value(self) -> float:
        return self.width / self.height

    def to_json(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "sizes": [[width, height] for width, height in self.sizes],
        }

    @classmethod
    def from_json(cls, payload: object) -> CropRatioPreset:
        if not isinstance(payload, dict):
            raise ValueError("比例预设必须是对象")
        unknown = set(payload) - {"width", "height", "sizes"}
        if unknown:
            raise ValueError(f"比例预设包含未知字段：{', '.join(sorted(unknown))}")
        sizes = payload.get("sizes", [])
        if not isinstance(sizes, list):
            raise ValueError("比例预设的尺寸列表无效")
        return cls(
            _positive_integer(payload.get("width"), "比例宽度"),
            _positive_integer(payload.get("height"), "比例高度"),
            tuple(sizes),
        )


@dataclass(frozen=True)
class CropPresetCatalog:
    """Validated collection used by settings, import/export and the Qt panel."""

    presets: tuple[CropRatioPreset, ...]

    def __post_init__(self) -> None:
        if len(self.presets) > MAX_RATIO_PRESETS:
            raise ValueError(f"最多保存 {MAX_RATIO_PRESETS} 个比例")
        labels: set[str] = set()
        sizes: set[tuple[int, int]] = set()
        for preset in self.presets:
            if not isinstance(preset, CropRatioPreset):
                raise ValueError("比例配置包含无效项目")
            if preset.label in labels:
                raise ValueError(f"比例 {preset.label} 重复")
            labels.add(preset.label)
            for size in preset.sizes:
                if size in sizes:
                    raise ValueError(f"尺寸 {size[0]} × {size[1]} 重复")
                sizes.add(size)

    @classmethod
    def defaults(cls) -> CropPresetCatalog:
        return cls(
            (
                CropRatioPreset(1, 1, ((512, 512), (108, 108), (28, 28), (1440, 1440))),
                CropRatioPreset(5, 3),
                CropRatioPreset(13, 5, ((650, 250),)),
                CropRatioPreset(16, 9, ((1280, 720), (1920, 1080))),
                CropRatioPreset(9, 16, ((720, 1280), (1080, 1920))),
                CropRatioPreset(5, 4, ((500, 400),)),
                CropRatioPreset(4, 3),
                CropRatioPreset(3, 4),
                CropRatioPreset(3, 2),
                CropRatioPreset(2, 3),
                CropRatioPreset(18, 9),
                CropRatioPreset(9, 18, ((1080, 2160),)),
                CropRatioPreset(9, 19, ((1080, 2280),)),
                CropRatioPreset(9, 20, ((1080, 2400),)),
            )
        )

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": PRESET_SCHEMA_VERSION,
            "presets": [preset.to_json() for preset in self.presets],
        }

    @classmethod
    def from_json(cls, payload: object) -> CropPresetCatalog:
        if not isinstance(payload, dict):
            raise ValueError("比例与尺寸配置必须是对象")
        unknown = set(payload) - {"schema_version", "presets"}
        if unknown:
            raise ValueError(f"配置包含未知字段：{', '.join(sorted(unknown))}")
        if payload.get("schema_version") != PRESET_SCHEMA_VERSION:
            raise ValueError("不支持的比例与尺寸配置版本")
        presets = payload.get("presets")
        if not isinstance(presets, list):
            raise ValueError("配置缺少比例预设列表")
        return cls(tuple(CropRatioPreset.from_json(item) for item in presets))

    def preset(self, label: str) -> CropRatioPreset | None:
        return next((preset for preset in self.presets if preset.label == label), None)


def load_crop_preset_catalog(path: str | Path) -> CropPresetCatalog:
    source = Path(path)
    if source.stat().st_size > MAX_PRESET_FILE_BYTES:
        raise ValueError("比例与尺寸配置文件过大")
    try:
        payload: Any = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取比例与尺寸配置：{error}") from error
    return CropPresetCatalog.from_json(payload)


def save_crop_preset_catalog(path: str | Path, catalog: CropPresetCatalog) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(catalog.to_json(), ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            Path(temporary_name).unlink()
        except FileNotFoundError:
            pass
        raise
