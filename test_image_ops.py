import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from image_editor import validate_filename_affix
from image_ops import (
    EditState,
    ExportOptions,
    apply_edits,
    apply_text_watermark,
    estimate_save_size,
    iter_image_files,
    natural_sort_key,
    open_image,
    prepare_image,
    save_image,
)


class ImageOpsTests(unittest.TestCase):
    def test_resize_crop_and_format_conversion(self) -> None:
        with TemporaryDirectory() as raw_dir:
            work = Path(raw_dir)
            source = work / "source.png"
            Image.new("RGBA", (400, 300), (20, 120, 220, 255)).save(source)

            image = open_image(source)
            target = work / "result.webp"
            save_image(
                image,
                target,
                ExportOptions(
                    output_format="WEBP",
                    quality=70,
                    exact_width=120,
                    exact_height=80,
                    crop_box=(20, 30, 320, 230),
                ),
            )

            self.assertTrue(target.exists())
            with Image.open(target) as result:
                self.assertEqual(result.size, (120, 80))
                self.assertEqual(result.format, "WEBP")

    def test_rotation_flip_adjustments_and_grayscale(self) -> None:
        image = Image.new("RGB", (40, 20), (80, 120, 160))

        edited = apply_edits(
            image,
            EditState(
                rotation=90,
                flip_horizontal=True,
                brightness=20,
                contrast=10,
                saturation=-30,
                sharpness=15,
                grayscale=True,
                auto_contrast=True,
            ),
        )

        self.assertEqual(edited.size, (20, 40))
        self.assertEqual(edited.mode, "RGB")

    def test_prepare_image_applies_edits_before_crop_and_resize(self) -> None:
        image = Image.new("RGB", (100, 50), "green")

        result = prepare_image(
            image,
            ExportOptions(
                output_format="JPEG",
                exact_width=20,
                exact_height=10,
                crop_box=(0, 0, 50, 100),
                edit_state=EditState(rotation=90),
            ),
        )

        self.assertEqual(result.size, (20, 10))

    def test_prepare_image_limits_longest_side_without_upscaling(self) -> None:
        wide = Image.new("RGB", (4000, 2000), "purple")
        small = Image.new("RGB", (400, 200), "purple")

        resized = prepare_image(
            wide, ExportOptions(output_format="JPEG", max_width=1000, max_height=1000)
        )
        unchanged = prepare_image(
            small, ExportOptions(output_format="JPEG", max_width=1000, max_height=1000)
        )

        self.assertEqual(resized.size, (1000, 500))
        self.assertEqual(unchanged.size, (400, 200))

    def test_prepare_image_limits_single_dimension_without_distortion(self) -> None:
        image = Image.new("RGB", (4000, 2000), "purple")

        width_limited = prepare_image(image, ExportOptions(output_format="JPEG", max_width=1000))
        height_limited = prepare_image(image, ExportOptions(output_format="JPEG", max_height=500))

        self.assertEqual(width_limited.size, (1000, 500))
        self.assertEqual(height_limited.size, (1000, 500))

    def test_iter_image_files_finds_supported_images(self) -> None:
        with TemporaryDirectory() as raw_dir:
            work = Path(raw_dir)
            (work / "nested").mkdir()
            Image.new("RGB", (10, 10), "red").save(work / "a.jpg")
            Image.new("RGB", (10, 10), "blue").save(work / "nested" / "b.png")
            (work / "note.txt").write_text("ignore", encoding="utf-8")

            found = iter_image_files([work])

            self.assertEqual(len(found), 2)
            self.assertEqual({item.name for item in found}, {"a.jpg", "b.png"})

    def test_image_files_use_natural_sort_order(self) -> None:
        with TemporaryDirectory() as raw_dir:
            work = Path(raw_dir)
            for name in ("image10.jpg", "image2.jpg", "image1.jpg"):
                Image.new("RGB", (10, 10), "red").save(work / name)

            found = iter_image_files([work])

            self.assertEqual(
                [item.name for item in found], ["image1.jpg", "image2.jpg", "image10.jpg"]
            )
            self.assertLess(
                natural_sort_key(work / "image2.jpg"), natural_sort_key(work / "image10.jpg")
            )

    def test_jpeg_target_size_uses_lower_quality(self) -> None:
        with TemporaryDirectory() as raw_dir:
            work = Path(raw_dir)
            image = Image.effect_noise((900, 700), 90).convert("RGB")
            high_quality = ExportOptions(output_format="JPEG", quality=95)
            target_quality = ExportOptions(output_format="JPEG", quality=95, target_size_kb=80)

            high_size = estimate_save_size(image, high_quality)
            target_size = estimate_save_size(image, target_quality)
            target = work / "target.jpg"
            save_image(image, target, target_quality)

            self.assertGreater(high_size, target_size)
            self.assertLessEqual(target.stat().st_size, 80 * 1024)

    def test_advanced_format_options_change_real_encoded_outputs(self) -> None:
        with TemporaryDirectory() as raw_dir:
            work = Path(raw_dir)
            pattern = Image.new("RGB", (48, 32))
            pixels = pattern.load()
            assert pixels is not None
            for y in range(pattern.height):
                for x in range(pattern.width):
                    pixels[x, y] = ((x * 17) % 256, (y * 29) % 256, ((x + y) * 11) % 256)

            webp = work / "lossless.webp"
            save_image(
                pattern,
                webp,
                ExportOptions(output_format="WEBP", quality=25, webp_lossless=True),
            )
            with Image.open(webp) as decoded_webp:
                self.assertEqual(decoded_webp.convert("RGB").tobytes(), pattern.tobytes())

            transparent = Image.new("RGBA", (64, 40), (20, 80, 160, 255))
            for y in range(transparent.height):
                for x in range(transparent.width // 2):
                    transparent.putpixel((x, y), (220, 50, 90, 0))
            palette_png = work / "palette.png"
            save_image(
                transparent,
                palette_png,
                ExportOptions(output_format="PNG", png_palette_colors=16),
            )
            with Image.open(palette_png) as decoded_png:
                self.assertEqual(decoded_png.mode, "P")
                self.assertLessEqual(len(decoded_png.getcolors(maxcolors=256) or ()), 16)
                alpha_extrema = decoded_png.convert("RGBA").getchannel("A").getextrema()
                self.assertEqual(alpha_extrema, (0, 255))

            raw_tiff = work / "raw.tif"
            compressed_tiff = work / "compressed.tif"
            flat = Image.new("RGB", (256, 192), "#456789")
            save_image(flat, raw_tiff, ExportOptions(output_format="TIFF"))
            save_image(
                flat,
                compressed_tiff,
                ExportOptions(
                    output_format="TIFF",
                    tiff_compression="tiff_adobe_deflate",
                ),
            )
            with Image.open(compressed_tiff) as decoded_tiff:
                self.assertEqual(decoded_tiff.info.get("compression"), "tiff_adobe_deflate")
                self.assertEqual(decoded_tiff.convert("RGB").getpixel((0, 0)), (69, 103, 137))
            self.assertLess(compressed_tiff.stat().st_size, raw_tiff.stat().st_size)

            pattern.close()
            transparent.close()
            flat.close()

    def test_filename_affix_validation_trims_and_rejects_windows_invalid_chars(self) -> None:
        self.assertEqual(validate_filename_affix("prefix", "  web_  "), "web_")
        with self.assertRaises(ValueError):
            validate_filename_affix("suffix", "bad:name")

    def test_text_watermark_changes_image_without_resizing(self) -> None:
        image = Image.new("RGB", (300, 180), "navy")
        watermarked = apply_text_watermark(image, "Sample", "右下", opacity=80, size_percent=10)

        self.assertEqual(watermarked.size, image.size)
        self.assertNotEqual(watermarked.tobytes(), image.tobytes())

    def test_prepare_image_applies_watermark_after_resize(self) -> None:
        image = Image.new("RGB", (600, 400), "white")
        result = prepare_image(
            image,
            ExportOptions(
                output_format="PNG",
                exact_width=300,
                exact_height=200,
                watermark_text="Demo",
                watermark_position="居中",
                watermark_opacity=90,
                watermark_size_percent=12,
            ),
        )

        self.assertEqual(result.size, (300, 200))
        self.assertNotEqual(result.tobytes(), Image.new("RGB", (300, 200), "white").tobytes())


if __name__ == "__main__":
    unittest.main()
