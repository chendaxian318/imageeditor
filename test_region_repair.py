from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from light_image_editor.services.region_repair import (
    RegionRepairError,
    RegionRepairSpec,
    repair_mask,
    repair_region,
)


class RegionRepairTests(unittest.TestCase):
    @staticmethod
    def _gradient_fixture() -> Image.Image:
        image = Image.new("RGBA", (40, 30))
        for y in range(image.height):
            for x in range(image.width):
                image.putpixel((x, y), (20 + x * 3, 30 + y * 4, 90, 200 + x))
        for y in range(10, 20):
            for x in range(12, 28):
                image.putpixel((x, y), (240, 20, 30, 255))
        return image

    def test_linear_background_is_reconstructed_and_object_is_removed(self) -> None:
        source = self._gradient_fixture()
        try:
            result = repair_region(
                source,
                RegionRepairSpec((12 / 40, 10 / 30, 28 / 40, 20 / 30), feather=0),
            )
            try:
                self.assertEqual(result.mode, "RGBA")
                self.assertEqual(result.size, source.size)
                self.assertEqual(result.getpixel((4, 4)), source.getpixel((4, 4)))
                center = result.getpixel((20, 15))
                self.assertLess(abs(center[0] - 80), 2)
                self.assertLess(abs(center[1] - 90), 3)
                self.assertLessEqual(abs(center[2] - 90), 1)
                self.assertLess(abs(center[3] - 220), 2)
                self.assertNotEqual(center, (240, 20, 30, 255))
            finally:
                result.close()
        finally:
            source.close()

    def test_feathered_repair_is_deterministic_and_keeps_far_pixels(self) -> None:
        source = self._gradient_fixture()
        spec = RegionRepairSpec((0.3, 1 / 3, 0.7, 2 / 3), feather=4)
        try:
            first = repair_region(source, spec)
            second = repair_region(source, spec)
            try:
                self.assertEqual(first.tobytes(), second.tobytes())
                self.assertEqual(first.getpixel((2, 2)), source.getpixel((2, 2)))
                self.assertNotEqual(first.getpixel((20, 15)), source.getpixel((20, 15)))
            finally:
                first.close()
                second.close()
        finally:
            source.close()

    def test_region_on_image_edge_is_supported(self) -> None:
        source = Image.new("RGB", (20, 16), "#778899")
        try:
            result = repair_region(source, RegionRepairSpec((0.0, 0.0, 0.4, 0.5), feather=0))
            try:
                self.assertEqual(result.getpixel((1, 1)), (119, 136, 153, 255))
            finally:
                result.close()
        finally:
            source.close()

    def test_binary_mask_changes_only_selected_pixels_inside_its_box(self) -> None:
        source = self._gradient_fixture()
        mask = Image.new("L", source.size, 0)
        ImageDraw.Draw(mask).ellipse((12, 10, 27, 19), fill=255)
        try:
            result = repair_mask(source, mask)
            try:
                self.assertNotEqual(result.getpixel((20, 15)), source.getpixel((20, 15)))
                self.assertEqual(result.getpixel((12, 10)), source.getpixel((12, 10)))
                self.assertEqual(result.getpixel((27, 19)), source.getpixel((27, 19)))
                self.assertEqual(result.getpixel((4, 4)), source.getpixel((4, 4)))
            finally:
                result.close()
        finally:
            source.close()
            mask.close()

    def test_mask_repair_is_deterministic_and_supports_feathering(self) -> None:
        source = self._gradient_fixture()
        mask = Image.new("1", source.size, 0)
        ImageDraw.Draw(mask).rectangle((12, 10, 27, 19), fill=1)
        try:
            first = repair_mask(source, mask, feather=3)
            second = repair_mask(source, mask, feather=3)
            try:
                self.assertEqual(first.tobytes(), second.tobytes())
                self.assertNotEqual(first.getpixel((20, 15)), source.getpixel((20, 15)))
                self.assertEqual(first.getpixel((2, 2)), source.getpixel((2, 2)))
            finally:
                first.close()
                second.close()
        finally:
            source.close()
            mask.close()

    def test_invalid_regions_and_feather_fail_closed(self) -> None:
        invalid_values = (
            ((0.2, 0.2, 0.2, 0.8), 0),
            ((-0.1, 0.2, 0.8, 0.8), 0),
            ((0.1, 0.2, 0.8, 1.1), 0),
            ((0.1, 0.2, 0.8, 0.8), 65),
        )
        for region, feather in invalid_values:
            with self.subTest(region=region, feather=feather):
                with self.assertRaises(RegionRepairError):
                    RegionRepairSpec(region, feather=feather)  # type: ignore[arg-type]

        source = Image.new("RGB", (8, 8), "white")
        try:
            with self.assertRaisesRegex(RegionRepairError, "至少需要"):
                repair_region(source, RegionRepairSpec((0.0, 0.0, 0.2, 0.2), feather=0))
            empty = Image.new("L", source.size, 0)
            wrong_size = Image.new("L", (7, 8), 255)
            tiny = Image.new("L", source.size, 0)
            ImageDraw.Draw(tiny).rectangle((1, 1, 2, 2), fill=255)
            try:
                with self.assertRaisesRegex(RegionRepairError, "不能为空"):
                    repair_mask(source, empty)
                with self.assertRaisesRegex(RegionRepairError, "尺寸"):
                    repair_mask(source, wrong_size)
                with self.assertRaisesRegex(RegionRepairError, "至少需要"):
                    repair_mask(source, tiny)
                with self.assertRaises(RegionRepairError):
                    repair_mask(source, empty, feather=65)
            finally:
                empty.close()
                wrong_size.close()
                tiny.close()
        finally:
            source.close()


if __name__ == "__main__":
    unittest.main()
