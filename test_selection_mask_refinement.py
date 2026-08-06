from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from light_image_editor.domain import (
    SelectionMaskStroke,
    parse_selection_mask_strokes,
    serialize_selection_mask_strokes,
)
from light_image_editor.services.smart_selection import (
    SmartSelectionError,
    decode_selection_mask,
    encode_selection_mask,
    refine_selection_mask,
)


class SelectionMaskRefinementTests(unittest.TestCase):
    def test_strokes_round_trip_with_stable_bounded_json(self) -> None:
        strokes = (
            SelectionMaskStroke("add", 0.05, ((0.1, 0.2), (0.3, 0.4))),
            SelectionMaskStroke("remove", 0.02, ((0.8, 0.7),)),
        )

        payload = serialize_selection_mask_strokes(strokes)

        self.assertEqual(parse_selection_mask_strokes(payload), strokes)
        self.assertEqual(serialize_selection_mask_strokes(strokes), payload)
        self.assertNotIn(" ", payload)

    def test_add_and_remove_strokes_modify_only_expected_mask_pixels(self) -> None:
        mask = Image.new("L", (32, 24), 0)
        ImageDraw.Draw(mask).rectangle((4, 4, 14, 18), fill=255)
        try:
            original_rle = encode_selection_mask(mask)
        finally:
            mask.close()
        strokes = (
            SelectionMaskStroke("add", 0.08, ((0.75, 0.5),)),
            SelectionMaskStroke("remove", 0.08, ((0.28, 0.5),)),
        )

        first = refine_selection_mask(original_rle, (32, 24), strokes)
        second = refine_selection_mask(original_rle, (32, 24), strokes)

        self.assertEqual(first, second)
        refined = decode_selection_mask(first, (32, 24))
        try:
            self.assertEqual(refined.getpixel((24, 12)), 255)
            self.assertEqual(refined.getpixel((9, 12)), 0)
            self.assertEqual(refined.getpixel((5, 5)), 255)
            self.assertEqual(refined.getpixel((30, 2)), 0)
        finally:
            refined.close()

    def test_empty_refined_mask_and_empty_source_fail_closed(self) -> None:
        mask = Image.new("L", (16, 16), 0)
        mask.putpixel((8, 8), 255)
        try:
            mask_rle = encode_selection_mask(mask)
        finally:
            mask.close()
        with self.assertRaisesRegex(SmartSelectionError, "不能为空"):
            refine_selection_mask(
                mask_rle,
                (16, 16),
                (SelectionMaskStroke("remove", 0.1, ((0.5, 0.5),)),),
            )
        empty = Image.new("L", (8, 8), 0)
        try:
            with self.assertRaisesRegex(SmartSelectionError, "不能为空"):
                encode_selection_mask(empty)
        finally:
            empty.close()

    def test_invalid_stroke_contracts_and_payloads_are_rejected(self) -> None:
        invalid_strokes = (
            ("paint", 0.1, ((0.5, 0.5),)),
            ("add", 0.0, ((0.5, 0.5),)),
            ("add", 0.1, ()),
            ("add", 0.1, ((-0.1, 0.5),)),
        )
        for mode, radius, points in invalid_strokes:
            with self.subTest(mode=mode, radius=radius, points=points):
                with self.assertRaises(ValueError):
                    SelectionMaskStroke(mode, radius, points)  # type: ignore[arg-type]

        for payload in ("", "{}", "[]", '[{"mode":"add"}]', "not-json"):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_selection_mask_strokes(payload)


if __name__ == "__main__":
    unittest.main()
