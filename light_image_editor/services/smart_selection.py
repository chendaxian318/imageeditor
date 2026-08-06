"""Deterministic offline subject-region selection."""

from __future__ import annotations

import base64
import binascii
import math
import struct
import zlib
from array import array
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError

from light_image_editor.domain.selection_mask_refinement import (
    MAX_MASK_POINTS,
    MAX_MASK_STROKES,
    SelectionMaskStroke,
)

from .background_analysis import estimate_border_background

MAX_SMART_SELECTION_PIXELS: Final = 80_000_000
DEFAULT_PROXY_DIMENSION: Final = 512
MAX_SELECTION_MASK_PIXELS: Final = 4_194_304
MAX_SELECTION_MASK_PAYLOAD: Final = 1_000_000


class SmartSelectionError(ValueError):
    """Raised when a selection request cannot be evaluated safely."""


@dataclass(frozen=True, slots=True)
class SmartSelectionSuggestion:
    region: tuple[float, float, float, float]
    confidence: float
    foreground_fraction: float
    source: str
    mask_size: tuple[int, int] | None = None
    mask_rle: str | None = None
    selection_kind: str = "component"

    def __post_init__(self) -> None:
        if len(self.region) != 4 or any(not math.isfinite(value) for value in self.region):
            raise SmartSelectionError("智能选区坐标必须包含四个有限数字")
        left, top, right, bottom = self.region
        if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
            raise SmartSelectionError("智能选区必须位于图片范围内")
        if not 0 <= self.confidence <= 1:
            raise SmartSelectionError("智能选区置信度必须在 0 到 1 之间")
        if not 0 < self.foreground_fraction <= 1:
            raise SmartSelectionError("智能选区前景占比必须在 0 到 1 之间")
        if self.source not in {"alpha", "contrast"}:
            raise SmartSelectionError("智能选区来源无效")
        if self.selection_kind not in {"component", "color_split", "shape_split"}:
            raise SmartSelectionError("智能选区候选类型无效")
        if (self.mask_size is None) != (self.mask_rle is None):
            raise SmartSelectionError("智能选区蒙版尺寸与数据必须同时提供")
        if self.mask_size is not None:
            width, height = self.mask_size
            if (
                isinstance(width, bool)
                or isinstance(height, bool)
                or not isinstance(width, int)
                or not isinstance(height, int)
                or width < 1
                or height < 1
                or width * height > MAX_SELECTION_MASK_PIXELS
            ):
                raise SmartSelectionError("智能选区蒙版尺寸无效")
            if (
                not isinstance(self.mask_rle, str)
                or not self.mask_rle
                or len(self.mask_rle) > MAX_SELECTION_MASK_PAYLOAD
            ):
                raise SmartSelectionError("智能选区蒙版数据无效")


def _encode_component(indices: list[int]) -> str:
    indices.sort()
    payload = bytearray()
    run_start = indices[0]
    previous = run_start
    for index in indices[1:]:
        if index == previous + 1:
            previous = index
            continue
        payload.extend(struct.pack(">II", run_start, previous - run_start + 1))
        run_start = index
        previous = index
    payload.extend(struct.pack(">II", run_start, previous - run_start + 1))
    encoded = base64.b64encode(zlib.compress(bytes(payload), level=9)).decode("ascii")
    if len(encoded) > MAX_SELECTION_MASK_PAYLOAD:
        raise SmartSelectionError("智能选区蒙版压缩数据超过安全上限")
    return encoded


def encode_selection_mask(mask: Image.Image) -> str:
    """Encode a non-empty bounded mask into canonical compressed RLE."""

    if not isinstance(mask, Image.Image):
        raise TypeError("mask 必须是 Pillow 图片")
    width, height = mask.size
    if width < 1 or height < 1 or width * height > MAX_SELECTION_MASK_PIXELS:
        raise SmartSelectionError("智能选区蒙版尺寸超过安全上限")
    grayscale = mask.convert("L")
    try:
        indices = [index for index, value in enumerate(grayscale.tobytes()) if value]
    finally:
        grayscale.close()
    if not indices:
        raise SmartSelectionError("智能选区蒙版不能为空")
    return _encode_component(indices)


