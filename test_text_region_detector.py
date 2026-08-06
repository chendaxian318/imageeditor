from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

from light_image_editor.providers.mock_provider import (
    _draw_replacement_text,
    _warp_surface_to_quad,
)
from light_image_editor.services.text_region_detector import (
    TextRegionSuggestion,
    detect_text_regions,
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _photo_like_background(seed: int = 20260806) -> Image.Image:
    width, height = 720, 240
    rng = np.random.default_rng(seed)
    y_grid, x_grid = np.mgrid[0:height, 0:width]
    normalized_x = x_grid / (width - 1)
    normalized_y = y_grid / (height - 1)
    background = np.stack(
        (
            145 + 35 * normalized_x - 18 * normalized_y,
            165 - 22 * normalized_x + 20 * normalized_y,
            178 - 30 * normalized_x - 10 * normalized_y,
        ),
        axis=2,
    )
    coarse_noise = (rng.random((18, 48, 3)) * 255).astype(np.uint8)
    coarse_image = Image.fromarray(coarse_noise).resize(
        (width, height),
        Image.Resampling.BICUBIC,
    )
    try:
        coarse_texture = np.asarray(coarse_image, dtype=np.float64) / 255 - 0.5
    finally:
        coarse_image.close()
    fine_texture = rng.random((height, width, 1)) - 0.5
    pixels = np.clip(
        background + 8 * (coarse_texture * 0.75 + fine_texture * 0.25),
        0,
        255,
    ).astype(np.uint8)
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image, "RGBA")
    for index in range(8):
        start_x = int(rng.integers(-50, width // 2))
        start_y = int(rng.integers(0, height))
        points = [
            (
                start_x + step * int(rng.integers(35, 75)),
                start_y + int(rng.integers(-42, 43)),
            )
            for step in range(6)
        ]
        color = (55, 92, 58, 105) if index % 2 == 0 else (105, 72, 45, 95)
        draw.line(
            points,
            fill=color,
            width=int(rng.integers(1, 5)),
            joint="curve",
        )
    return image


class TextRegionDetectorTests(unittest.TestCase):
    def test_classifies_controlled_outline_shadow_gradient_and_glow_printed_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "艺术字效果 TEXT 2026"
            expected_region = (0.05, 0.25, 0.92, 0.75)
            observed: dict[str, TextRegionSuggestion] = {}
            for visual_effect in ("plain", "outline", "shadow", "gradient", "glow"):
                path = root / f"受控字效-{visual_effect}.png"
                background = (24, 28, 38) if visual_effect == "glow" else (238, 235, 228)
                image = Image.new("RGB", (720, 240), background)
                draw = ImageDraw.Draw(image)
                if visual_effect == "plain":
                    draw.text((70, 80), text, font=_font(52), fill=(28, 32, 40))
                elif visual_effect == "outline":
                    draw.text(
                        (70, 80),
                        text,
                        font=_font(52),
                        fill=(248, 248, 248),
                        stroke_width=5,
                        stroke_fill=(25, 55, 110),
                    )
                elif visual_effect == "shadow":
                    draw.text((80, 88), text, font=_font(52), fill=(80, 70, 65))
                    draw.text((70, 80), text, font=_font(52), fill=(245, 165, 40))
                elif visual_effect == "gradient":
                    mask = Image.new("L", image.size, 0)
                    gradient = ImageOps.colorize(
                        Image.linear_gradient("L").resize(image.size),
                        black="#d02090",
                        white="#20b8dc",
                    )
                    try:
                        ImageDraw.Draw(mask).text(
                            (70, 80),
                            text,
                            font=_font(52),
                            fill=255,
                            stroke_width=1,
                            stroke_fill=255,
                        )
                        image.paste(gradient, (0, 0), mask)
                    finally:
                        mask.close()
                        gradient.close()
                else:
                    mask = Image.new("L", image.size, 0)
                    ImageDraw.Draw(mask).text(
                        (70, 80),
                        text,
                        font=_font(52),
                        fill=255,
                    )
                    blurred = mask.filter(ImageFilter.GaussianBlur(7))
                    boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
                    glow = Image.new(
                        "RGBA",
                        image.size,
                        ImageColor.getcolor("#20b8ff", "RGBA"),
                    )
                    try:
                        glow.putalpha(boosted)
                        rgba = image.convert("RGBA")
                        try:
                            rgba.alpha_composite(glow)
                            ImageDraw.Draw(rgba).text(
                                (70, 80),
                                text,
                                font=_font(52),
                                fill="#f8f8ff",
                            )
                            replacement = rgba.convert("RGB")
                            image.close()
                            image = replacement
                        finally:
                            rgba.close()
                    finally:
                        mask.close()
                        blurred.close()
                        boosted.close()
                        glow.close()
                image.save(path)
                image.close()

                suggestions = detect_text_regions(path, max_suggestions=12)
                matches = [
                    item
                    for item in suggestions
                    if abs(item.rotation) <= 1
                    and item.visual_effect == visual_effect
                    and _overlap_ratio(item.region, expected_region) >= 0.85
                ]
                self.assertTrue(matches, (visual_effect, suggestions))
                observed[visual_effect] = max(matches, key=lambda item: item.confidence)

            self.assertEqual(set(observed), {"plain", "outline", "shadow", "gradient", "glow"})
            expected_estimates = {
                "plain": ("#1c2028", None, None),
                "outline": ("#f8f8f8", "#19376e", 5),
                "shadow": ("#f5a528", "#504641", 9),
                "gradient": ("#8660b0", "#7274ba", None),
                "glow": ("#f8f8ff", "#20b8ff", 7),
            }
            for visual_effect, suggestion in observed.items():
                expected_fill, expected_secondary, expected_size = expected_estimates[visual_effect]
                self.assertLessEqual(
                    _hex_color_error(suggestion.estimated_fill_color, expected_fill),
                    3,
                )
                if expected_secondary is None:
                    self.assertIsNone(suggestion.estimated_effect_color)
                else:
                    self.assertLessEqual(
                        _hex_color_error(
                            suggestion.estimated_effect_color,
                            expected_secondary,
                        ),
                        3,
                    )
                if expected_size is None:
                    self.assertIsNone(suggestion.estimated_effect_size)
                else:
                    assert suggestion.estimated_effect_size is not None
                    self.assertLessEqual(
                        abs(suggestion.estimated_effect_size - expected_size),
                        1,
                        (visual_effect, suggestion),
                    )
            self.assertEqual(
                detect_text_regions(root / "受控字效-gradient.png", max_suggestions=12),
                detect_text_regions(root / "受控字效-gradient.png", max_suggestions=12),
            )

    def test_scales_outline_strength_back_to_original_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scaled-outline.png"
            image = Image.new("RGB", (1440, 480), (238, 235, 228))
            ImageDraw.Draw(image).text(
                (140, 160),
                "SCALE TEST 2026",
                font=_font(104),
                fill=(248, 248, 248),
                stroke_width=10,
                stroke_fill=(25, 55, 110),
            )
            image.save(path)
            image.close()

            suggestions = detect_text_regions(path, max_suggestions=12)

        matches = [item for item in suggestions if item.visual_effect == "outline"]
        self.assertTrue(matches, suggestions)
        suggestion = max(matches, key=lambda item: item.confidence)
        self.assertEqual(suggestion.estimated_fill_color, "#f8f8f8")
        self.assertEqual(suggestion.estimated_effect_color, "#19376e")
        self.assertEqual(suggestion.estimated_effect_size, 10)

    def test_models_smooth_textured_background_for_plain_and_glow_text(self) -> None:
        width, height = 720, 240
        y_grid, x_grid = np.mgrid[0:height, 0:width]
        normalized_x = x_grid / (width - 1)
        normalized_y = y_grid / (height - 1)
        texture = 7 * np.sin(x_grid / 55) + 5 * np.cos(y_grid / 31)
        background = np.empty((height, width, 3), dtype=np.float64)
        background[..., 0] = 205 + 35 * normalized_x - 28 * normalized_y + texture
        background[..., 1] = 215 - 30 * normalized_x + 18 * normalized_y + texture * 0.7
        background[..., 2] = 225 - 45 * normalized_x - 10 * normalized_y + texture * 0.4
        background = np.clip(background, 0, 255).astype(np.uint8)
        expected_region = (0.05, 0.25, 0.92, 0.75)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: dict[str, TextRegionSuggestion] = {}
            for visual_effect in ("plain", "glow"):
                path = root / f"smooth-textured-{visual_effect}.png"
                image = Image.fromarray(background)
                if visual_effect == "plain":
                    ImageDraw.Draw(image).text(
                        (70, 80),
                        "复杂背景 TEXT 2026",
                        font=_font(52),
                        fill="#202830",
                    )
                else:
                    rgba = image.convert("RGBA")
                    mask = Image.new("L", image.size, 0)
                    ImageDraw.Draw(mask).text(
                        (70, 80),
                        "复杂背景 TEXT 2026",
                        font=_font(52),
                        fill=255,
                    )
                    blurred = mask.filter(ImageFilter.GaussianBlur(7))
                    boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
                    glow = Image.new(
                        "RGBA",
                        image.size,
                        ImageColor.getcolor("#20b8ff", "RGBA"),
                    )
                    try:
                        glow.putalpha(boosted)
                        rgba.alpha_composite(glow)
                        ImageDraw.Draw(rgba).text(
                            (70, 80),
                            "复杂背景 TEXT 2026",
                            font=_font(52),
                            fill="#f8f8ff",
                        )
                        replacement = rgba.convert("RGB")
                        image.close()
                        image = replacement
                    finally:
                        rgba.close()
                        mask.close()
                        blurred.close()
                        boosted.close()
                        glow.close()
                image.save(path)
                image.close()

                suggestions = detect_text_regions(path, max_suggestions=12)
                matches = [
                    item
                    for item in suggestions
                    if abs(item.rotation) <= 1
                    and item.visual_effect == visual_effect
                    and _overlap_ratio(item.region, expected_region) >= 0.85
                ]
                self.assertTrue(matches, (visual_effect, suggestions))
                observed[visual_effect] = max(matches, key=lambda item: item.confidence)
                self.assertEqual(
                    suggestions,
                    detect_text_regions(path, max_suggestions=12),
                )

        plain = observed["plain"]
        self.assertEqual(plain.estimated_fill_color, "#202830")
        self.assertIsNone(plain.estimated_effect_color)
        self.assertIsNone(plain.estimated_effect_size)
        glow = observed["glow"]
        self.assertEqual(glow.estimated_fill_color, "#f8f8ff")
        self.assertLessEqual(_hex_color_error(glow.estimated_effect_color, "#20b8ff"), 8)
        assert glow.estimated_effect_size is not None
        self.assertLessEqual(abs(glow.estimated_effect_size - 7), 2)

    def test_suppresses_high_frequency_texture_and_structural_edges(self) -> None:
        width, height = 720, 240
        y_grid, x_grid = np.mgrid[0:height, 0:width]
        amplitude = 12
        texture = (
            amplitude * np.sin(x_grid / 4.7)
            + amplitude * 0.7 * np.cos(y_grid / 6.3)
            + amplitude * 0.45 * np.sin((x_grid + y_grid) / 9.5)
        )
        structure = np.where(x_grid < width // 2, -18, 18) + np.where(y_grid < 118, 9, -9)
        background = np.stack(
            (
                178 + 18 * x_grid / (width - 1) - 12 * y_grid / (height - 1) + texture + structure,
                196
                - 15 * x_grid / (width - 1)
                + 10 * y_grid / (height - 1)
                + texture * 0.65
                + structure * 0.45,
                208
                - 22 * x_grid / (width - 1)
                - 8 * y_grid / (height - 1)
                + texture * 0.35
                - structure * 0.25,
            ),
            axis=2,
        )
        background = np.clip(np.rint(background), 0, 255).astype(np.uint8)
        expected_region = (0.05, 0.25, 0.92, 0.75)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: dict[str, TextRegionSuggestion] = {}
            for visual_effect in ("plain", "glow"):
                path = root / f"structured-texture-{visual_effect}.png"
                image = Image.fromarray(background)
                if visual_effect == "plain":
                    ImageDraw.Draw(image).text(
                        (70, 80),
                        "结构背景 TEXT 2026",
                        font=_font(52),
                        fill="#18222c",
                    )
                else:
                    rgba = image.convert("RGBA")
                    mask = Image.new("L", image.size, 0)
                    ImageDraw.Draw(mask).text(
                        (70, 80),
                        "结构背景 TEXT 2026",
                        font=_font(52),
                        fill=255,
                    )
                    blurred = mask.filter(ImageFilter.GaussianBlur(7))
                    boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
                    glow_layer = Image.new(
                        "RGBA",
                        image.size,
                        ImageColor.getcolor("#20b8ff", "RGBA"),
                    )
                    try:
                        glow_layer.putalpha(boosted)
                        rgba.alpha_composite(glow_layer)
                        ImageDraw.Draw(rgba).text(
                            (70, 80),
                            "结构背景 TEXT 2026",
                            font=_font(52),
                            fill="#f8f8ff",
                        )
                        replacement = rgba.convert("RGB")
                        image.close()
                        image = replacement
                    finally:
                        rgba.close()
                        mask.close()
                        blurred.close()
                        boosted.close()
                        glow_layer.close()
                image.save(path)
                image.close()

                suggestions = detect_text_regions(path, max_suggestions=12)
                matches = [
                    item
                    for item in suggestions
                    if abs(item.rotation) <= 1
                    and item.visual_effect == visual_effect
                    and _overlap_ratio(item.region, expected_region) >= 0.85
                ]
                self.assertTrue(matches, (visual_effect, suggestions))
                observed[visual_effect] = max(matches, key=lambda item: item.confidence)
                self.assertEqual(suggestions, detect_text_regions(path, max_suggestions=12))

        plain = observed["plain"]
        self.assertEqual(plain.estimated_fill_color, "#18222c")
        self.assertIsNone(plain.estimated_effect_color)
        self.assertIsNone(plain.estimated_effect_size)
        glow = observed["glow"]
        self.assertEqual(glow.estimated_fill_color, "#f8f8ff")
        self.assertLessEqual(_hex_color_error(glow.estimated_effect_color, "#20b8ff"), 12)
        assert glow.estimated_effect_size is not None
        self.assertLessEqual(abs(glow.estimated_effect_size - 7), 2)

    def test_localizes_text_among_photo_like_texture_and_dense_edges(self) -> None:
        expected_region = (0.05, 0.25, 0.92, 0.75)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            background = _photo_like_background()
            negative_path = root / "photo-like-no-text.png"
            background.save(negative_path)
            self.assertEqual(detect_text_regions(negative_path, max_suggestions=12), ())

            observed: dict[str, TextRegionSuggestion] = {}
            for visual_effect in ("plain", "glow"):
                path = root / f"photo-like-{visual_effect}.png"
                image = background.copy()
                if visual_effect == "plain":
                    ImageDraw.Draw(image).text(
                        (70, 80),
                        "PHOTO TEXT 2026",
                        font=_font(52),
                        fill="#111820",
                    )
                else:
                    rgba = image.convert("RGBA")
                    mask = Image.new("L", image.size, 0)
                    ImageDraw.Draw(mask).text(
                        (70, 80),
                        "PHOTO TEXT 2026",
                        font=_font(52),
                        fill=255,
                    )
                    blurred = mask.filter(ImageFilter.GaussianBlur(7))
                    boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
                    glow_layer = Image.new(
                        "RGBA",
                        image.size,
                        ImageColor.getcolor("#20b8ff", "RGBA"),
                    )
                    try:
                        glow_layer.putalpha(boosted)
                        rgba.alpha_composite(glow_layer)
                        ImageDraw.Draw(rgba).text(
                            (70, 80),
                            "PHOTO TEXT 2026",
                            font=_font(52),
                            fill="#f8f8ff",
                        )
                        replacement = rgba.convert("RGB")
                        image.close()
                        image = replacement
                    finally:
                        rgba.close()
                        mask.close()
                        blurred.close()
                        boosted.close()
                        glow_layer.close()
                image.save(path)
                image.close()

                suggestions = detect_text_regions(path, max_suggestions=12)
                matches = [
                    item
                    for item in suggestions
                    if abs(item.rotation) <= 1
                    and item.visual_effect == visual_effect
                    and _overlap_ratio(item.region, expected_region) >= 0.85
                ]
                self.assertTrue(matches, (visual_effect, suggestions))
                observed[visual_effect] = max(matches, key=lambda item: item.confidence)
                self.assertEqual(suggestions, detect_text_regions(path, max_suggestions=12))

            background.close()

        plain = observed["plain"]
        self.assertLessEqual(_hex_color_error(plain.estimated_fill_color, "#111820"), 5)
        self.assertIsNone(plain.estimated_effect_color)
        self.assertIsNone(plain.estimated_effect_size)
        glow = observed["glow"]
        self.assertEqual(glow.estimated_fill_color, "#f8f8ff")
        self.assertLessEqual(_hex_color_error(glow.estimated_effect_color, "#20b8ff"), 12)
        assert glow.estimated_effect_size is not None
        self.assertLessEqual(abs(glow.estimated_effect_size - 7), 2)
        for suggestion in observed.values():
            self.assertLessEqual(suggestion.region[2] - suggestion.region[0], 0.75)
            self.assertLessEqual(suggestion.region[3] - suggestion.region[1], 0.30)

    def test_ranks_plain_text_first_and_rejects_photo_like_edge_only_matrix(self) -> None:
        expected_region = (0.05, 0.25, 0.92, 0.75)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for seed in range(20260806, 20260814):
                with self.subTest(seed=seed):
                    background = _photo_like_background(seed)
                    negative_path = root / f"photo-like-negative-{seed}.png"
                    background.save(negative_path)
                    self.assertEqual(
                        detect_text_regions(negative_path, max_suggestions=5),
                        (),
                    )

                    text_path = root / f"photo-like-plain-{seed}.png"
                    ImageDraw.Draw(background).text(
                        (70, 80),
                        "PHOTO TEXT 2026",
                        font=_font(52),
                        fill="#111820",
                    )
                    background.save(text_path)
                    background.close()

                    suggestions = detect_text_regions(text_path, max_suggestions=5)
                    self.assertTrue(suggestions)
                    best = suggestions[0]
                    self.assertLessEqual(abs(best.rotation), 1)
                    self.assertEqual(best.visual_effect, "plain")
                    self.assertGreaterEqual(
                        _overlap_ratio(best.region, expected_region),
                        0.85,
                    )
                    self.assertLessEqual(
                        _hex_color_error(best.estimated_fill_color, "#111820"),
                        5,
                    )

                    glow_path = root / f"photo-like-glow-{seed}.png"
                    glow_source = _photo_like_background(seed)
                    glow_mask = Image.new("L", glow_source.size, 0)
                    ImageDraw.Draw(glow_mask).text(
                        (70, 80),
                        "PHOTO TEXT 2026",
                        font=_font(52),
                        fill=255,
                    )
                    blurred = glow_mask.filter(ImageFilter.GaussianBlur(7))
                    boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
                    glow_layer = Image.new(
                        "RGBA",
                        glow_source.size,
                        ImageColor.getcolor("#20b8ff", "RGBA"),
                    )
                    rgba = glow_source.convert("RGBA")
                    try:
                        glow_layer.putalpha(boosted)
                        rgba.alpha_composite(glow_layer)
                        ImageDraw.Draw(rgba).text(
                            (70, 80),
                            "PHOTO TEXT 2026",
                            font=_font(52),
                            fill="#f8f8ff",
                        )
                        rendered = rgba.convert("RGB")
                        try:
                            rendered.save(glow_path)
                        finally:
                            rendered.close()
                    finally:
                        glow_source.close()
                        glow_mask.close()
                        blurred.close()
                        boosted.close()
                        glow_layer.close()
                        rgba.close()

                    glow_suggestions = detect_text_regions(
                        glow_path,
                        max_suggestions=5,
                    )
                    self.assertTrue(glow_suggestions)
                    glow = glow_suggestions[0]
                    self.assertLessEqual(abs(glow.rotation), 1)
                    self.assertEqual(glow.visual_effect, "glow")
                    self.assertGreaterEqual(
                        _overlap_ratio(glow.region, expected_region),
                        0.85,
                    )
                    self.assertEqual(glow.estimated_fill_color, "#f8f8ff")
                    self.assertLessEqual(
                        _hex_color_error(glow.estimated_effect_color, "#20b8ff"),
                        12,
                    )
                    assert glow.estimated_effect_size is not None
                    self.assertLessEqual(abs(glow.estimated_effect_size - 7), 4)

    def test_handles_photo_like_layout_scale_multiline_and_partial_occlusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layouts = (
                ("small", 20260806, (80, 92), 34, (0.06, 0.28, 0.85, 0.75)),
                ("top", 20260808, (60, 24), 42, (0.03, 0.01, 0.88, 0.48)),
                ("bottom", 20260811, (60, 158), 42, (0.03, 0.52, 0.88, 0.99)),
            )
            for name, seed, position, font_size, expected_region in layouts:
                with self.subTest(layout=name):
                    path = root / f"photo-layout-{name}.png"
                    image = _photo_like_background(seed)
                    ImageDraw.Draw(image).text(
                        position,
                        "SMALL PHOTO 2026",
                        font=_font(font_size),
                        fill="#111820",
                    )
                    image.save(path)
                    image.close()

                    suggestions = detect_text_regions(path, max_suggestions=8)
                    self.assertTrue(suggestions)
                    best = suggestions[0]
                    self.assertLessEqual(abs(best.rotation), 1)
                    self.assertEqual(best.visual_effect, "plain")
                    self.assertGreaterEqual(_overlap_ratio(best.region, expected_region), 0.85)
                    self.assertLessEqual(
                        _hex_color_error(best.estimated_fill_color, "#111820"),
                        5,
                    )

            occluded_path = root / "photo-layout-occluded.png"
            occluded = _photo_like_background(20260813)
            draw = ImageDraw.Draw(occluded)
            draw.text(
                (70, 80),
                "OCCLUDED PHOTO 2026",
                font=_font(52),
                fill="#111820",
            )
            for left in (155, 255, 355, 455, 555):
                draw.ellipse((left, 96, left + 24, 117), fill=(75, 118, 70))
            draw.line((125, 112, 620, 99), fill=(110, 72, 48), width=4)
            occluded.save(occluded_path)
            occluded.close()
            occluded_suggestions = detect_text_regions(occluded_path, max_suggestions=8)
            self.assertTrue(occluded_suggestions)
            occluded_best = occluded_suggestions[0]
            self.assertEqual(occluded_best.visual_effect, "plain")
            self.assertGreaterEqual(
                _overlap_ratio(occluded_best.region, (0.04, 0.24, 0.92, 0.78)),
                0.85,
            )
            self.assertLessEqual(
                _hex_color_error(occluded_best.estimated_fill_color, "#111820"),
                5,
            )

            multiline_path = root / "photo-layout-multiline.png"
            multiline = _photo_like_background(20260810)
            draw = ImageDraw.Draw(multiline)
            draw.text((70, 36), "FIRST PHOTO LINE", font=_font(34), fill="#111820")
            draw.text((70, 135), "SECOND PHOTO LINE", font=_font(34), fill="#111820")
            multiline.save(multiline_path)
            multiline.close()
            multiline_suggestions = detect_text_regions(multiline_path, max_suggestions=8)
            expected_lines = ((0.04, 0.08, 0.75, 0.45), (0.04, 0.48, 0.75, 0.82))
            matched = [
                next(
                    (
                        item
                        for item in multiline_suggestions
                        if abs(item.rotation) <= 1
                        and item.visual_effect == "plain"
                        and _overlap_ratio(item.region, expected) >= 0.85
                        and _hex_color_error(item.estimated_fill_color, "#111820") <= 5
                    ),
                    None,
                )
                for expected in expected_lines
            ]
            self.assertTrue(all(item is not None for item in matched), multiline_suggestions)
            self.assertLess(matched[0].region[1], matched[1].region[1])  # type: ignore[union-attr]

    def test_handles_jpeg_compressed_photo_text_and_rejects_compressed_negatives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fill_error_limits = {95: 8, 75: 12, 50: 16}
            for quality in (95, 75, 50):
                for seed in (20260806, 20260808, 20260811, 20260813):
                    with self.subTest(quality=quality, seed=seed, case="negative"):
                        negative_path = root / f"jpeg-negative-{quality}-{seed}.jpg"
                        negative = _photo_like_background(seed)
                        negative.save(
                            negative_path,
                            "JPEG",
                            quality=quality,
                            subsampling=2,
                        )
                        negative.close()
                        self.assertEqual(
                            detect_text_regions(negative_path, max_suggestions=8),
                            (),
                        )

                for name, seed, position, font_size, expected_region in (
                    ("plain", 20260806, (70, 80), 52, (0.05, 0.25, 0.92, 0.75)),
                    ("small", 20260806, (80, 92), 34, (0.06, 0.28, 0.85, 0.75)),
                ):
                    with self.subTest(quality=quality, case=name):
                        path = root / f"jpeg-{name}-{quality}.jpg"
                        image = _photo_like_background(seed)
                        ImageDraw.Draw(image).text(
                            position,
                            "PHOTO TEXT 2026",
                            font=_font(font_size),
                            fill="#111820",
                        )
                        image.save(path, "JPEG", quality=quality, subsampling=2)
                        image.close()
                        suggestions = detect_text_regions(path, max_suggestions=8)
                        self.assertTrue(suggestions)
                        best = suggestions[0]
                        self.assertEqual(best.visual_effect, "plain")
                        self.assertLessEqual(abs(best.rotation), 1)
                        self.assertGreaterEqual(
                            _overlap_ratio(best.region, expected_region),
                            0.85,
                        )
                        self.assertLessEqual(
                            _hex_color_error(best.estimated_fill_color, "#111820"),
                            fill_error_limits[quality],
                        )

                glow_path = root / f"jpeg-glow-{quality}.jpg"
                glow_source = _photo_like_background(20260806)
                glow_mask = Image.new("L", glow_source.size, 0)
                ImageDraw.Draw(glow_mask).text(
                    (70, 80),
                    "PHOTO TEXT 2026",
                    font=_font(52),
                    fill=255,
                )
                blurred = glow_mask.filter(ImageFilter.GaussianBlur(7))
                boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
                glow_layer = Image.new(
                    "RGBA",
                    glow_source.size,
                    ImageColor.getcolor("#20b8ff", "RGBA"),
                )
                rgba = glow_source.convert("RGBA")
                try:
                    glow_layer.putalpha(boosted)
                    rgba.alpha_composite(glow_layer)
                    ImageDraw.Draw(rgba).text(
                        (70, 80),
                        "PHOTO TEXT 2026",
                        font=_font(52),
                        fill="#f8f8ff",
                    )
                    rendered = rgba.convert("RGB")
                    try:
                        rendered.save(
                            glow_path,
                            "JPEG",
                            quality=quality,
                            subsampling=2,
                        )
                    finally:
                        rendered.close()
                finally:
                    glow_source.close()
                    glow_mask.close()
                    blurred.close()
                    boosted.close()
                    glow_layer.close()
                    rgba.close()
                glow_suggestions = detect_text_regions(glow_path, max_suggestions=8)
                self.assertTrue(glow_suggestions)
                glow = glow_suggestions[0]
                self.assertEqual(glow.visual_effect, "glow")
                self.assertGreaterEqual(
                    _overlap_ratio(glow.region, (0.05, 0.25, 0.92, 0.75)),
                    0.85,
                )
                self.assertLessEqual(
                    _hex_color_error(glow.estimated_fill_color, "#f8f8ff"),
                    10,
                )
                self.assertLessEqual(
                    _hex_color_error(glow.estimated_effect_color, "#20b8ff"),
                    8,
                )
                self.assertIsNotNone(glow.estimated_effect_size)
                self.assertLessEqual(abs((glow.estimated_effect_size or 0) - 7), 1)

                multiline_path = root / f"jpeg-multiline-{quality}.jpg"
                multiline = _photo_like_background(20260810)
                draw = ImageDraw.Draw(multiline)
                draw.text((70, 36), "FIRST PHOTO LINE", font=_font(34), fill="#111820")
                draw.text((70, 135), "SECOND PHOTO LINE", font=_font(34), fill="#111820")
                multiline.save(
                    multiline_path,
                    "JPEG",
                    quality=quality,
                    subsampling=2,
                )
                multiline.close()
                multiline_suggestions = detect_text_regions(
                    multiline_path,
                    max_suggestions=8,
                )
                self.assertGreaterEqual(len(multiline_suggestions), 2)
                self.assertLess(
                    multiline_suggestions[0].region[1],
                    multiline_suggestions[1].region[1],
                )
                for expected_region in (
                    (0.04, 0.08, 0.75, 0.45),
                    (0.04, 0.48, 0.75, 0.82),
                ):
                    self.assertTrue(
                        any(
                            item.visual_effect == "plain"
                            and _overlap_ratio(item.region, expected_region) >= 0.85
                            and _hex_color_error(item.estimated_fill_color, "#111820")
                            <= fill_error_limits[quality]
                            for item in multiline_suggestions
                        ),
                        multiline_suggestions,
                    )

                occluded_path = root / f"jpeg-occluded-{quality}.jpg"
                occluded = _photo_like_background(20260813)
                draw = ImageDraw.Draw(occluded)
                draw.text(
                    (70, 80),
                    "OCCLUDED PHOTO 2026",
                    font=_font(52),
                    fill="#111820",
                )
                for left in (155, 255, 355, 455, 555):
                    draw.ellipse((left, 96, left + 24, 117), fill=(75, 118, 70))
                draw.line((125, 112, 620, 99), fill=(110, 72, 48), width=4)
                occluded.save(
                    occluded_path,
                    "JPEG",
                    quality=quality,
                    subsampling=2,
                )
                occluded.close()
                occluded_suggestions = detect_text_regions(
                    occluded_path,
                    max_suggestions=8,
                )
                self.assertTrue(occluded_suggestions)
                occluded_best = occluded_suggestions[0]
                self.assertEqual(occluded_best.visual_effect, "plain")
                self.assertGreaterEqual(
                    _overlap_ratio(
                        occluded_best.region,
                        (0.04, 0.24, 0.92, 0.78),
                    ),
                    0.85,
                )
                self.assertLessEqual(
                    _hex_color_error(occluded_best.estimated_fill_color, "#111820"),
                    fill_error_limits[quality],
                )

    def test_detects_controlled_curved_printed_text_and_estimates_direction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected_region = (0.075, 0.16, 0.925, 0.84)
            observed: dict[float, TextRegionSuggestion] = {}
            for curve in (0.0, 0.25, 0.45, 0.75, -0.45, -0.75):
                path = root / f"弧形文字-{curve:+.2f}.png"
                image = Image.new("RGBA", (800, 360), "white")
                layer = Image.new("RGBA", (680, 240), "white")
                _draw_replacement_text(
                    layer,
                    text="弧形文字弧形文字",
                    font_family="微软雅黑",
                    font_size=72,
                    color="#202020",
                    align="center",
                    curve=curve,
                )
                image.alpha_composite(layer, (60, 60))
                rendered = image.convert("RGB")
                rendered.save(path)
                rendered.close()
                layer.close()
                image.close()

                suggestions = detect_text_regions(path, max_suggestions=20)
                horizontal = [
                    item
                    for item in suggestions
                    if abs(item.rotation) <= 1
                    and _overlap_ratio(item.region, expected_region) >= 0.85
                ]
                self.assertTrue(horizontal, (curve, suggestions))
                observed[curve] = max(horizontal, key=lambda item: item.confidence)

            self.assertAlmostEqual(observed[0.0].curve, 0.0)
            for expected_curve in (0.25, 0.45, 0.75, -0.45, -0.75):
                self.assertAlmostEqual(
                    observed[expected_curve].curve,
                    expected_curve,
                    delta=0.11,
                )
                self.assertIsNone(observed[expected_curve].perspective_quad)
            self.assertEqual(
                detect_text_regions(root / "弧形文字-+0.75.png", max_suggestions=20),
                detect_text_regions(root / "弧形文字-+0.75.png", max_suggestions=20),
            )

    def test_keeps_two_spatially_separated_perspective_planes_in_reading_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "双平面文字.png"
            size = (1_000, 700)
            image = Image.new("RGBA", size, "white")
            fixtures = (
                (
                    "平面一 PERSPECTIVE 2026",
                    (50.0, 60.0, 460.0, 25.0, 485.0, 300.0, 25.0, 205.0),
                ),
                (
                    "平面二 PERSPECTIVE 2026",
                    (520.0, 380.0, 930.0, 435.0, 965.0, 550.0, 495.0, 600.0),
                ),
            )
            for text, quad in fixtures:
                layer = Image.new("RGBA", (440, 100), (0, 0, 0, 0))
                ImageDraw.Draw(layer).text(
                    (8, 10),
                    text,
                    font=_font(38),
                    fill="#202020",
                )
                warped = _warp_surface_to_quad(
                    layer,
                    output_size=size,
                    destination=quad,
                )
                image.alpha_composite(warped)
                layer.close()
                warped.close()
            rendered = image.convert("RGB")
            rendered.save(path)
            rendered.close()
            image.close()

            first_run = detect_text_regions(path, max_suggestions=20)
            second_run = detect_text_regions(path, max_suggestions=20)

        self.assertEqual(first_run, second_run)
        planes = [item for item in first_run if item.perspective_quad is not None]
        self.assertGreaterEqual(len(planes), 2, first_run)
        first, second = planes[:2]
        self.assertLess(first.region[1], second.region[1])
        self.assertGreater(_overlap_ratio(first.region, (0.02, 0.06, 0.50, 0.28)), 0.85)
        self.assertGreater(_overlap_ratio(second.region, (0.49, 0.55, 0.98, 0.77)), 0.85)
        first_quad = first.perspective_quad
        second_quad = second.perspective_quad
        assert first_quad is not None and second_quad is not None
        first_scale_direction = (first_quad[5] - first_quad[3]) - (first_quad[7] - first_quad[1])
        second_scale_direction = (second_quad[5] - second_quad[3]) - (
            second_quad[7] - second_quad[1]
        )
        self.assertGreater(first_scale_direction, 0.02)
        self.assertLess(second_scale_direction, -0.02)

    def test_separates_partially_occluded_perspective_planes_by_glyph_color(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "局部遮挡双色双平面.png"
            size = (1_000, 700)
            image = Image.new("RGBA", size, "white")
            fixtures = (
                (
                    "平面一 PERSPECTIVE 2026",
                    (60.0, 70.0, 660.0, 40.0, 690.0, 300.0, 40.0, 250.0),
                    "#16324f",
                ),
                (
                    "平面二 PERSPECTIVE 2026",
                    (260.0, 80.0, 900.0, 160.0, 930.0, 290.0, 220.0, 400.0),
                    "#8c3a1e",
                ),
            )
            expected_regions: list[tuple[float, float, float, float]] = []
            ink_masks: list[np.ndarray] = []
            for text, quad, color in fixtures:
                layer = Image.new("RGBA", (520, 120), (0, 0, 0, 0))
                ImageDraw.Draw(layer).text(
                    (10, 12),
                    text,
                    font=_font(46),
                    fill=color,
                )
                warped = _warp_surface_to_quad(
                    layer,
                    output_size=size,
                    destination=quad,
                )
                alpha = np.asarray(warped.getchannel("A"), dtype=np.uint8)
                ink_mask = alpha > 32
                y_indexes, x_indexes = np.nonzero(ink_mask)
                expected_regions.append(
                    (
                        float(np.min(x_indexes)) / size[0],
                        float(np.min(y_indexes)) / size[1],
                        (float(np.max(x_indexes)) + 1) / size[0],
                        (float(np.max(y_indexes)) + 1) / size[1],
                    )
                )
                ink_masks.append(ink_mask.copy())
                image.alpha_composite(warped)
                layer.close()
                warped.close()
            occluded_pixels = int(np.count_nonzero(ink_masks[0] & ink_masks[1]))
            occlusion_ratio = occluded_pixels / min(
                int(np.count_nonzero(mask)) for mask in ink_masks
            )
            self.assertGreaterEqual(occlusion_ratio, 0.04)
            self.assertGreaterEqual(
                _intersection_over_union(expected_regions[0], expected_regions[1]),
                0.10,
            )
            rendered = image.convert("RGB")
            rendered.save(path)
            rendered.close()
            image.close()

            first_run = detect_text_regions(path, max_suggestions=20)
            second_run = detect_text_regions(path, max_suggestions=20)

        self.assertEqual(first_run, second_run)
        planes = [item for item in first_run if item.perspective_quad is not None]
        matched = [
            max(
                planes,
                key=lambda item: _intersection_over_union(item.region, expected),
                default=None,
            )
            for expected in expected_regions
        ]
        self.assertTrue(all(item is not None for item in matched), first_run)
        first, second = matched
        assert first is not None and second is not None
        self.assertIsNot(first, second)
        self.assertLess(planes.index(first), planes.index(second))
        for suggestion, expected, expected_color in zip(
            matched,
            expected_regions,
            ("#16324f", "#8c3a1e"),
        ):
            assert suggestion is not None
            self.assertGreaterEqual(_overlap_ratio(expected, suggestion.region), 0.85)
            self.assertGreaterEqual(_overlap_ratio(suggestion.region, expected), 0.70)
            self.assertLessEqual(
                _hex_color_error(suggestion.estimated_fill_color, expected_color),
                4,
            )
        first_quad = first.perspective_quad
        second_quad = second.perspective_quad
        assert first_quad is not None and second_quad is not None
        self.assertGreater(
            (first_quad[5] - first_quad[3]) - (first_quad[7] - first_quad[1]),
            0.02,
        )
        self.assertLess(
            (second_quad[5] - second_quad[3]) - (second_quad[7] - second_quad[1]),
            -0.02,
        )

    def test_separates_low_color_distance_occluded_perspective_planes(self) -> None:
        colors = ("#283c50", "#403048")
        rgb_colors = np.asarray([ImageColor.getrgb(color) for color in colors], dtype=np.float64)
        self.assertEqual(int(np.max(np.abs(rgb_colors[0] - rgb_colors[1]))), 24)
        color_axes = rgb_colors - 255.0
        axis_similarity = float(
            np.dot(color_axes[0], color_axes[1])
            / (np.linalg.norm(color_axes[0]) * np.linalg.norm(color_axes[1]))
        )
        self.assertGreaterEqual(axis_similarity, 0.996)
        self.assertLess(axis_similarity, 0.998)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "低色差局部遮挡双平面.png"
            expected_regions, occlusion_ratio = _render_occluded_plane_fixture(
                path,
                colors=colors,
            )
            first_run = detect_text_regions(path, max_suggestions=20)
            second_run = detect_text_regions(path, max_suggestions=20)

        self.assertGreaterEqual(occlusion_ratio, 0.04)
        self.assertEqual(first_run, second_run)
        planes = [item for item in first_run if item.perspective_quad is not None]
        matched = [
            max(
                planes,
                key=lambda item: _intersection_over_union(item.region, expected),
                default=None,
            )
            for expected in expected_regions
        ]
        self.assertTrue(all(item is not None for item in matched), first_run)
        first, second = matched
        assert first is not None and second is not None
        self.assertIsNot(first, second)
        self.assertLess(planes.index(first), planes.index(second))
        for suggestion, expected, expected_color in zip(matched, expected_regions, colors):
            assert suggestion is not None
            self.assertGreaterEqual(_overlap_ratio(expected, suggestion.region), 0.85)
            self.assertGreaterEqual(_overlap_ratio(suggestion.region, expected), 0.65)
            self.assertLessEqual(
                _hex_color_error(suggestion.estimated_fill_color, expected_color),
                4,
            )
        first_quad = first.perspective_quad
        second_quad = second.perspective_quad
        assert first_quad is not None and second_quad is not None
        self.assertGreater(
            (first_quad[5] - first_quad[3]) - (first_quad[7] - first_quad[1]),
            0.015,
        )
        self.assertLess(
            (second_quad[5] - second_quad[3]) - (second_quad[7] - second_quad[1]),
            -0.015,
        )

    def test_separates_three_partially_overlapping_perspective_planes_by_color(self) -> None:
        colors = ("#16324f", "#2f6b3d", "#8c3a1e")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "三层局部遮挡透视文字.png"
            expected_regions, ink_overlaps = _render_three_plane_fixture(path, colors=colors)
            first_run = detect_text_regions(path, max_suggestions=20)
            second_run = detect_text_regions(path, max_suggestions=20)

        self.assertEqual(first_run, second_run)
        self.assertGreaterEqual(min(ink_overlaps), 0.01)
        self.assertGreaterEqual(max(ink_overlaps), 0.15)
        planes = [item for item in first_run if item.perspective_quad is not None]
        self.assertEqual(len(planes), 3, first_run)
        matched = [
            max(planes, key=lambda item: _intersection_over_union(item.region, expected))
            for expected in expected_regions
        ]
        self.assertEqual(len({id(item) for item in matched}), 3)
        self.assertEqual([planes.index(item) for item in matched], [0, 1, 2])
        for suggestion, expected, color in zip(matched, expected_regions, colors):
            self.assertGreaterEqual(_overlap_ratio(expected, suggestion.region), 0.85)
            self.assertGreaterEqual(_overlap_ratio(suggestion.region, expected), 0.65)
            self.assertLessEqual(_hex_color_error(suggestion.estimated_fill_color, color), 4)
        scale_directions = []
        for suggestion in matched:
            quad = suggestion.perspective_quad
            assert quad is not None
            scale_directions.append((quad[5] - quad[3]) - (quad[7] - quad[1]))
        self.assertGreater(max(scale_directions), 0.02)
        self.assertLess(min(scale_directions), -0.02)

    def test_does_not_guess_same_color_occluded_perspective_planes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "同色局部遮挡双平面.png"
            _render_occluded_plane_fixture(path, colors=("#283c50", "#283c50"))
            first_run = detect_text_regions(path, max_suggestions=20)
            second_run = detect_text_regions(path, max_suggestions=20)

        self.assertEqual(first_run, second_run)
        planes = [item for item in first_run if item.perspective_quad is not None]
        self.assertLess(len(planes), 2, first_run)

    def test_detects_multiple_horizontal_text_regions_in_reading_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "poster.png"
            image = Image.new("RGB", (480, 240), "#f7f5f0")
            draw = ImageDraw.Draw(image)
            draw.text((44, 42), "第一行标题 ABC", font=_font(36), fill="#202020")
            draw.text((80, 146), "第二行说明 2026", font=_font(28), fill="#303030")
            image.save(path)
            image.close()

            suggestions = detect_text_regions(path)

            self.assertGreaterEqual(len(suggestions), 2)
            boxes = [item.region for item in suggestions]
            self.assertTrue(any(top < 0.30 and bottom > 0.16 for _, top, _, bottom in boxes))
            self.assertTrue(any(top < 0.75 and bottom > 0.58 for _, top, _, bottom in boxes))
            self.assertTrue(all(0 <= item.confidence <= 1 for item in suggestions))
            horizontal = [item for item in suggestions if abs(item.rotation) <= 1]
            self.assertGreaterEqual(len(horizontal), 2)
            self.assertTrue(all(item.perspective_quad is None for item in horizontal[:2]))

    def test_detects_controlled_keystone_perspective_as_four_corners(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perspective-poster.png"
            image = Image.new("RGBA", (620, 320), "white")
            text_layer = Image.new("RGBA", (440, 100), (0, 0, 0, 0))
            draw = ImageDraw.Draw(text_layer)
            draw.text(
                (8, 10),
                "透视标题 PERSPECTIVE 2026",
                font=_font(48),
                fill="#202020",
            )
            fixture_quad = (90.0, 80.0, 510.0, 45.0, 535.0, 250.0, 70.0, 185.0)
            warped = _warp_surface_to_quad(
                text_layer,
                output_size=image.size,
                destination=fixture_quad,
            )
            image.alpha_composite(warped)
            image.convert("RGB").save(path)
            image.close()
            text_layer.close()
            warped.close()

            suggestions = detect_text_regions(path, max_suggestions=12)

            perspective = [
                item
                for item in suggestions
                if item.perspective_quad is not None and abs(item.rotation) <= 3
            ]
            self.assertTrue(perspective, suggestions)
            quad = perspective[0].perspective_quad
            assert quad is not None
            left_height = quad[7] - quad[1]
            right_height = quad[5] - quad[3]
            self.assertGreater(abs(left_height - right_height), 0.06)
            self.assertLess(quad[3], quad[1])
            self.assertGreater(quad[5], quad[7])

    def test_detects_rotated_printed_text_and_reports_visual_angle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rotated-poster.png"
            image = Image.new("RGB", (560, 360), "#f7f5f0")
            text_layer = Image.new("RGBA", (360, 90), (0, 0, 0, 0))
            draw = ImageDraw.Draw(text_layer)
            draw.text(
                (12, 12),
                "旋转标题 ROTATED 2026",
                font=_font(38),
                fill="#202020",
            )
            rotated = text_layer.rotate(
                30,
                resample=Image.Resampling.BICUBIC,
                expand=True,
            )
            paste_left, paste_top = 100, 70
            image.paste(rotated, (paste_left, paste_top), rotated)
            expected = (
                paste_left / image.width,
                paste_top / image.height,
                (paste_left + rotated.width) / image.width,
                (paste_top + rotated.height) / image.height,
            )
            image.save(path)
            image.close()
            text_layer.close()
            rotated.close()

            suggestions = detect_text_regions(path, max_suggestions=8)

            rotated_suggestions = [
                item
                for item in suggestions
                if abs(item.rotation - 30) <= 1 and _overlap_ratio(item.region, expected) >= 0.45
            ]
            self.assertTrue(rotated_suggestions, suggestions)

    def test_detects_near_vertical_text_and_keeps_both_exact_directions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for angle in (80, 90):
                path = root / f"竖排标题-{angle}.png"
                image = Image.new("RGB", (500, 500), "#f7f5f0")
                text_layer = Image.new("RGBA", (340, 90), (0, 0, 0, 0))
                draw = ImageDraw.Draw(text_layer)
                draw.text(
                    (12, 12),
                    "近竖排标题 VERTICAL",
                    font=_font(38),
                    fill="#202020",
                )
                rotated = text_layer.rotate(
                    angle,
                    resample=Image.Resampling.BICUBIC,
                    expand=True,
                )
                paste_left, paste_top = 100, 50
                image.paste(rotated, (paste_left, paste_top), rotated)
                expected = (
                    paste_left / image.width,
                    paste_top / image.height,
                    (paste_left + rotated.width) / image.width,
                    (paste_top + rotated.height) / image.height,
                )
                image.save(path)
                image.close()
                text_layer.close()
                rotated.close()

                suggestions = detect_text_regions(path, max_suggestions=12)
                if angle == 80:
                    self.assertTrue(
                        any(
                            abs(item.rotation - 80) <= 1
                            and _overlap_ratio(item.region, expected) >= 0.45
                            for item in suggestions
                        ),
                        suggestions,
                    )
                else:
                    directions = {
                        round(item.rotation)
                        for item in suggestions
                        if abs(abs(item.rotation) - 90) <= 1
                        and _overlap_ratio(item.region, expected) >= 0.45
                    }
                    self.assertEqual(directions, {-90, 90})

    def test_refines_non_coarse_angles_across_the_full_rotation_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for angle in (17, 58, -83):
                path = root / f"refined-angle-{angle}.png"
                image = Image.new("RGB", (560, 520), "#f7f5f0")
                text_layer = Image.new("RGBA", (360, 90), (0, 0, 0, 0))
                draw = ImageDraw.Draw(text_layer)
                draw.text(
                    (12, 12),
                    "任意角度标题 REFINED 2026",
                    font=_font(36),
                    fill="#202020",
                )
                rotated = text_layer.rotate(
                    angle,
                    resample=Image.Resampling.BICUBIC,
                    expand=True,
                )
                paste_left = (image.width - rotated.width) // 2
                paste_top = (image.height - rotated.height) // 2
                image.paste(rotated, (paste_left, paste_top), rotated)
                expected = (
                    paste_left / image.width,
                    paste_top / image.height,
                    (paste_left + rotated.width) / image.width,
                    (paste_top + rotated.height) / image.height,
                )
                image.save(path)
                image.close()
                text_layer.close()
                rotated.close()

                suggestions = detect_text_regions(path, max_suggestions=12)

                self.assertTrue(
                    any(
                        abs(item.rotation - angle) <= 1
                        and _overlap_ratio(item.region, expected) >= 0.45
                        for item in suggestions
                    ),
                    (angle, suggestions),
                )

    def test_flat_image_returns_no_suggestions_and_contract_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat.png"
            Image.new("RGB", (120, 80), "white").save(path)
            self.assertEqual(detect_text_regions(path), ())
        with self.assertRaises(ValueError):
            detect_text_regions("missing.png", max_suggestions=0)
        with self.assertRaises(ValueError):
            TextRegionSuggestion((0.5, 0.2, 0.4, 0.3), 0.5)
        with self.assertRaises(ValueError):
            TextRegionSuggestion((0.1, 0.2, 0.4, 0.3), 0.5, float("nan"))
        with self.assertRaises(ValueError):
            TextRegionSuggestion((0.1, 0.2, 0.4, 0.3), 0.5, 91)
        with self.assertRaises(ValueError):
            TextRegionSuggestion((0.1, 0.2, 0.4, 0.3), 0.5, curve=True)
        with self.assertRaises(ValueError):
            TextRegionSuggestion((0.1, 0.2, 0.4, 0.3), 0.5, curve=float("nan"))
        with self.assertRaises(ValueError):
            TextRegionSuggestion((0.1, 0.2, 0.4, 0.3), 0.5, curve=1.01)
        with self.assertRaises(ValueError):
            TextRegionSuggestion(
                (0.1, 0.2, 0.4, 0.3),
                0.5,
                visual_effect="emboss",  # type: ignore[arg-type]
            )
        for invalid_color in ("white", "#fff", "#12345g", 7):
            with self.subTest(invalid_color=invalid_color), self.assertRaises(ValueError):
                TextRegionSuggestion(
                    (0.1, 0.2, 0.4, 0.3),
                    0.5,
                    estimated_fill_color=invalid_color,  # type: ignore[arg-type]
                )
        for invalid_size in (0, 65, True, 2.5):
            with self.subTest(invalid_size=invalid_size), self.assertRaises(ValueError):
                TextRegionSuggestion(
                    (0.1, 0.2, 0.4, 0.3),
                    0.5,
                    estimated_effect_size=invalid_size,  # type: ignore[arg-type]
                )
        legacy_quad = (0.1, 0.2, 0.4, 0.2, 0.4, 0.3, 0.1, 0.3)
        legacy_candidate = TextRegionSuggestion(
            (0.1, 0.2, 0.4, 0.3),
            0.5,
            0,
            legacy_quad,
        )
        self.assertEqual(legacy_candidate.perspective_quad, legacy_quad)
        self.assertEqual(legacy_candidate.curve, 0)
        with self.assertRaises(ValueError):
            TextRegionSuggestion(
                (0.1, 0.2, 0.4, 0.3),
                0.5,
                perspective_quad=legacy_quad,
                curve=0.5,
            )


def _overlap_ratio(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = width * height
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    return intersection / max(first_area, 1e-12)


def _intersection_over_union(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = width * height
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / max(first_area + second_area - intersection, 1e-12)


def _render_occluded_plane_fixture(
    path: Path,
    *,
    colors: tuple[str, str],
) -> tuple[tuple[tuple[float, float, float, float], ...], float]:
    size = (1_000, 700)
    image = Image.new("RGBA", size, "white")
    fixtures = (
        (
            "平面一 PERSPECTIVE 2026",
            (60.0, 70.0, 660.0, 40.0, 690.0, 300.0, 40.0, 250.0),
            colors[0],
        ),
        (
            "平面二 PERSPECTIVE 2026",
            (260.0, 80.0, 900.0, 160.0, 930.0, 290.0, 220.0, 400.0),
            colors[1],
        ),
    )
    expected_regions: list[tuple[float, float, float, float]] = []
    ink_masks: list[np.ndarray] = []
    for text, quad, color in fixtures:
        layer = Image.new("RGBA", (520, 120), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((10, 12), text, font=_font(46), fill=color)
        warped = _warp_surface_to_quad(layer, output_size=size, destination=quad)
        alpha = np.asarray(warped.getchannel("A"), dtype=np.uint8)
        ink_mask = alpha > 32
        y_indexes, x_indexes = np.nonzero(ink_mask)
        expected_regions.append(
            (
                float(np.min(x_indexes)) / size[0],
                float(np.min(y_indexes)) / size[1],
                (float(np.max(x_indexes)) + 1) / size[0],
                (float(np.max(y_indexes)) + 1) / size[1],
            )
        )
        ink_masks.append(ink_mask.copy())
        image.alpha_composite(warped)
        layer.close()
        warped.close()
    rendered = image.convert("RGB")
    rendered.save(path)
    rendered.close()
    image.close()
    occluded_pixels = int(np.count_nonzero(ink_masks[0] & ink_masks[1]))
    occlusion_ratio = occluded_pixels / min(int(np.count_nonzero(mask)) for mask in ink_masks)
    return tuple(expected_regions), occlusion_ratio


def _render_three_plane_fixture(
    path: Path,
    *,
    colors: tuple[str, str, str],
) -> tuple[tuple[tuple[float, float, float, float], ...], tuple[float, ...]]:
    size = (1_000, 700)
    image = Image.new("RGBA", size, "white")
    fixtures = (
        (
            "平面一 PERSPECTIVE 2026",
            (60.0, 70.0, 660.0, 40.0, 690.0, 300.0, 40.0, 250.0),
            colors[0],
        ),
        (
            "平面三 PERSPECTIVE 2026",
            (120.0, 190.0, 780.0, 75.0, 835.0, 335.0, 90.0, 315.0),
            colors[1],
        ),
        (
            "平面二 PERSPECTIVE 2026",
            (260.0, 80.0, 900.0, 160.0, 930.0, 290.0, 220.0, 400.0),
            colors[2],
        ),
    )
    expected_regions: list[tuple[float, float, float, float]] = []
    ink_masks: list[np.ndarray] = []
    for text, quad, color in fixtures:
        layer = Image.new("RGBA", (520, 120), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((10, 12), text, font=_font(46), fill=color)
        warped = _warp_surface_to_quad(layer, output_size=size, destination=quad)
        alpha = np.asarray(warped.getchannel("A"), dtype=np.uint8)
        ink_mask = alpha > 32
        y_indexes, x_indexes = np.nonzero(ink_mask)
        expected_regions.append(
            (
                float(np.min(x_indexes)) / size[0],
                float(np.min(y_indexes)) / size[1],
                (float(np.max(x_indexes)) + 1) / size[0],
                (float(np.max(y_indexes)) + 1) / size[1],
            )
        )
        ink_masks.append(ink_mask.copy())
        image.alpha_composite(warped)
        layer.close()
        warped.close()
    rendered = image.convert("RGB")
    rendered.save(path)
    rendered.close()
    image.close()
    ink_overlaps = tuple(
        int(np.count_nonzero(first & second))
        / min(int(np.count_nonzero(first)), int(np.count_nonzero(second)))
        for index, first in enumerate(ink_masks)
        for second in ink_masks[index + 1 :]
    )
    return tuple(expected_regions), ink_overlaps


def _hex_color_error(actual: str | None, expected: str) -> int:
    if actual is None:
        return 255
    return max(
        abs(int(actual[index : index + 2], 16) - int(expected[index : index + 2], 16))
        for index in (1, 3, 5)
    )


if __name__ == "__main__":
    unittest.main()
