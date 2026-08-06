"""Deterministic local repair for rectangular regions and binary masks."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageFilter


class RegionRepairError(ValueError):
    """Raised when a local repair request is unsafe or malformed."""


@dataclass(frozen=True, slots=True)
class RegionRepairSpec:
    region: tuple[float, float, float, float]
    feather: int = 8

    def __post_init__(self) -> None:
        if (
            len(self.region) != 4
            or any(isinstance(value, bool) for value in self.region)
            or any(
                not isinstance(value, (int, float)) or not math.isfinite(float(value))
                for value in self.region
            )
        ):
            raise RegionRepairError("修复区域必须包含四个有限数值")
        left, top, right, bottom = (float(value) for value in self.region)
        if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
            raise RegionRepairError("修复区域必须位于图片内且宽高大于零")
        object.__setattr__(self, "region", (left, top, right, bottom))
        if isinstance(self.feather, bool) or not isinstance(self.feather, int):
            raise RegionRepairError("修复羽化必须是整数")
        if not 0 <= self.feather <= 64:
            raise RegionRepairError("修复羽化必须在 0 到 64 像素之间")


def _pixel_region(
    region: tuple[float, float, float, float],
    size: tuple[int, int],
) -> tuple[int, int, int, int]:
    width, height = size
    left = max(0, min(width - 1, round(region[0] * width)))
    top = max(0, min(height - 1, round(region[1] * height)))
    right = max(left + 1, min(width, round(region[2] * width)))
    bottom = max(top + 1, min(height, round(region[3] * height)))
    if right - left < 4 or bottom - top < 4:
        raise RegionRepairError("修复区域至少需要 4 × 4 像素")
    return (left, top, right, bottom)


def _boundary_fill(source: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = box
    patch_width = right - left
    patch_height = bottom - top
    top_y = max(0, top - 1)
    bottom_y = min(source.height - 1, bottom)
    left_x = max(0, left - 1)
    right_x = min(source.width - 1, right)

    top_edge = source.crop((left, top_y, right, top_y + 1))
    bottom_edge = source.crop((left, bottom_y, right, bottom_y + 1))
    left_edge = source.crop((left_x, top, left_x + 1, bottom))
    right_edge = source.crop((right_x, top, right_x + 1, bottom))
    vertical_mask = Image.linear_gradient("L").resize((patch_width, patch_height))
    horizontal_mask = vertical_mask.rotate(90, expand=True).resize((patch_width, patch_height))
    try:
        top_layer = top_edge.resize((patch_width, patch_height), Image.Resampling.BILINEAR)
        bottom_layer = bottom_edge.resize((patch_width, patch_height), Image.Resampling.BILINEAR)
        left_layer = left_edge.resize((patch_width, patch_height), Image.Resampling.BILINEAR)
        right_layer = right_edge.resize((patch_width, patch_height), Image.Resampling.BILINEAR)
        try:
            vertical = Image.composite(bottom_layer, top_layer, vertical_mask)
            horizontal = Image.composite(right_layer, left_layer, horizontal_mask)
            try:
                return Image.blend(vertical, horizontal, 0.5)
            finally:
                vertical.close()
                horizontal.close()
        finally:
            top_layer.close()
            bottom_layer.close()
            left_layer.close()
            right_layer.close()
    finally:
        top_edge.close()
        bottom_edge.close()
        left_edge.close()
        right_edge.close()
        vertical_mask.close()
        horizontal_mask.close()


def repair_region(image: Image.Image, spec: RegionRepairSpec) -> Image.Image:
    """Return an RGBA copy with the selected region reconstructed from its boundary."""

    if not isinstance(spec, RegionRepairSpec):
        raise TypeError("spec 必须是 RegionRepairSpec")
    if image.width < 1 or image.height < 1:
        raise RegionRepairError("源图片尺寸无效")
    if image.width * image.height > 128_000_000:
        raise RegionRepairError("源图片超过 128,000,000 像素安全上限")
    source = image.convert("RGBA")
    box = _pixel_region(spec.region, source.size)
    left, top, right, bottom = box
    fill = _boundary_fill(source, box)
    repaired = source.copy()
    try:
        if spec.feather == 0:
            repaired.paste(fill, (left, top))
            return repaired
        mask = Image.new("L", source.size, 0)
        try:
            mask.paste(255, box)
            blurred = mask.filter(ImageFilter.GaussianBlur(radius=spec.feather))
            try:
                layer = source.copy()
                try:
                    layer.paste(fill, (left, top))
                    return Image.composite(layer, source, blurred)
                finally:
                    layer.close()
            finally:
                blurred.close()
        finally:
            mask.close()
    finally:
        fill.close()
        source.close()


def repair_mask(
    image: Image.Image,
    mask: Image.Image,
    *,
    feather: int = 0,
) -> Image.Image:
    """Reconstruct only selected mask pixels from the mask boundary."""

    if not isinstance(mask, Image.Image):
        raise TypeError("mask 必须是 Pillow 图片")
    if isinstance(feather, bool) or not isinstance(feather, int):
        raise RegionRepairError("修复羽化必须是整数")
    if not 0 <= feather <= 64:
        raise RegionRepairError("修复羽化必须在 0 到 64 像素之间")
    if image.width < 1 or image.height < 1:
        raise RegionRepairError("源图片尺寸无效")
    if image.width * image.height > 128_000_000:
        raise RegionRepairError("源图片超过 128,000,000 像素安全上限")
    if mask.size != image.size:
        raise RegionRepairError("修复蒙版尺寸必须与源图片一致")

    source = image.convert("RGBA")
    binary_mask = mask.convert("L").point(lambda value: 255 if value else 0)
    box = binary_mask.getbbox()
    if box is None:
        source.close()
        binary_mask.close()
        raise RegionRepairError("修复蒙版不能为空")
    left, top, right, bottom = box
    if right - left < 4 or bottom - top < 4:
        source.close()
        binary_mask.close()
        raise RegionRepairError("修复蒙版边界至少需要 4 × 4 像素")

    fill = _boundary_fill(source, box)
    layer = source.copy()
    try:
        layer.paste(fill, (left, top))
        if feather == 0:
            return Image.composite(layer, source, binary_mask)
        softened = binary_mask.filter(ImageFilter.GaussianBlur(radius=feather))
        try:
            return Image.composite(layer, source, softened)
        finally:
            softened.close()
    finally:
        fill.close()
        layer.close()
        source.close()
        binary_mask.close()


__all__ = [
    "RegionRepairError",
    "RegionRepairSpec",
    "repair_mask",
    "repair_region",
]
