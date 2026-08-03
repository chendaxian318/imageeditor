"""Bounded background decoding and adjacent-image prefetch."""

from __future__ import annotations

import threading
import warnings
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


@dataclass(frozen=True)
class ImageLoadFailure(Exception):
    path: Path
    kind: str
    user_message: str

    def __str__(self) -> str:
        return self.user_message


def friendly_load_failure(path: str | Path, error: BaseException) -> ImageLoadFailure:
    image_path = Path(path)
    if isinstance(error, FileNotFoundError):
        return ImageLoadFailure(image_path, "missing", "文件已被移动或删除。")
    if isinstance(error, PermissionError):
        return ImageLoadFailure(image_path, "permission", "没有权限读取这个文件。")
    if isinstance(error, (Image.DecompressionBombError, Image.DecompressionBombWarning)):
        return ImageLoadFailure(image_path, "too_large", "图片尺寸过大，已停止解码以保护内存。")
    if isinstance(error, UnidentifiedImageError):
        return ImageLoadFailure(image_path, "damaged", "文件已损坏或不是受支持的图片。")
    return ImageLoadFailure(image_path, "decode", f"无法解码图片：{error}")


def decode_image(path: str | Path) -> Image.Image:
    image_path = Path(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(image_path) as source:
                source.load()
                result = ImageOps.exif_transpose(source)
                result.load()
                return result.copy()
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise friendly_load_failure(image_path, error) from error


class ImageLoader:
    """Decode images away from Tk's main thread with a small LRU cache."""

    def __init__(self, max_workers: int = 2, cache_size: int = 3) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="image-decode"
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

    def load(
        self,
        path: str | Path,
        callback: Callable[[Future[Image.Image]], object] | None = None,
    ) -> Future[Image.Image]:
        key = self._key(path)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                future: Future[Image.Image] = Future()
                future.set_result(cached.copy())
            else:
                existing = self._inflight.get(key)
                if existing is not None:
                    future = existing
                else:
                    future = self._executor.submit(decode_image, path)
                    self._inflight[key] = future

                    def remember(completed: Future[Image.Image], cache_key: str = key) -> None:
                        self._remember(cache_key, completed)

                    future.add_done_callback(remember)
        if callback is not None:
            future.add_done_callback(callback)
        return future

    def _remember(self, key: str, future: Future[Image.Image]) -> None:
        with self._lock:
            self._inflight.pop(key, None)
            if future.cancelled() or future.exception() is not None:
                return
            self._cache[key] = future.result().copy()
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                _, old_image = self._cache.popitem(last=False)
                old_image.close()

    def prefetch(self, paths: Iterable[str | Path]) -> None:
        for path in paths:
            self.load(path)

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
