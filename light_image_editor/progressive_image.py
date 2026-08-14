"""Memory-bounded progressive image loading for the viewer-only shell."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .image_loader import decode_image

DEFAULT_CACHE_BYTES = 160 * 1024 * 1024
DEFAULT_TILE_SIZE = 1024


def image_memory_bytes(image: Image.Image) -> int:
    """Return the decoded pixel footprint used for cache accounting."""

    return max(1, image.width) * max(1, image.height) * max(1, len(image.getbands()))


def _rotate(image: Image.Image, quarter_turns: int) -> Image.Image:
    turns = int(quarter_turns) % 4
    if turns == 0:
        return image
    transpose = {
        1: Image.Transpose.ROTATE_270,
        2: Image.Transpose.ROTATE_180,
        3: Image.Transpose.ROTATE_90,
    }[turns]
    return image.transpose(transpose)


@dataclass(frozen=True)
class ProgressivePreview:
    image: Image.Image
    source_size: tuple[int, int]
    source_format: str


@dataclass(frozen=True)
class ProgressiveTile:
    rect: tuple[int, int, int, int]
    image: Image.Image


@dataclass
class _CacheEntry:
    path_key: str
    image: Image.Image
    source_size: tuple[int, int] | None = None
    source_format: str = ""

    @property
    def memory_bytes(self) -> int:
        return image_memory_bytes(self.image)


class ProgressiveImageLoader:
    """Decode screen previews and visible full-resolution tiles off the UI thread."""

    def __init__(
        self,
        *,
        max_workers: int = 2,
        max_cache_bytes: int = DEFAULT_CACHE_BYTES,
        tile_size: int = DEFAULT_TILE_SIZE,
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="progressive-image",
        )
        self._max_cache_bytes = max(8 * 1024 * 1024, int(max_cache_bytes))
        self._tile_size = max(256, min(2048, int(tile_size)))
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._cache_bytes = 0
        self._lock = threading.Lock()

    @property
    def max_cache_bytes(self) -> int:
        return self._max_cache_bytes

    @property
    def cache_bytes(self) -> int:
        with self._lock:
            return self._cache_bytes

    @property
    def tile_size(self) -> int:
        return self._tile_size

    @staticmethod
    def _path_key(path: str | Path) -> str:
        try:
            return str(Path(path).resolve()).casefold()
        except OSError:
            return str(Path(path).absolute()).casefold()

    def _preview_key(self, path: str | Path, max_size: tuple[int, int]) -> str:
        width, height = max_size
        return f"preview|{self._path_key(path)}|{width}x{height}"

    def _tile_key(
        self,
        path: str | Path,
        rect: tuple[int, int, int, int],
        quarter_turns: int,
    ) -> str:
        return (
            f"tile|{self._path_key(path)}|r={int(quarter_turns) % 4}|"
            f"{rect[0]},{rect[1]},{rect[2]},{rect[3]}"
        )

    def _cached_entry(self, key: str) -> _CacheEntry | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            self._cache.move_to_end(key)
            return _CacheEntry(
                entry.path_key,
                entry.image.copy(),
                entry.source_size,
                entry.source_format,
            )

    def _remember(self, key: str, entry: _CacheEntry) -> None:
        owned = _CacheEntry(
            entry.path_key,
            entry.image.copy(),
            entry.source_size,
            entry.source_format,
        )
        if owned.memory_bytes > self._max_cache_bytes:
            owned.image.close()
            return
        retired: list[Image.Image] = []
        with self._lock:
            previous = self._cache.pop(key, None)
            if previous is not None:
                self._cache_bytes -= previous.memory_bytes
                retired.append(previous.image)
            self._cache[key] = owned
            self._cache_bytes += owned.memory_bytes
            self._cache.move_to_end(key)
            while self._cache_bytes > self._max_cache_bytes and self._cache:
                _old_key, old = self._cache.popitem(last=False)
                self._cache_bytes -= old.memory_bytes
                retired.append(old.image)
        for image in retired:
            image.close()

    @staticmethod
    def _ready(value: object) -> Future:
        future: Future = Future()
        future.set_result(value)
        return future

    @staticmethod
    def _normalized_max_size(max_size: tuple[int, int]) -> tuple[int, int]:
        return max(64, int(max_size[0])), max(64, int(max_size[1]))

    def load_preview(
        self,
        path: str | Path,
        max_size: tuple[int, int],
        callback: Callable[[Future[ProgressivePreview]], object] | None = None,
    ) -> Future[ProgressivePreview]:
        normalized = self._normalized_max_size(max_size)
        key = self._preview_key(path, normalized)
        cached = self._cached_entry(key)
        if cached is not None:
            assert cached.source_size is not None
            future: Future[ProgressivePreview] = self._ready(
                ProgressivePreview(cached.image, cached.source_size, cached.source_format)
            )
        else:

            def decode_and_remember() -> ProgressivePreview:
                preview = self._decode_preview(path, normalized)
                self._remember(
                    key,
                    _CacheEntry(
                        self._path_key(path),
                        preview.image,
                        preview.source_size,
                        preview.source_format,
                    ),
                )
                return preview

            future = self._executor.submit(decode_and_remember)
        if callback is not None:
            future.add_done_callback(callback)
        return future

    @staticmethod
    def _decode_preview(path: str | Path, max_size: tuple[int, int]) -> ProgressivePreview:
        decoded = decode_image(path)
        try:
            source_size = decoded.size
            source_format = (decoded.format or Path(path).suffix.lstrip(".")).upper()
            preview = decoded.copy()
            preview.thumbnail(max_size, Image.Resampling.LANCZOS, reducing_gap=2.0)
            preview.info.update(decoded.info)
            preview.info["viewer_source_size"] = source_size
            preview.info["viewer_source_format"] = source_format
            return ProgressivePreview(preview, source_size, source_format)
        finally:
            decoded.close()

    def prefetch_previews(
        self,
        paths: Iterable[str | Path],
        max_size: tuple[int, int],
    ) -> None:
        for path in paths:
            future = self.load_preview(path, max_size)

            def release(completed: Future[ProgressivePreview]) -> None:
                if not completed.cancelled() and completed.exception() is None:
                    completed.result().image.close()

            future.add_done_callback(release)

    def load_full(
        self,
        path: str | Path,
        *,
        quarter_turns: int = 0,
    ) -> Future[Image.Image]:
        def decode() -> Image.Image:
            source = decode_image(path)
            rotated = _rotate(source, quarter_turns)
            if rotated is not source:
                source.close()
            return rotated

        return self._executor.submit(decode)

    def load_tiles(
        self,
        path: str | Path,
        rects: Sequence[tuple[int, int, int, int]],
        *,
        quarter_turns: int = 0,
    ) -> Future[tuple[ProgressiveTile, ...]]:
        normalized: tuple[tuple[int, int, int, int], ...] = tuple(
            dict.fromkeys(
                (int(left), int(top), int(right), int(bottom)) for left, top, right, bottom in rects
            )
        )
        cached_tiles: dict[tuple[int, int, int, int], Image.Image] = {}
        missing: list[tuple[int, int, int, int]] = []
        for rect in normalized:
            entry = self._cached_entry(self._tile_key(path, rect, quarter_turns))
            if entry is None:
                missing.append(rect)
            else:
                cached_tiles[rect] = entry.image
        if not missing:
            return self._ready(
                tuple(ProgressiveTile(rect, cached_tiles[rect]) for rect in normalized)
            )

        def decode_missing() -> tuple[ProgressiveTile, ...]:
            decoded = decode_image(path)
            source_to_close: Image.Image | None = decoded
            rotated: Image.Image | None = None
            try:
                rotated = _rotate(decoded, quarter_turns)
                if rotated is not decoded:
                    decoded.close()
                    source_to_close = None
                for rect in missing:
                    left, top, right, bottom = rect
                    bounded = (
                        max(0, min(rotated.width, left)),
                        max(0, min(rotated.height, top)),
                        max(0, min(rotated.width, right)),
                        max(0, min(rotated.height, bottom)),
                    )
                    if bounded[2] <= bounded[0] or bounded[3] <= bounded[1]:
                        continue
                    tile = rotated.crop(bounded)
                    cached_tiles[rect] = tile
                    self._remember(
                        self._tile_key(path, rect, quarter_turns),
                        _CacheEntry(self._path_key(path), tile),
                    )
                return tuple(
                    ProgressiveTile(rect, cached_tiles[rect])
                    for rect in normalized
                    if rect in cached_tiles
                )
            except BaseException:
                for tile in cached_tiles.values():
                    tile.close()
                raise
            finally:
                if rotated is not None:
                    rotated.close()
                if source_to_close is not None and source_to_close is not rotated:
                    source_to_close.close()

        return self._executor.submit(decode_missing)

    def invalidate(self, path: str | Path) -> None:
        path_key = self._path_key(path)
        retired: list[Image.Image] = []
        with self._lock:
            keys = [key for key, entry in self._cache.items() if entry.path_key == path_key]
            for key in keys:
                entry = self._cache.pop(key)
                self._cache_bytes -= entry.memory_bytes
                retired.append(entry.image)
        for image in retired:
            image.close()

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)
        with self._lock:
            images = [entry.image for entry in self._cache.values()]
            self._cache.clear()
            self._cache_bytes = 0
        for image in images:
            image.close()