def decode_selection_mask(
    mask_rle: str,
    mask_size: tuple[int, int],
    *,
    target_size: tuple[int, int] | None = None,
) -> Image.Image:
    """Decode a bounded RLE mask and optionally resize it with nearest sampling."""

    if not isinstance(mask_rle, str) or not mask_rle or len(mask_rle) > MAX_SELECTION_MASK_PAYLOAD:
        raise SmartSelectionError("智能选区蒙版数据无效")
    if (
        not isinstance(mask_size, tuple)
        or len(mask_size) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in mask_size)
    ):
        raise SmartSelectionError("智能选区蒙版尺寸无效")
    width, height = mask_size
    if width < 1 or height < 1 or width * height > MAX_SELECTION_MASK_PIXELS:
        raise SmartSelectionError("智能选区蒙版尺寸超过安全上限")
    try:
        compressed = base64.b64decode(mask_rle, validate=True)
        inflater = zlib.decompressobj()
        payload = inflater.decompress(compressed, MAX_SELECTION_MASK_PIXELS * 8 + 1)
        if inflater.unconsumed_tail or inflater.unused_data or not inflater.eof:
            raise SmartSelectionError("智能选区蒙版压缩数据无效")
    except (binascii.Error, zlib.error) as error:
        raise SmartSelectionError("智能选区蒙版无法安全解码") from error
    if not payload or len(payload) % 8 or len(payload) > MAX_SELECTION_MASK_PIXELS * 8:
        raise SmartSelectionError("智能选区蒙版游程数据无效")
    pixels = bytearray(width * height)
    previous_end = 0
    for offset in range(0, len(payload), 8):
        start, length = struct.unpack_from(">II", payload, offset)
        end = start + length
        if length < 1 or start < previous_end or end > len(pixels):
            raise SmartSelectionError("智能选区蒙版游程越界或重叠")
        pixels[start:end] = b"\xff" * length
        previous_end = end
    mask = Image.frombytes("L", (width, height), bytes(pixels))
    if target_size is None or target_size == mask.size:
        return mask
    target_width, target_height = target_size
    if (
        isinstance(target_width, bool)
        or isinstance(target_height, bool)
        or not isinstance(target_width, int)
        or not isinstance(target_height, int)
        or target_width < 1
        or target_height < 1
        or target_width * target_height > MAX_SMART_SELECTION_PIXELS
    ):
        mask.close()
        raise SmartSelectionError("智能选区目标蒙版尺寸无效")
    resized = mask.resize(target_size, Image.Resampling.NEAREST)
    mask.close()
    return resized


def refine_selection_mask(
    mask_rle: str,
    mask_size: tuple[int, int],
    strokes: tuple[SelectionMaskStroke, ...],
) -> str:
    """Apply ordered add/remove strokes and return canonical refined mask RLE."""

    normalized = tuple(strokes)
    if (
        not normalized
        or len(normalized) > MAX_MASK_STROKES
        or any(not isinstance(stroke, SelectionMaskStroke) for stroke in normalized)
        or sum(len(stroke.points) for stroke in normalized) > MAX_MASK_POINTS
    ):
        raise SmartSelectionError("蒙版修整笔划数量或类型无效")
    mask = decode_selection_mask(mask_rle, mask_size)
    try:
        draw = ImageDraw.Draw(mask)
        width, height = mask.size
        minimum_side = min(mask.size)
        for stroke in normalized:
            points = [
                (
                    round(x * max(1, width - 1)),
                    round(y * max(1, height - 1)),
                )
                for x, y in stroke.points
            ]
            radius = max(1, round(stroke.radius * minimum_side))
            value = 255 if stroke.mode == "add" else 0
            if len(points) > 1:
                draw.line(points, fill=value, width=radius * 2, joint="curve")
            for x, y in (points[0], points[-1]):
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius),
                    fill=value,
                )
        return encode_selection_mask(mask)
    finally:
        mask.close()


