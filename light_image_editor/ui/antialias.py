"""Supersampled raster helpers for crisp Tk Canvas surfaces."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageTk

Painter = Callable[[ImageDraw.ImageDraw, int], None]


def render_antialiased(
    width: int,
    height: int,
    painter: Painter,
    *,
    scale: int = 4,
) -> Image.Image:
    """Render a transparent control layer at high resolution and downsample it."""

    safe_width = max(1, int(width))
    safe_height = max(1, int(height))
    safe_scale = max(2, int(scale))
    image = Image.new(
        "RGBA",
        (safe_width * safe_scale, safe_height * safe_scale),
        (0, 0, 0, 0),
    )
    painter(ImageDraw.Draw(image), safe_scale)
    return image.resize(
        (safe_width, safe_height),
        Image.Resampling.LANCZOS,
        reducing_gap=3.0,
    )


def render_photo(
    width: int,
    height: int,
    painter: Painter,
    *,
    master: tk.Misc,
    scale: int = 4,
) -> ImageTk.PhotoImage:
    """Create a Tk image from a supersampled transparent layer."""

    return ImageTk.PhotoImage(
        render_antialiased(width, height, painter, scale=scale),
        master=master,
    )
