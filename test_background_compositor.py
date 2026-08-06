from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from light_image_editor.services.background_compositor import (
    BackgroundCompositionError,
    BackgroundKind,
    BackgroundSpec,
    compose_background,
)


class BackgroundCompositorTests(unittest.TestCase):
    def test_solid_colour_composites_real_alpha_and_writes_opaque_png(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foreground_path = root / "foreground.png"
            output_path = root / "output.png"
            foreground = Image.new("RGBA", (3, 1), (0, 0, 0, 0))
            foreground.putpixel((1, 0), (200, 0, 0, 128))
            foreground.putpixel((2, 0), (12, 34, 56, 255))
            foreground.save(foreground_path)
            foreground.close()

            result = compose_background(
                foreground_path,
                output_path,
                BackgroundSpec.solid_color("#0000FF"),
            )

            self.assertEqual((result.width, result.height), (3, 1))
            self.assertTrue(result.source_had_transparency)
            self.assertIsNone(result.background_sha256)
            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.mode, "RGBA")
                self.assertEqual(output.getpixel((0, 0)), (0, 0, 255, 255))
                self.assertEqual(output.getpixel((1, 0)), (100, 0, 127, 255))
                self.assertEqual(output.getpixel((2, 0)), (12, 34, 56, 255))

    def test_custom_image_is_centre_fitted_and_hash_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foreground_path = root / "foreground.png"
            background_path = root / "background.png"
            output_path = root / "output.png"
            Image.new("RGBA", (4, 2), (0, 0, 0, 0)).save(foreground_path)
            background = Image.new("RGB", (2, 1), "#ff0000")
            background.putpixel((1, 0), (0, 255, 0))
            background.save(background_path)
            background.close()

            result = compose_background(
                foreground_path,
                output_path,
                BackgroundSpec.custom_image(background_path, fallback_color="#ffffff"),
            )

            self.assertIsNotNone(result.background_sha256)
            assert result.background_sha256 is not None
            self.assertEqual(len(result.background_sha256), 64)
            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.getpixel((0, 0))[:3], (255, 0, 0))
                self.assertEqual(output.getpixel((3, 1))[:3], (0, 255, 0))
                self.assertTrue(all(pixel[3] == 255 for pixel in output.getdata()))

    def test_transparent_custom_background_falls_back_to_selected_colour(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foreground_path = root / "foreground.png"
            background_path = root / "background.png"
            output_path = root / "output.png"
            Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(foreground_path)
            Image.new("RGBA", (2, 2), (255, 0, 0, 0)).save(background_path)

            compose_background(
                foreground_path,
                output_path,
                BackgroundSpec.custom_image(background_path, fallback_color="#123456"),
            )

            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.getpixel((1, 1)), (18, 52, 86, 255))

    def test_invalid_requests_do_not_leave_output_or_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foreground_path = root / "foreground.png"
            output_path = root / "output.png"
            Image.new("RGBA", (3, 3), (0, 0, 0, 0)).save(foreground_path)

            with self.assertRaisesRegex(BackgroundCompositionError, "#RRGGBB"):
                BackgroundSpec.solid_color("red")
            with self.assertRaisesRegex(BackgroundCompositionError, "请选择"):
                BackgroundSpec(BackgroundKind.IMAGE, color="#ffffff")
            with self.assertRaisesRegex(BackgroundCompositionError, "不存在"):
                compose_background(
                    foreground_path,
                    output_path,
                    BackgroundSpec.custom_image(
                        root / "missing.png",
                        fallback_color="#ffffff",
                    ),
                )
            self.assertFalse(output_path.exists())
            self.assertEqual(tuple(root.glob("*.tmp")), ())

    def test_pixel_and_byte_safety_limits_fail_before_output_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foreground_path = root / "foreground.png"
            background_path = root / "background.png"
            output_path = root / "output.png"
            Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(foreground_path)
            Image.new("RGB", (8, 8), "#556677").save(background_path)

            with self.assertRaisesRegex(BackgroundCompositionError, "像素安全上限"):
                compose_background(
                    foreground_path,
                    output_path,
                    BackgroundSpec.solid_color("#ffffff"),
                    max_pixels=63,
                )
            with self.assertRaisesRegex(BackgroundCompositionError, "字节安全上限"):
                compose_background(
                    foreground_path,
                    output_path,
                    BackgroundSpec.custom_image(
                        background_path,
                        fallback_color="#ffffff",
                    ),
                    max_background_bytes=1,
                )
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
