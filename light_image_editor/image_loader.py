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
from .formats import HEIF_EXTENSIONS, AnimationSequence, decode_animation, register_optional_heif
from .pdf_support import PDF_EXTENSION, PdfSupportError, render_pdf_page
from .psd_support import PSD_EXTENSIONS, PsdSupportError, render_psd_composite

MAX_DECODE_PIXELS = 120_000_000
MAX_IMAGE_DIMENSION = 100_000
MAX_ANIMATION_FRAMES = 10_000
SUPPORTED_DECODE_FORMATS = {
    "JPEG",
    "PNG",
    "WEBP",
    "BMP",
    "TIFF",
    "GIF",
    "AVIF",
    "HEIF",
    "PSD",
}
CACHED_PREVIEW_MAX_SIZE = (1600, 1600)

HEIF_AVAILABLE, HEIF_DETAIL = register_optional_heif()


@dataclass(frozen=True)
class ImageLoadFailure(Exception):
    path: Path
    kind: str
    user_message: str

    def __str__(self) -> str:
        return self.user_message


def friendly_load_failure(path: str | Path, error: BaseException) -> ImageLoadFailure:
    image_path = Path(path)
    if image_path.suffix.casefold() in HEIF_EXTENSIONS and not HEIF_AVAILABLE:
        return ImageLoadFailure(
            image_path,
            "optional_codec",
            "该文件需要可选 HEIF/HEIC 解码插件。请安装 requirements-heif.lock 后重试。",
        )
    if isinstance(error, FileNotFoundError):
        return ImageLoadFailure(image_path, "missing", "文件已被移动或删除。")
    if isinstance(error, PermissionError):
        return ImageLoadFailure(image_path, "permission", "没有权限读取这个文件。")
    if isinstance(error, PdfSupportError):
        return ImageLoadFailure(image_path, "pdf", str(error))
    if isinstance(error, PsdSupportError):
        return ImageLoadFailure(image_path, "psd", str(error))
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


