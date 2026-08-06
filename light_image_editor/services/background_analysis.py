"""Shared offline background analysis primitives."""

from __future__ import annotations

from collections import Counter
from typing import cast

from PIL import Image


def estimate_border_background(
    image: Image.Image,
) -> tuple[tuple[int, int, int], int]:
    """Estimate the dominant border colour and its normal variation.

    The complete outer ring is sampled instead of only the corners. Quantized
    clustering rejects a foreground object touching one side, while the spread
    describes gentle background gradients for downstream adaptive thresholds.
    """

    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    try:
        width, height = rgba.size
        depth = max(1, min(16, min(width, height) // 24))
        depth_stride = max(1, depth // 4)
        depths = set(range(0, depth, depth_stride))
        depths.add(depth - 1)
        x_stride = max(1, width // 256)
        y_stride = max(1, height // 256)
        points: set[tuple[int, int]] = set()
        for offset in sorted(depths):
            top = min(height - 1, offset)
            bottom = max(0, height - 1 - offset)
            for x in range(0, width, x_stride):
                points.add((x, top))
                points.add((x, bottom))
            points.add((width - 1, top))
            points.add((width - 1, bottom))
            left = min(width - 1, offset)
            right = max(0, width - 1 - offset)
            for y in range(0, height, y_stride):
                points.add((left, y))
                points.add((right, y))
            points.add((left, height - 1))
            points.add((right, height - 1))

        samples: list[tuple[int, int, int]] = []
        for point in sorted(points, key=lambda value: (value[1], value[0])):
            pixel = cast(tuple[int, int, int, int], rgba.getpixel(point))
            if pixel[3] >= 16:
                samples.append(pixel[:3])
        if not samples:
            return ((255, 255, 255), 0)

        buckets = Counter(tuple(channel // 16 for channel in pixel) for pixel in samples)
        family_support = {
            bucket: sum(
                count
                for candidate, count in buckets.items()
                if max(abs(candidate[channel] - bucket[channel]) for channel in range(3)) <= 4
            )
            for bucket in buckets
        }
        dominant = max(
            buckets,
            key=lambda bucket: (family_support[bucket], buckets[bucket], bucket),
        )
        clustered = [
            sample for sample in samples if tuple(channel // 16 for channel in sample) == dominant
        ]
        seed = cast(
            tuple[int, int, int],
            tuple(
                sorted(pixel[channel] for pixel in clustered)[len(clustered) // 2]
                for channel in range(3)
            ),
        )
        related = [
            pixel
            for pixel in samples
            if max(abs(pixel[channel] - seed[channel]) for channel in range(3)) <= 64
        ]
        if not related:
            related = clustered
        background = cast(
            tuple[int, int, int],
            tuple(
                sorted(pixel[channel] for pixel in related)[len(related) // 2]
                for channel in range(3)
            ),
        )
        distances = sorted(
            max(abs(pixel[channel] - background[channel]) for channel in range(3))
            for pixel in related
        )
        return (background, distances[-1])
    finally:
        if rgba is not image:
            rgba.close()


__all__ = ["estimate_border_background"]
