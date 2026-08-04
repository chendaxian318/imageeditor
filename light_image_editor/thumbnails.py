"""Bounded background thumbnail generation for directory browsing."""

from __future__ import annotations

import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from .image_loader import decode_image

THUMBNAIL_SIZE = (88, 72)


def generate_thumbnail(
    path: str | Path,
    size: tuple[int, int] = THUMBNAIL_SIZE,
    background: str = "#eef2f7",
) -> Image.Image:
    """Decode *path* safely and return an orientation-correct letterboxed RGB preview."""

    decoded = decode_image(path)
    contained: Image.Image | None = None
    try:
        rgba = decoded.convert("RGBA")
        try:
            contained = ImageOps.contain(rgba, size, Image.Resampling.LANCZOS)
        finally:
            rgba.close()
        result = Image.new("RGBA", size, background)
        offset = ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2)
        result.alpha_composite(contained, offset)
        rgb = result.convert("RGB")
        result.close()
        return rgb
    finally:
        if contained is not None:
            contained.close()
        decoded.close()


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