def _indices_box(
    indices: list[int],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    for index in indices:
        y, x = divmod(index, width)
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
    return (min_x, min_y, max_x + 1, max_y + 1)


def _color_subcomponents(
    indices: list[int],
    rgb_data: bytes,
    width: int,
    height: int,
) -> tuple[list[int], ...]:
    if len(indices) < 128 or len(rgb_data) != width * height * 3:
        return ()
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for index in indices:
        offset = index * 3
        red, green, blue = rgb_data[offset : offset + 3]
        key = (red // 32, green // 32, blue // 32)
        aggregate = buckets.setdefault(key, [0, 0, 0, 0])
        aggregate[0] += 1
        aggregate[1] += red
        aggregate[2] += green
        aggregate[3] += blue
    minimum_seed_area = max(32, round(len(indices) * 0.12))
    seeds: list[tuple[int, int, int]] = []
    for key, aggregate in sorted(
        buckets.items(),
        key=lambda item: (-item[1][0], item[0]),
    ):
        count = aggregate[0]
        if count < minimum_seed_area:
            continue
        color = (
            round(aggregate[1] / count),
            round(aggregate[2] / count),
            round(aggregate[3] / count),
        )
        if all(
            sum((color[channel] - seed[channel]) ** 2 for channel in range(3)) >= 70**2
            for seed in seeds
        ):
            seeds.append(color)
        if len(seeds) >= 4:
            break
    if len(seeds) < 2:
        return ()

    clusters: list[list[int]] = [[] for _seed in seeds]
    for index in indices:
        offset = index * 3
        pixel_color = rgb_data[offset : offset + 3]
        selected_seed = min(
            range(len(seeds)),
            key=lambda seed_index: (
                sum(
                    (pixel_color[channel] - seeds[seed_index][channel]) ** 2 for channel in range(3)
                ),
                seed_index,
            ),
        )
        clusters[selected_seed].append(index)

    minimum_cluster_area = max(32, round(len(indices) * 0.10))
    accepted: list[list[int]] = []
    centers: list[tuple[float, float]] = []
    for cluster in clusters:
        if len(cluster) < minimum_cluster_area:
            continue
        box = _indices_box(cluster, width, height)
        box_area = (box[2] - box[0]) * (box[3] - box[1])
        if len(cluster) / max(1, box_area) < 0.18:
            continue
        accepted.append(cluster)
        centers.append(
            (
                sum(index % width for index in cluster) / len(cluster),
                sum(index // width for index in cluster) / len(cluster),
            )
        )
    if len(accepted) < 2:
        return ()
    component_box = _indices_box(indices, width, height)
    diagonal = math.hypot(
        component_box[2] - component_box[0],
        component_box[3] - component_box[1],
    )
    if max(
        math.dist(first, second)
        for first_index, first in enumerate(centers)
        for second in centers[first_index + 1 :]
    ) < max(8.0, diagonal * 0.12):
        return ()
    accepted.sort(
        key=lambda cluster: (
            -len(cluster),
            _indices_box(cluster, width, height),
        )
    )
    return tuple(accepted)


def _shape_subcomponents(
    indices: list[int],
    width: int,
    height: int,
) -> tuple[list[int], ...]:
    """Split a visibly lobed component using bounded thickness peaks."""

    if len(indices) < 256:
        return ()
    component_box = _indices_box(indices, width, height)
    box_width = component_box[2] - component_box[0]
    box_height = component_box[3] - component_box[1]
    if min(box_width, box_height) < 16:
        return ()

    total = width * height
    present = bytearray(total)
    for index in indices:
        present[index] = 1
    depths = array("H", [0]) * total
    boundary: deque[int] = deque()
    for index in indices:
        y, x = divmod(index, width)
        if (
            x == 0
            or x + 1 == width
            or y == 0
            or y + 1 == height
            or not present[index - 1]
            or not present[index + 1]
            or not present[index - width]
            or not present[index + width]
        ):
            depths[index] = 1
            boundary.append(index)
    while boundary:
        current = boundary.popleft()
        next_depth = depths[current] + 1
        y, x = divmod(current, width)
        neighbors = []
        if x > 0:
            neighbors.append(current - 1)
        if x + 1 < width:
            neighbors.append(current + 1)
        if y > 0:
            neighbors.append(current - width)
        if y + 1 < height:
            neighbors.append(current + width)
        for neighbor in neighbors:
            if present[neighbor] and depths[neighbor] == 0:
                depths[neighbor] = next_depth
                boundary.append(neighbor)

    minimum_peak_depth = max(4, round(min(box_width, box_height) * 0.08))
    maximum_depth = max((depths[index] for index in indices), default=0)
    if maximum_depth < minimum_peak_depth:
        return ()
    maxima = bytearray(total)
    maxima_indices: list[int] = []
    for index in indices:
        depth = depths[index]
        if depth < minimum_peak_depth or depth < maximum_depth * 0.60:
            continue
        y, x = divmod(index, width)
        is_maximum = True
        for neighbor_y in range(max(0, y - 1), min(height, y + 2)):
            row = neighbor_y * width
            for neighbor_x in range(max(0, x - 1), min(width, x + 2)):
                neighbor = row + neighbor_x
                if present[neighbor] and depths[neighbor] > depth:
                    is_maximum = False
                    break
            if not is_maximum:
                break
        if is_maximum:
            maxima[index] = 1
            maxima_indices.append(index)
    if not maxima_indices:
        return ()

    maxima_seen = bytearray(total)
    peak_groups: list[tuple[int, float, float, list[int]]] = []
    for start in maxima_indices:
        if maxima_seen[start]:
            continue
        maxima_seen[start] = 1
        queue: deque[int] = deque((start,))
        group: list[int] = []
        while queue:
            current = queue.popleft()
            group.append(current)
            y, x = divmod(current, width)
            for neighbor_y in range(max(0, y - 1), min(height, y + 2)):
                row = neighbor_y * width
                for neighbor_x in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = row + neighbor_x
                    if maxima[neighbor] and not maxima_seen[neighbor]:
                        maxima_seen[neighbor] = 1
                        queue.append(neighbor)
        representative = min(group, key=lambda item: (-depths[item], item))
        peak_groups.append(
            (
                depths[representative],
                sum(index % width for index in group) / len(group),
                sum(index // width for index in group) / len(group),
                group,
            )
        )
    peak_groups.sort(key=lambda item: (-item[0], -len(item[3]), item[2], item[1]))
    separated_peaks: list[tuple[int, float, float, list[int]]] = []
    for peak in peak_groups:
        if all(
            math.dist((peak[1], peak[2]), (accepted[1], accepted[2]))
            >= max(8.0, min(peak[0], accepted[0]) * 1.25)
            for accepted in separated_peaks
        ):
            separated_peaks.append(peak)
        if len(separated_peaks) >= 8:
            break
    if len(separated_peaks) < 2:
        return ()

    neck_level = max(2, round(min(peak[0] for peak in separated_peaks) * 0.75))
    core_labels = bytearray(total)
    next_label = 1
    for peak in separated_peaks:
        representative = peak[3][0]
        if core_labels[representative]:
            continue
        core_labels[representative] = next_label
        queue = deque((representative,))
        while queue:
            current = queue.popleft()
            y, x = divmod(current, width)
            neighbors = []
            if x > 0:
                neighbors.append(current - 1)
            if x + 1 < width:
                neighbors.append(current + 1)
            if y > 0:
                neighbors.append(current - width)
            if y + 1 < height:
                neighbors.append(current + width)
            for neighbor in neighbors:
                if (
                    present[neighbor]
                    and depths[neighbor] > neck_level
                    and core_labels[neighbor] == 0
                ):
                    core_labels[neighbor] = next_label
                    queue.append(neighbor)
        next_label += 1
    distinct_peaks: list[tuple[int, float, float, list[int]]] = []
    used_labels: set[int] = set()
    for peak in separated_peaks:
        label = core_labels[peak[3][0]]
        if label and label not in used_labels:
            distinct_peaks.append(peak)
            used_labels.add(label)
        if len(distinct_peaks) >= 4:
            break
    if len(distinct_peaks) < 2:
        return ()

    owners = bytearray(total)
    wave: deque[int] = deque()
    for owner, peak in enumerate(distinct_peaks, start=1):
        for index in sorted(peak[3]):
            owners[index] = owner
            wave.append(index)
    while wave:
        current = wave.popleft()
        owner = owners[current]
        y, x = divmod(current, width)
        neighbors = []
        if x > 0:
            neighbors.append(current - 1)
        if x + 1 < width:
            neighbors.append(current + 1)
        if y > 0:
            neighbors.append(current - width)
        if y + 1 < height:
            neighbors.append(current + width)
        for neighbor in neighbors:
            if present[neighbor] and owners[neighbor] == 0:
                owners[neighbor] = owner
                wave.append(neighbor)
    clusters = [list[int]() for _peak in distinct_peaks]
    for index in indices:
        owner = owners[index]
        if owner == 0:
            return ()
        clusters[owner - 1].append(index)
    minimum_cluster_area = max(64, round(len(indices) * 0.12))
    if any(len(cluster) < minimum_cluster_area for cluster in clusters):
        return ()
    if max(len(cluster) for cluster in clusters) > len(indices) * 0.82:
        return ()
    centers = [
        (
            sum(index % width for index in cluster) / len(cluster),
            sum(index // width for index in cluster) / len(cluster),
        )
        for cluster in clusters
    ]
    diagonal = math.hypot(box_width, box_height)
    if min(
        math.dist(first, second)
        for first_index, first in enumerate(centers)
        for second in centers[first_index + 1 :]
    ) < max(8.0, diagonal * 0.16):
        return ()
    clusters.sort(key=lambda cluster: (-len(cluster), _indices_box(cluster, width, height)))
    return tuple(clusters)


def _components(
    mask: Image.Image,
    rgb_data: bytes,
) -> tuple[tuple[tuple[int, int, int, int], int, str, str], ...]:
    width, height = mask.size
    data = mask.tobytes()
    visited = bytearray(width * height)
    components: list[tuple[tuple[int, int, int, int], int, str, str]] = []
    for start in range(width * height):
        if not data[start] or visited[start]:
            continue
        visited[start] = 1
        queue: deque[int] = deque((start,))
        area = 0
        min_x = width
        min_y = height
        max_x = -1
        max_y = -1
        indices: list[int] = []
        while queue:
            current = queue.popleft()
            indices.append(current)
            y, x = divmod(current, width)
            area += 1
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
            if x > 0:
                neighbor = current - 1
                if data[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)
            if x + 1 < width:
                neighbor = current + 1
                if data[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)
            if y > 0:
                neighbor = current - width
                if data[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)
            if y + 1 < height:
                neighbor = current + width
                if data[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    queue.append(neighbor)
        components.append(
            (
                (min_x, min_y, max_x + 1, max_y + 1),
                area,
                _encode_component(indices),
                "component",
            )
        )
        for cluster in _color_subcomponents(indices, rgb_data, width, height):
            components.append(
                (
                    _indices_box(cluster, width, height),
                    len(cluster),
                    _encode_component(cluster),
                    "color_split",
                )
            )
        for cluster in _shape_subcomponents(indices, width, height):
            components.append(
                (
                    _indices_box(cluster, width, height),
                    len(cluster),
                    _encode_component(cluster),
                    "shape_split",
                )
            )
    components.sort(
        key=lambda item: (
            -item[1],
            {"component": 0, "color_split": 1, "shape_split": 2}[item[3]],
            item[0][1],
            item[0][0],
            item[0][3],
            item[0][2],
        )
    )
    return tuple(components)


def _contrast_mask(rgba: Image.Image) -> tuple[Image.Image, int]:
    background, spread = estimate_border_background(rgba)
    rgb = rgba.convert("RGB")
    background_image = Image.new("RGB", rgb.size, background)
    difference = ImageChops.difference(rgb, background_image)
    rgb.close()
    background_image.close()
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    red.close()
    green.close()
    blue.close()
    difference.close()
    threshold = max(14, min(112, spread + 6))
    mask = maximum.point(lambda value: 255 if value > threshold else 0)
    maximum.close()
    cleaned = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    mask.close()
    return (cleaned, threshold)


def detect_smart_selection(
    source: str | Path,
    *,
    maximum_dimension: int = DEFAULT_PROXY_DIMENSION,
    padding_ratio: float = 0.025,
    maximum_suggestions: int = 5,
) -> tuple[SmartSelectionSuggestion, ...]:
    """Return prominent subject regions in stable area-descending order."""

    if (
        isinstance(maximum_dimension, bool)
        or not isinstance(maximum_dimension, int)
        or not 64 <= maximum_dimension <= 2048
    ):
        raise SmartSelectionError("智能选区代理尺寸必须在 64 到 2048 之间")
    if (
        isinstance(padding_ratio, bool)
        or not isinstance(padding_ratio, (int, float))
        or not math.isfinite(float(padding_ratio))
        or not 0 <= float(padding_ratio) <= 0.25
    ):
        raise SmartSelectionError("智能选区留白比例必须在 0 到 0.25 之间")
    if (
        isinstance(maximum_suggestions, bool)
        or not isinstance(maximum_suggestions, int)
        or not 1 <= maximum_suggestions <= 8
    ):
        raise SmartSelectionError("智能选区候选数量必须在 1 到 8 之间")
    path = Path(source).expanduser().resolve(strict=True)
    try:
        with Image.open(path) as opened:
            if opened.width * opened.height > MAX_SMART_SELECTION_PIXELS:
                raise SmartSelectionError("图片超过智能选区像素安全上限")
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            try:
                if min(oriented.size) < 8:
                    raise SmartSelectionError("图片尺寸过小，无法执行智能选区")
                scale = min(1.0, maximum_dimension / max(oriented.size))
                proxy_size = (
                    max(8, round(oriented.width * scale)),
                    max(8, round(oriented.height * scale)),
                )
                proxy = oriented.convert("RGBA")
                if proxy.size != proxy_size:
                    resized = proxy.resize(proxy_size, Image.Resampling.LANCZOS)
                    proxy.close()
                    proxy = resized
            finally:
                if oriented is not opened:
                    oriented.close()
    except SmartSelectionError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise SmartSelectionError("无法安全读取智能选区图片") from error

    threshold = 0
    alpha = proxy.getchannel("A")
    alpha_extrema = cast(tuple[int, int], alpha.getextrema())
    alpha_values = cast(list[int], list(alpha.getdata()))
    alpha_fraction = sum(value >= 16 for value in alpha_values) / (proxy.width * proxy.height)
    if alpha_extrema[0] < 240 and 0.002 <= alpha_fraction <= 0.995:
        mask = alpha.point(lambda value: 255 if value >= 16 else 0)
        source_kind = "alpha"
    else:
        mask, threshold = _contrast_mask(proxy)
        source_kind = "contrast"
    rgb_proxy = proxy.convert("RGB")
    try:
        rgb_data = rgb_proxy.tobytes()
    finally:
        rgb_proxy.close()
    alpha.close()
    proxy.close()
    try:
        components = _components(mask, rgb_data)
        if not components:
            return ()
        total = mask.width * mask.height
        pad_x = round(mask.width * float(padding_ratio))
        pad_y = round(mask.height * float(padding_ratio))
        suggestions: list[SmartSelectionSuggestion] = []
        for box, area, mask_rle, selection_kind in components:
            fraction = area / total
            box_area = (box[2] - box[0]) * (box[3] - box[1])
            if fraction < 0.002 or (fraction > 0.90 and box_area / total > 0.96):
                continue
            left = max(0, box[0] - pad_x)
            top = max(0, box[1] - pad_y)
            right = min(mask.width, box[2] + pad_x)
            bottom = min(mask.height, box[3] + pad_y)
            compactness = min(1.0, area / max(1, box_area))
            confidence = (
                0.98
                if source_kind == "alpha"
                else min(
                    0.97,
                    0.58 + compactness * 0.24 + min(threshold, 80) / 800,
                )
            )
            if selection_kind == "color_split":
                confidence = max(0.0, confidence - 0.05)
            elif selection_kind == "shape_split":
                confidence = max(0.0, confidence - 0.08)
            suggestions.append(
                SmartSelectionSuggestion(
                    region=(
                        left / mask.width,
                        top / mask.height,
                        right / mask.width,
                        bottom / mask.height,
                    ),
                    confidence=round(confidence, 6),
                    foreground_fraction=round(fraction, 6),
                    source=source_kind,
                    mask_size=mask.size,
                    mask_rle=mask_rle,
                    selection_kind=selection_kind,
                )
            )
            if len(suggestions) >= maximum_suggestions:
                break
        return tuple(suggestions)
    finally:
        mask.close()


__all__ = [
    "DEFAULT_PROXY_DIMENSION",
    "MAX_SMART_SELECTION_PIXELS",
    "MAX_SELECTION_MASK_PIXELS",
    "MAX_SELECTION_MASK_PAYLOAD",
    "SmartSelectionError",
    "SmartSelectionSuggestion",
    "encode_selection_mask",
    "detect_smart_selection",
    "decode_selection_mask",
    "refine_selection_mask",
]
