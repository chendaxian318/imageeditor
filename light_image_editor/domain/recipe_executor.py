"""Deterministic pixel execution for ordered conversion recipes."""

from __future__ import annotations

import math
from typing import Any

from PIL import Image

from image_ops import apply_text_watermark

from .recipes import TransformRecipe, TransformStep


def _with_source_info(source: Image.Image, result: Image.Image) -> Image.Image:
    """Carry undecoded metadata until the export policy decides whether to retain it."""

    result.info.update(source.info)
    return result


def _crop(image: Image.Image, params: dict[str, Any]) -> Image.Image:
    left, top, right, bottom = (float(value) for value in params["box"])
    pixel_box = (
        max(0, min(image.width - 1, math.floor(left * image.width))),
        max(0, min(image.height - 1, math.floor(top * image.height))),
        max(1, min(image.width, math.ceil(right * image.width))),
        max(1, min(image.height, math.ceil(bottom * image.height))),
    )
    if pixel_box[2] <= pixel_box[0] or pixel_box[3] <= pixel_box[1]:
        raise ValueError("裁切步骤在当前图片尺寸下生成了空区域")
    return _with_source_info(image, image.crop(pixel_box))


def _rotate(image: Image.Image, params: dict[str, Any]) -> Image.Image:
    clockwise = int(params["degrees"]) % 360
    transpose = {
        90: Image.Transpose.ROTATE_270,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_90,
    }[clockwise]
    return _with_source_info(image, image.transpose(transpose))


def _flip(image: Image.Image, params: dict[str, Any]) -> Image.Image:
    transpose = (
        Image.Transpose.FLIP_LEFT_RIGHT
        if params["axis"] == "horizontal"
        else Image.Transpose.FLIP_TOP_BOTTOM
    )
    return _with_source_info(image, image.transpose(transpose))


def _resize(image: Image.Image, params: dict[str, Any]) -> Image.Image:
    exact_width = params.get("exact_width")
    exact_height = params.get("exact_height")
    if exact_width is not None and exact_height is not None:
        size = (int(exact_width), int(exact_height))
    else:
        width_scale = (
            float(params["max_width"]) / image.width
            if params.get("max_width") is not None
            else math.inf
        )
        height_scale = (
            float(params["max_height"]) / image.height
            if params.get("max_height") is not None
            else math.inf
        )
        scale = min(width_scale, height_scale)
        if not bool(params["allow_upscale"]):
            scale = min(1.0, scale)
        size = (
            max(1, round(image.width * scale)),
            max(1, round(image.height * scale)),
        )
    if size == image.size:
        return image.copy()
    return _with_source_info(image, image.resize(size, Image.Resampling.LANCZOS))


def _watermark(image: Image.Image, params: dict[str, Any]) -> Image.Image:
    result = apply_text_watermark(
        image,
        str(params["text"]),
        str(params["position"]),
        int(params["opacity"]),
        int(params["size_percent"]),
    )
    return _with_source_info(image, result)


def _execute_step(image: Image.Image, step: TransformStep) -> Image.Image:
    executors = {
        "crop": _crop,
        "rotate": _rotate,
        "flip": _flip,
        "resize": _resize,
        "watermark": _watermark,
    }
    try:
        execute = executors[step.kind]
    except KeyError as error:  # Defensive: TransformStep currently rejects unknown kinds.
        raise ValueError(f"不支持执行转图步骤：{step.kind}") from error
    return execute(image, step.params)


def _has_partial_transparency(image: Image.Image) -> bool:
    if "A" not in image.getbands() and not (image.mode == "P" and "transparency" in image.info):
        return False
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    try:
        return any(alpha.histogram()[1:255])
    finally:
        alpha.close()
        rgba.close()


def _apply_export_background(
    image: Image.Image,
    recipe: TransformRecipe,
) -> Image.Image:
    profile = recipe.export_profile
    background_color = profile.transparency_background
    if background_color is None:
        return image
    has_alpha = "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
    requires_background = profile.output_format in {"JPEG", "BMP"} and has_alpha
    requires_background = requires_background or (
        profile.output_format == "GIF" and _has_partial_transparency(image)
    )
    if not requires_background:
        return image
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", image.size, background_color)
    try:
        background.alpha_composite(rgba)
        flattened = background.convert("RGB")
        return _with_source_info(image, flattened)
    finally:
        rgba.close()
        background.close()


def execute_transform_recipe(image: Image.Image, recipe: TransformRecipe) -> Image.Image:
    """Apply every recipe step in declaration order and return an owned image.

    The source image is never mutated or closed. Intermediate images are closed as
    soon as the next step succeeds, and the returned image belongs to the caller.
    """

    if not isinstance(image, Image.Image):
        raise TypeError("image 必须是 PIL Image")
    if not isinstance(recipe, TransformRecipe):
        raise TypeError("recipe 必须是 TransformRecipe")
    recipe.validate()

    current = image.copy()
    try:
        for step in recipe.steps:
            next_image = _execute_step(current, step)
            current.close()
            current = next_image
        prepared = _apply_export_background(current, recipe)
        if prepared is not current:
            current.close()
            current = prepared
    except BaseException:
        current.close()
        raise
    return current
