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

from .document import (
    ORIENTATION_TAG,
    SourceMetadata,
    attach_source_metadata,
    replace_orientation_in_exif,
)

MAX_DECODE_PIXELS = 120_000_000
MAX_IMAGE_DIMENSION = 100_000
MAX_ANIMATION_FRAMES = 10_000
SUPPORTED_DECODE_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF", "GIF"}


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
    if isinstance(error, OSError) and "truncated" in str(error).casefold():
        return ImageLoadFailure(image_path, "truncated", "图片数据不完整，已拒绝加载截断文件。")
    if isinstance(error, ValueError) and "安全上限" in str(error):
        return ImageLoadFailure(image_path, "too_large", str(error))
    if isinstance(error, ValueError) and "不支持的图片格式" in str(error):
        return ImageLoadFailure(image_path, "unsupported", str(error))
    return ImageLoadFailure(image_path, "decode", f"无法解码图片：{error}")


def decode_image(path: str | Path) -> Image.Image:
    image_path = Path(path)
    try:
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
                source_format = source.format
                source_mode = source.mode
                source_info = dict(source.info)
                is_animated = bool(getattr(source, "is_animated", False))
                frame_count = int(getattr(source, "n_frames", 1))
                if frame_count > MAX_ANIMATION_FRAMES:
                    raise ValueError(f"动画包含 {frame_count} 帧，超过安全上限，已停止解码。")
                loop = source_info.get("loop")
                try:
                    source_exif = source.getexif()
                except (AttributeError, OSError, SyntaxError):
                    source_exif = Image.Exif()
                orientation = int(source_exif.get(ORIENTATION_TAG, 1) or 1)
                raw_exif = source_info.get("exif")
                if not isinstance(raw_exif, bytes) and len(source_exif):
                    raw_exif = source_exif.tobytes()
                durations: list[int] = []
                if is_animated:
                    for frame_index in range(frame_count):
                        source.seek(frame_index)
                        duration = source.info.get("duration")
                        durations.append(int(duration) if isinstance(duration, int) else 0)
                    source.seek(0)
                source.load()
                result = ImageOps.exif_transpose(source)
                result.load()
                decoded = result.copy()
                dpi = source_info.get("dpi")
                dpi_tuple = (
                    (float(dpi[0]), float(dpi[1]))
                    if isinstance(dpi, tuple) and len(dpi) == 2
                    else None
                )
                xmp_value = source_info.get("xmp", source_info.get("XML:com.adobe.xmp"))
                if isinstance(xmp_value, str):
                    xmp_value = xmp_value.encode("utf-8")
                metadata = SourceMetadata(
                    original_format=source_format,
                    original_mode=source_mode,
                    original_orientation=orientation,
                    exif=replace_orientation_in_exif(
                        raw_exif if isinstance(raw_exif, bytes) else None
                    ),
                    icc_profile=(
                        source_info.get("icc_profile")
                        if isinstance(source_info.get("icc_profile"), bytes)
                        else None
                    ),
                    dpi=dpi_tuple,
                    xmp=xmp_value if isinstance(xmp_value, bytes) else None,
                    has_alpha="A" in decoded.getbands() or "transparency" in source_info,
                    is_animated=is_animated,
                    frame_count=frame_count,
                    loop=int(loop) if isinstance(loop, int) else None,
                    durations=tuple(durations),
                )
                return attach_source_metadata(decoded, metadata)
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
