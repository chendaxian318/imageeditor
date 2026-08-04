from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops

from light_image_editor.thumbnails import THUMBNAIL_SIZE, ThumbnailLoader, generate_thumbnail


class ThumbnailGenerationTests(unittest.TestCase):
    def test_thumbnail_applies_exif_orientation_and_letterboxes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "rotated.jpg"
            image = Image.new("RGB", (40, 20), "#ff0000")
            exif = Image.Exif()
            exif[274] = 6
            image.save(source, exif=exif)
            image.close()

            thumbnail = generate_thumbnail(source, background="#000000")
            self.assertEqual(thumbnail.size, THUMBNAIL_SIZE)
            red_area = ImageChops.difference(thumbnail, Image.new("RGB", thumbnail.size, "black"))
            bounds = red_area.getbbox()
            self.assertIsNotNone(bounds)
            assert bounds is not None
            self.assertGreater(bounds[3] - bounds[1], bounds[2] - bounds[0])
            thumbnail.close()
            red_area.close()

    def test_transparency_is_composited_onto_sidebar_background(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "transparent.png"
            image = Image.new("RGBA", (20, 20), (255, 0, 0, 0))
            image.save(source)
            image.close()

            thumbnail = generate_thumbnail(source, size=(32, 32), background="#123456")
            self.assertEqual(thumbnail.getpixel((16, 16)), (18, 52, 86))
            thumbnail.close()


class ThumbnailLoaderTests(unittest.TestCase):
    def test_loader_returns_independent_cached_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.png"
            image = Image.new("RGB", (16, 16), "blue")
            image.save(source)
            image.close()
            loader = ThumbnailLoader(max_workers=1, cache_size=1)
            try:
                first = loader.load(source).result(timeout=5)
                first.close()
                second = loader.load(source).result(timeout=5)
                self.assertEqual(second.size, THUMBNAIL_SIZE)
                second.close()
                loader.invalidate(source)
                third = loader.load(source).result(timeout=5)
                self.assertEqual(third.size, THUMBNAIL_SIZE)
                third.close()
            finally:
                loader.shutdown()


if __name__ == "__main__":
    unittest.main()
