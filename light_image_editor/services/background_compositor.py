"""Deterministic local compositing for transparent smart-edit revisions."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import cast

from PIL import Image, ImageColor, ImageOps, UnidentifiedImageError

DEFAULT_MAX_COMPOSITION_PIXELS = 100_000_000
DEFAULT_MAX_BACKGROUND_BYTES = 256 * 1024 * 1024
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


class BackgroundCompositionError(ValueError):
    """Raised when a background request or source image cannot be composed safely."""


class BackgroundKind(StrEnum):
    COLOR = "color"
    IMAGE = "image"


@dataclass(frozen=True, slots=True)
class BackgroundSpec:
    kind: BackgroundKind
    color: str = "#ffffff"
    image_path: Path | None = None

    def __post_init__(self) -> None:
        try:
            kind = BackgroundKind(self.kind)
        except ValueError as error:
            raise BackgroundCompositionError("背景类型无效") from error
        object.__setattr__(self, "kind", kind)
        color = self.color.strip().lower()
        if _HEX_COLOR.fullmatch(color) is None:
            raise BackgroundCompositionError("背景色必须使用 #RRGGBB 格式")
        object.__setattr__(self, "color", color)
        if kind is BackgroundKind.IMAGE:
            if self.image_path is None:
                raise BackgroundCompositionError("请选择一张自定义背景图片")
            object.__setattr__(self, "image_path", Path(self.image_path).expanduser())
        elif self.image_path is not None:
            object.__setattr__(self, "image_path", None)

    @classmethod
    def solid_color(cls, color: str) -> BackgroundSpec:
        return cls(BackgroundKind.COLOR, color=color)

    @classmethod
    def custom_image(cls, image_path: str | Path, *, fallback_color: str) -> BackgroundSpec:
        return cls(
            BackgroundKind.IMAGE,
            color=fallback_color,
            image_path=Path(image_path),
        )


@dataclass(frozen=True, slots=True)
class BackgroundCompositionResult:
    output_path: Path
    width: int
    height: int
    source_had_transparency: bool
    background_sha256: str | None


def _open_loaded_rgba(
    source: str | Path | BytesIO,
    *,
    label: str,
    max_pixels: int,
) -> Image.Image:
    try:
        with Image.open(source) as opened:
            width, height = opened.size
            if width < 1 or height < 1:
                raise BackgroundCompositionError(f"{label}尺寸无效")
            if width * height > max_pixels:
                raise BackgroundCompositionError(f"{label}超过 {max_pixels:,} 像素安全上限")
            opened.load()
            return opened.convert("RGBA")
    except BackgroundCompositionError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise BackgroundCompositionError(f"无法安全读取{label}") from error


def _read_background_bytes(path: Path, *, max_bytes: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise BackgroundCompositionError("自定义背景图片不存在或不可读取") from error
    if size < 1:
        raise BackgroundCompositionError("自定义背景图片为空")
    if size > max_bytes:
        raise BackgroundCompositionError(f"自定义背景图片超过 {max_bytes:,} 字节安全上限")
    try:
        content = path.read_bytes()
    except OSError as error:
        raise BackgroundCompositionError("自定义背景图片不存在或不可读取") from error
    if len(content) != size:
        raise BackgroundCompositionError("读取自定义背景图片时文件发生变化")
    return content


def compose_background(
    foreground_path: str | Path,
    output_path: str | Path,
    spec: BackgroundSpec,
    *,
    max_pixels: int = DEFAULT_MAX_COMPOSITION_PIXELS,
    max_background_bytes: int = DEFAULT_MAX_BACKGROUND_BYTES,
) -> BackgroundCompositionResult:
    """Composite an RGBA foreground over a solid colour or centre-cropped image."""

    if not isinstance(spec, BackgroundSpec):
        raise TypeError("spec 必须是 BackgroundSpec")
    if max_pixels < 1 or max_background_bytes < 1:
        raise ValueError("合成安全上限必须大于零")
    foreground = Path(foreground_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if foreground == output:
        raise BackgroundCompositionError("输出路径不能覆盖透明源修订")
    if spec.image_path is not None and spec.image_path.resolve() == output:
        raise BackgroundCompositionError("输出路径不能覆盖自定义背景图片")
    if not foreground.is_file():
        raise BackgroundCompositionError("透明源修订不存在或不可读取")

    rgba = _open_loaded_rgba(foreground, label="透明源修订", max_pixels=max_pixels)
    background_sha256: str | None = None
    prepared_background: Image.Image | None = None
    base: Image.Image | None = None
    composed: Image.Image | None = None
    temporary_path: Path | None = None
    try:
        width, height = rgba.size
        alpha_channel = rgba.getchannel("A")
        try:
            alpha_extrema = cast(tuple[int, int], alpha_channel.getextrema())
        finally:
            alpha_channel.close()
        source_had_transparency = alpha_extrema[0] < 255
        red, green, blue = cast(tuple[int, int, int], ImageColor.getrgb(spec.color))
        base = Image.new("RGBA", rgba.size, (red, green, blue, 255))
        if spec.kind is BackgroundKind.IMAGE:
            assert spec.image_path is not None
            content = _read_background_bytes(
                spec.image_path,
                max_bytes=max_background_bytes,
            )
            background_sha256 = hashlib.sha256(content).hexdigest()
            background = _open_loaded_rgba(
                BytesIO(content),
                label="自定义背景图片",
                max_pixels=max_pixels,
            )
            try:
                prepared_background = ImageOps.fit(
                    background,
                    rgba.size,
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            finally:
                background.close()
            base.alpha_composite(prepared_background)
        composed = Image.alpha_composite(base, rgba)
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}-",
            suffix=".png.tmp",
            dir=output.parent,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        composed.save(temporary_path, format="PNG", optimize=False)
        os.replace(temporary_path, output)
        temporary_path = None
        return BackgroundCompositionResult(
            output_path=output,
            width=width,
            height=height,
            source_had_transparency=source_had_transparency,
            background_sha256=background_sha256,
        )
    except BackgroundCompositionError:
        raise
    except OSError as error:
        raise BackgroundCompositionError("背景合成结果无法安全写入") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if composed is not None:
            composed.close()
        if base is not None:
            base.close()
        if prepared_background is not None:
            prepared_background.close()
        rgba.close()
