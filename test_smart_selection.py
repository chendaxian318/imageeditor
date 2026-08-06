from __future__ import annotations

import base64
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

from light_image_editor.services.smart_selection import (
    SmartSelectionError,
    SmartSelectionSuggestion,
    decode_selection_mask,
    detect_smart_selection,
)


def _overlap(
    actual: tuple[float, float, float, float],
    expected: tuple[float, float, float, float],
) -> float:
    width = max(0.0, min(actual[2], expected[2]) - max(actual[0], expected[0]))
    height = max(0.0, min(actual[3], expected[3]) - max(actual[1], expected[1]))
    intersection = width * height
    expected_area = (expected[2] - expected[0]) * (expected[3] - expected[1])
    return intersection / expected_area


class SmartSelectionTests(unittest.TestCase):
    def test_contrast_subject_is_detected_with_padding_and_stable_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "主体.png"
            image = Image.new("RGB", (240, 160), "#f5f5f5")
            ImageDraw.Draw(image).rounded_rectangle(
                (70, 38, 174, 132),
                radius=16,
                fill="#2468d8",
            )
            image.save(path)
            image.close()

            first = detect_smart_selection(path)
            second = detect_smart_selection(path)

            self.assertEqual(first, second)
            self.assertEqual(len(first), 1)
            suggestion = first[0]
            self.assertEqual(suggestion.source, "contrast")
            self.assertGreaterEqual(suggestion.confidence, 0.7)
            self.assertGreaterEqual(
                _overlap(suggestion.region, (70 / 240, 38 / 160, 175 / 240, 133 / 160)),
                0.98,
            )
            self.assertLess(suggestion.region[0], 70 / 240)
            self.assertGreater(suggestion.region[2], 175 / 240)

    def test_gradient_background_and_edge_touching_subject_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "贴边主体.png"
            image = Image.new("RGB", (300, 180))
            for x in range(image.width):
                shade = round(185 + x * 55 / (image.width - 1))
                for y in range(image.height):
                    image.putpixel((x, y), (shade, shade, shade))
            ImageDraw.Draw(image).rectangle((0, 42, 82, 144), fill="#1f63dc")
            image.save(path)
            image.close()

            suggestions = detect_smart_selection(path)

            self.assertEqual(len(suggestions), 1)
            suggestion = suggestions[0]
            self.assertEqual(suggestion.region[0], 0)
            self.assertGreaterEqual(
                _overlap(suggestion.region, (0, 42 / 180, 83 / 300, 145 / 180)),
                0.95,
            )

    def test_existing_transparency_is_used_as_strong_selection_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "透明主体.png"
            image = Image.new("RGBA", (200, 140), (0, 0, 0, 0))
            ImageDraw.Draw(image).ellipse((54, 28, 154, 124), fill=(220, 60, 80, 180))
            image.save(path)
            image.close()

            suggestions = detect_smart_selection(path, padding_ratio=0)

            self.assertEqual(len(suggestions), 1)
            suggestion = suggestions[0]
            self.assertEqual(suggestion.source, "alpha")
            self.assertEqual(suggestion.confidence, 0.98)
            self.assertGreaterEqual(
                _overlap(suggestion.region, (54 / 200, 28 / 140, 155 / 200, 125 / 140)),
                0.95,
            )

    def test_flat_image_returns_no_guess_instead_of_selecting_everything(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "平坦图片.png"
            Image.new("RGB", (120, 80), "#808080").save(path)
            self.assertEqual(detect_smart_selection(path), ())

    def test_multiple_subjects_are_ranked_by_area_and_result_limit_is_honored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "多个主体.png"
            image = Image.new("RGB", (320, 180), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((24, 30, 144, 150), fill="#245fd2")
            draw.ellipse((224, 62, 288, 126), fill="#dc3c50")
            image.save(path)
            image.close()

            suggestions = detect_smart_selection(path, padding_ratio=0)
            limited = detect_smart_selection(
                path,
                padding_ratio=0,
                maximum_suggestions=1,
            )

            self.assertEqual(len(suggestions), 2)
            self.assertEqual(limited, suggestions[:1])
            self.assertGreater(
                suggestions[0].foreground_fraction,
                suggestions[1].foreground_fraction,
            )
            self.assertLess(suggestions[0].region[0], suggestions[1].region[0])

            first_mask = decode_selection_mask(
                suggestions[0].mask_rle or "",
                suggestions[0].mask_size or (0, 0),
                target_size=(320, 180),
            )
            second_mask = decode_selection_mask(
                suggestions[1].mask_rle or "",
                suggestions[1].mask_size or (0, 0),
                target_size=(320, 180),
            )
            try:
                self.assertEqual(first_mask.getpixel((80, 90)), 255)
                self.assertEqual(first_mask.getpixel((256, 94)), 0)
                self.assertEqual(second_mask.getpixel((80, 90)), 0)
                self.assertEqual(second_mask.getpixel((256, 94)), 255)
                self.assertEqual(second_mask.getpixel((224, 62)), 0)
            finally:
                first_mask.close()
                second_mask.close()

    def test_touching_and_visibly_overlapping_colors_offer_exclusive_subsubjects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "相接重叠主体.png"
            image = Image.new("RGB", (260, 160), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 35, 135, 130), fill="#245fd2")
            draw.ellipse((110, 45, 210, 135), fill="#dc3c50")
            image.save(path)
            image.close()

            first = detect_smart_selection(path, padding_ratio=0)
            second = detect_smart_selection(path, padding_ratio=0)
            limited = detect_smart_selection(
                path,
                padding_ratio=0,
                maximum_suggestions=1,
            )

            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 3)
            self.assertEqual(first[0].selection_kind, "component")
            self.assertEqual(limited, first[:1])
            split = [item for item in first if item.selection_kind == "color_split"]
            self.assertGreaterEqual(len(split), 2)
            combined = decode_selection_mask(
                first[0].mask_rle or "",
                first[0].mask_size or (0, 0),
                target_size=(260, 160),
            )
            split_masks = [
                decode_selection_mask(
                    item.mask_rle or "",
                    item.mask_size or (0, 0),
                    target_size=(260, 160),
                )
                for item in split
            ]
            try:
                self.assertEqual(combined.getpixel((60, 80)), 255)
                self.assertEqual(combined.getpixel((175, 80)), 255)
                self.assertEqual(
                    sorted(
                        (
                            mask.getpixel((60, 80)),
                            mask.getpixel((175, 80)),
                        )
                        for mask in split_masks
                    ),
                    [(0, 255), (255, 0)],
                )
                self.assertEqual(sum(mask.getpixel((120, 80)) > 0 for mask in split_masks), 1)
            finally:
                combined.close()
                for mask in split_masks:
                    mask.close()

    def test_same_color_thin_neck_subjects_offer_exclusive_shape_splits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "同色细颈相接主体.png"
            image = Image.new("RGB", (260, 160), "white")
            draw = ImageDraw.Draw(image)
            draw.ellipse((30, 35, 125, 130), fill="#245fd2")
            draw.ellipse((120, 35, 215, 130), fill="#245fd2")
            image.save(path)
            image.close()

            first = detect_smart_selection(path, padding_ratio=0, maximum_suggestions=8)
            second = detect_smart_selection(path, padding_ratio=0, maximum_suggestions=8)
            limited = detect_smart_selection(
                path,
                padding_ratio=0,
                maximum_suggestions=1,
            )

            self.assertEqual(first, second)
            self.assertEqual(first[0].selection_kind, "component")
            self.assertEqual(limited, first[:1])
            split = [item for item in first if item.selection_kind == "shape_split"]
            self.assertEqual(len(split), 2)
            self.assertFalse(any(item.selection_kind == "color_split" for item in first))
            split_masks = [
                decode_selection_mask(
                    item.mask_rle or "",
                    item.mask_size or (0, 0),
                    target_size=(260, 160),
                )
                for item in split
            ]
            try:
                self.assertEqual(
                    sorted(
                        (
                            mask.getpixel((75, 80)),
                            mask.getpixel((170, 80)),
                        )
                        for mask in split_masks
                    ),
                    [(0, 255), (255, 0)],
                )
                self.assertEqual(
                    sum(mask.getpixel((122, 80)) > 0 for mask in split_masks),
                    1,
                )
            finally:
                for mask in split_masks:
                    mask.close()

    def test_same_color_moderate_overlap_splits_and_deep_overlap_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            moderate = root / "同色中等宽接触.png"
            image = Image.new("RGB", (260, 160), "white")
            draw = ImageDraw.Draw(image)
            draw.ellipse((30, 35, 125, 130), fill="#245fd2")
            draw.ellipse((100, 35, 195, 130), fill="#245fd2")
            image.save(moderate)
            image.close()

            first = detect_smart_selection(
                moderate,
                padding_ratio=0,
                maximum_suggestions=8,
            )
            second = detect_smart_selection(
                moderate,
                padding_ratio=0,
                maximum_suggestions=8,
            )
            self.assertEqual(first, second)
            self.assertEqual(first[0].selection_kind, "component")
            split = [item for item in first if item.selection_kind == "shape_split"]
            self.assertEqual(len(split), 2)
            masks = [
                decode_selection_mask(
                    item.mask_rle or "",
                    item.mask_size or (0, 0),
                    target_size=(260, 160),
                )
                for item in split
            ]
            try:
                self.assertEqual(
                    sorted(
                        (
                            mask.getpixel((75, 80)),
                            mask.getpixel((155, 80)),
                        )
                        for mask in masks
                    ),
                    [(0, 255), (255, 0)],
                )
                self.assertEqual(
                    sum(mask.getpixel((112, 80)) > 0 for mask in masks),
                    1,
                )
            finally:
                for mask in masks:
                    mask.close()

            deep = root / "同色深度重叠.png"
            image = Image.new("RGB", (260, 160), "white")
            draw = ImageDraw.Draw(image)
            draw.ellipse((30, 35, 125, 130), fill="#245fd2")
            draw.ellipse((90, 35, 185, 130), fill="#245fd2")
            image.save(deep)
            image.close()
            fallback = detect_smart_selection(
                deep,
                padding_ratio=0,
                maximum_suggestions=8,
            )
            self.assertTrue(fallback)
            self.assertFalse(any(item.selection_kind == "shape_split" for item in fallback))

    def test_single_same_color_shapes_do_not_create_shape_splits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for name, draw_shape in (
                (
                    "圆形",
                    lambda draw: draw.ellipse((45, 25, 205, 135), fill="#245fd2"),
                ),
                (
                    "圆角矩形",
                    lambda draw: draw.rounded_rectangle(
                        (35, 35, 225, 125),
                        radius=28,
                        fill="#245fd2",
                    ),
                ),
            ):
                path = Path(directory) / f"单一{name}.png"
                image = Image.new("RGB", (260, 160), "white")
                draw_shape(ImageDraw.Draw(image))
                image.save(path)
                image.close()
                suggestions = detect_smart_selection(
                    path,
                    padding_ratio=0,
                    maximum_suggestions=8,
                )
                self.assertTrue(suggestions)
                self.assertFalse(
                    any(item.selection_kind == "shape_split" for item in suggestions),
                    name,
                )

    def test_mask_decoder_rejects_corrupt_overlapping_and_trailing_payloads(self) -> None:
        invalid_payloads = (
            "not-base64",
            base64.b64encode(zlib.compress(b"short")).decode("ascii"),
            base64.b64encode(zlib.compress(struct.pack(">IIII", 2, 4, 4, 2))).decode("ascii"),
            base64.b64encode(zlib.compress(struct.pack(">II", 99, 2))).decode("ascii"),
            base64.b64encode(zlib.compress(struct.pack(">II", 1, 2)) + b"tail").decode("ascii"),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(SmartSelectionError):
                    decode_selection_mask(payload, (8, 8))

        with self.assertRaises(SmartSelectionError):
            decode_selection_mask("eA==", (4096, 4096))

    def test_invalid_requests_and_suggestions_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "损坏.png"
            path.write_bytes(b"not-an-image")
            with self.assertRaises(SmartSelectionError):
                detect_smart_selection(path)
            with self.assertRaises(SmartSelectionError):
                detect_smart_selection(path, maximum_dimension=32)
            with self.assertRaises(SmartSelectionError):
                detect_smart_selection(path, maximum_suggestions=0)
            with self.assertRaises(SmartSelectionError):
                SmartSelectionSuggestion((0, 0, 2, 1), 0.8, 0.2, "contrast")
            with self.assertRaises(SmartSelectionError):
                SmartSelectionSuggestion(
                    (0, 0, 1, 1),
                    0.8,
                    0.2,
                    "contrast",
                    mask_size=(8, 8),
                )
            with self.assertRaises(SmartSelectionError):
                SmartSelectionSuggestion(
                    (0, 0, 1, 1),
                    0.8,
                    0.2,
                    "contrast",
                    selection_kind="semantic",
                )


if __name__ == "__main__":
    unittest.main()
