"""Bounded background thumbnail generation for directory browsing."""

from __future__ import annotations

import threading
import warnings
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from .image_loader import (
    MAX_ANIMATION_FRAMES,
    MAX_DECODE_PIXELS,
    MAX_IMAGE_DIMENSION,
    SUPPORTED_DECODE_FORMATS,
    friendly_load_failure,
)
from .pdf_support import PDF_EXTENSION, render_pdf_page

THUMBNAIL_SIZE = (66, 48)


def generate_thumbnail(
    path: str | Path,
    size: tuple[int, int] = THUMBNAIL_SIZE,
    background: str = "#eef2f7",
) -> Image.Image:
    """Decode *path* safely and return an orientation-correct letterboxed RGB preview."""

    image_path = Path(path)
    contained: Image.Image | None = None
    try:
        if image_path.suffix.casefold() == PDF_EXTENSION:
            rendered = render_pdf_page(image_path, dpi=72)
            try:
                rgba = rendered.convert("RGBA")
                try:
                    contained = ImageOps.contain(rgba, size, Image.Resampling.LANCZOS)
                finally:
                    rgba.close()
            finally:
                rendered.close()
            result = Image.new("RGBA", size, background)
            offset = ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2)
            result.alpha_composite(contained, offset)
            rgb = result.convert("RGB")
            result.close()
            return rgb
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(image_path) as source:
                if source.format not in SUPPORTED_DECODE_FORMATS:
                    raise ValueError(f"不支持的图片格式：{source.format or '未知'}")
                width, height = source.size
                if (
                    width <= 0
                    or height <= 0
                    or width > MAX_IMAGE_DIMENSION
                    or height > MAX_IMAGE_DIMENSION
                    or width * height > MAX_DECODE_PIXELS
                ):
                    raise ValueError(f"图片尺寸 {width} × {height} 超过安全上限，已停止解码。")
                frame_count = int(getattr(source, "n_frames", 1))
                if frame_count > MAX_ANIMATION_FRAMES:
                    raise ValueError(f"动画包含 {frame_count} 帧，超过安全上限，已停止解码。")
                source.seek(0)
                # JPEG and several optional codecs can decode a reduced pyramid
                # directly.  Requesting a modest overscan preserves LANCZOS edge
                # quality without allocating the full image merely for a 66px row.
                source.draft(
                    "RGBA" if "A" in source.getbands() else "RGB",
                    (size[0] * 4, size[1] * 4),
                )
                source.load()
                oriented = ImageOps.exif_transpose(source)
                try:
                    rgba = oriented.convert("RGBA")
                    try:
                        contained = ImageOps.contain(rgba, size, Image.Resampling.LANCZOS)
                    finally:
                        rgba.close()
                finally:
                    if oriented is not source:
                        oriented.close()
        result = Image.new("RGBA", size, background)
        offset = ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2)
        result.alpha_composite(contained, offset)
        rgb = result.convert("RGB")
        result.close()
        return rgb
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise friendly_load_failure(image_path, error) from error
    finally:
        if contained is not None:
            contained.close()


class ThumbnailLoader:
    """Generate thumbnails off the Tk thread with a bounded LRU cache."""

    def __init__(self, max_workers: int = 2, cache_size: int = 96) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers), thread_name_prefix="thumbnail-decode"
        )
        self._cache_size = max(1, cache_size)
        self._cache: OrderedDict[str, Image.Image] = OrderedDict()
        self._inflight: dict[str, Future[Image.Image]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(path: str | Path) -> str:
        try:
            return str(Path(path).resolve()).casefold()
        except OSError:
            return str(Path(path).absolute()).casefold()

    def load(self, path: str | Path) -> Future[Image.Image]:
        key = self._key(path)
        remember_new_future = False
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                ready: Future[Image.Image] = Future()
                ready.set_result(cached.copy())
                return ready
            existing = self._inflight.get(key)
            if existing is not None:
                return existing
            future = self._executor.submit(generate_thumbnail, path)
            self._inflight[key] = future
            remember_new_future = True
        if remember_new_future:

            def remember(completed: Future[Image.Image], cache_key: str = key) -> None:
                self._remember(cache_key, completed)

            future.add_done_callback(remember)
        return future

    def _remember(self, key: str, future: Future[Image.Image]) -> None:
        with self._lock:
            self._inflight.pop(key, None)
            if future.cancelled() or future.exception() is not None:
                return
            self._cache[key] = future.result().copy()
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                _, image = self._cache.popitem(last=False)
                image.close()

    def invalidate(self, path: str | Path) -> None:
        key = self._key(path)
        with self._lock:
            image = self._cache.pop(key, None)
        if image is not None:
            image.close()

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)
        with self._lock:
            images = list(self._cache.values())
            self._cache.clear()
        for image in images:
            image.close()