def decode_image(path: str | Path, page_index: int = 0) -> Image.Image:
    image_path = Path(path)
    try:
        if image_path.suffix.casefold() == PDF_EXTENSION:
            return render_pdf_page(image_path, page_index)
        if image_path.suffix.casefold() in PSD_EXTENSIONS:
            return render_psd_composite(image_path)
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
        self._preview_cache: dict[str, Image.Image] = {}
        self._inflight: dict[str, Future[None]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(path: str | Path, page_index: int | None = None) -> str:
        try:
            key = str(Path(path).resolve()).casefold()
        except OSError:
            key = str(Path(path).absolute()).casefold()
        if Path(path).suffix.casefold() == PDF_EXTENSION:
            key += f"#page={page_index or 0}"
        return key

    def load(
        self,
        path: str | Path,
        callback: Callable[[Future[Image.Image]], object] | None = None,
        *,
        take_cached: bool = False,
        page_index: int | None = None,
    ) -> Future[Image.Image]:
        key = self._key(path, page_index)
        direct_pdf_transfer = take_cached and Path(path).suffix.casefold() == PDF_EXTENSION
        new_base_future: Future[None] | None = None
        discarded_preview: Image.Image | None = None
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                future: Future[Image.Image] = Future()
                if take_cached:
                    # Navigation transfers the cache-owned full bitmap directly
                    # into the new Document.  Copying a 24 MP RGB image on Tk's
                    # thread added visible latency even though decode/prefetch had
                    # already completed.  The screen-sized proxy stays available
                    # for the first render; adjacent prefetch repopulates the full
                    # cache when the user moves on again.
                    self._cache.pop(key)
                    if "#page=" in key:
                        # PDF pages are already retained by the continuous
                        # document view. Keeping another full-page proxy in the
                        # loader only duplicates memory and is never used for
                        # folder navigation.
                        discarded_preview = self._preview_cache.pop(key, None)
                    future.set_result(cached)
                else:
                    self._cache.move_to_end(key)
                    future.set_result(cached.copy())
            else:
                base_future = self._inflight.get(key)
                if base_future is None and direct_pdf_transfer:
                    # The continuous PDF view owns every rendered page it asks
                    # for. Decode directly into that owner instead of first
                    # creating full-size and proxy copies in the navigation LRU.
                    future = self._executor.submit(decode_image, path, page_index or 0)
                else:
                    if base_future is None:
                        base_future = self._executor.submit(
                            self._decode_into_cache, key, path, page_index
                        )
                        self._inflight[key] = base_future
                        new_base_future = base_future
                    future = self._copy_after_cached(key, base_future)
        if discarded_preview is not None:
            discarded_preview.close()
        if new_base_future is not None:

            def clear(completed: Future[None], cache_key: str = key) -> None:
                self._clear_inflight(cache_key, completed)

            new_base_future.add_done_callback(clear)
        if callback is not None:
            future.add_done_callback(callback)
        return future

    def _decode_into_cache(
        self,
        key: str,
        path: str | Path,
        page_index: int | None = None,
    ) -> None:
        decoded = decode_image(path, page_index or 0)
        try:
            self._store_decoded(key, decoded)
        finally:
            decoded.close()

    def _store_decoded(self, key: str, decoded: Image.Image) -> None:
        cached = decoded.copy()
        scale = min(
            CACHED_PREVIEW_MAX_SIZE[0] / decoded.width,
            CACHED_PREVIEW_MAX_SIZE[1] / decoded.height,
            1.0,
        )
        preview_size = (
            max(1, round(decoded.width * scale)),
            max(1, round(decoded.height * scale)),
        )
        preview = (
            decoded.copy()
            if preview_size == decoded.size
            else decoded.resize(
                preview_size,
                Image.Resampling.LANCZOS,
                reducing_gap=2.0,
            )
        )
        retired: list[Image.Image] = []
        with self._lock:
            old_cached = self._cache.pop(key, None)
            old_preview = self._preview_cache.pop(key, None)
            if old_cached is not None:
                retired.append(old_cached)
            if old_preview is not None:
                retired.append(old_preview)
            self._cache[key] = cached
            self._preview_cache[key] = preview
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                old_key, old_image = self._cache.popitem(last=False)
                retired.append(old_image)
                old_small = self._preview_cache.pop(old_key, None)
                if old_small is not None:
                    retired.append(old_small)
        for image in retired:
            image.close()

    def _copy_after_cached(
        self,
        key: str,
        base_future: Future[None],
    ) -> Future[Image.Image]:
        response: Future[Image.Image] = Future()

        def deliver(completed: Future[None]) -> None:
            if completed.cancelled():
                response.cancel()
                return
            error = completed.exception()
            if error is not None:
                response.set_exception(error)
                return
            with self._lock:
                cached = self._cache.get(key)
                if cached is None:
                    response.set_exception(RuntimeError("decoded image was not cached"))
                    return
                self._cache.move_to_end(key)
                image = cached.copy()
            response.set_result(image)

        base_future.add_done_callback(deliver)
        return response

    def _clear_inflight(self, key: str, future: Future[None]) -> None:
        with self._lock:
            if self._inflight.get(key) is future:
                self._inflight.pop(key, None)

    def cached_preview(self, path: str | Path, page_index: int | None = None) -> Image.Image | None:
        """Return an owned screen-sized preview when adjacent prefetch is ready."""

        key = self._key(path, page_index)
        with self._lock:
            preview = self._preview_cache.get(key)
            if preview is None:
                return None
            if key in self._cache:
                self._cache.move_to_end(key)
            return preview.copy()

    def prefetch(self, paths: Iterable[str | Path]) -> None:
        for path in paths:
            key = self._key(path)
            with self._lock:
                if key in self._cache or key in self._inflight:
                    continue
                future = self._executor.submit(self._decode_into_cache, key, path, None)
                self._inflight[key] = future

            def clear(completed: Future[None], cache_key: str = key) -> None:
                self._clear_inflight(cache_key, completed)

            future.add_done_callback(clear)

    def load_animation(self, path: str | Path) -> Future[AnimationSequence]:
        """Decode an animation after its first frame without blocking Tk."""

        return self._executor.submit(decode_animation, path)

    def invalidate(self, path: str | Path) -> None:
        key = self._key(path)
        base_key = key.partition("#page=")[0]
        with self._lock:
            keys = [
                cached_key
                for cached_key in set(self._cache) | set(self._preview_cache)
                if cached_key == base_key or cached_key.startswith(f"{base_key}#page=")
            ]
            retired = [self._cache.pop(cached_key, None) for cached_key in keys]
            retired.extend(self._preview_cache.pop(cached_key, None) for cached_key in keys)
        for image in retired:
            if image is not None:
                image.close()

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)
        with self._lock:
            images = list(self._cache.values())
            images.extend(self._preview_cache.values())
            self._cache.clear()
            self._preview_cache.clear()
        for image in images:
            image.close()
