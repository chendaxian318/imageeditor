from __future__ import annotations

import unittest

from PIL import Image, ImageChops

from light_image_editor.domain import (
    ExportProfile,
    TransformRecipe,
    TransformStep,
    execute_transform_recipe,
)


class RecipeExecutorTests(unittest.TestCase):
    def test_empty_recipe_returns_an_independent_copy_with_metadata(self) -> None:
        source = Image.new("RGB", (8, 6), "#42658a")
        source.info["icc_profile"] = b"profile"
        result = execute_transform_recipe(source, TransformRecipe())
        try:
            self.assertIsNot(result, source)
            self.assertEqual(result.size, source.size)
            self.assertEqual(result.getpixel((0, 0)), source.getpixel((0, 0)))
            self.assertEqual(result.info["icc_profile"], b"profile")
            result.putpixel((0, 0), (255, 0, 0))
            self.assertEqual(source.getpixel((0, 0)), (66, 101, 138))
        finally:
            result.close()
            source.close()

    def test_steps_execute_in_declaration_order(self) -> None:
        source = Image.new("RGB", (4, 2), "white")
        crop_then_rotate = TransformRecipe(
            steps=(
                TransformStep.crop((0.0, 0.0, 0.5, 1.0)),
                TransformStep.rotate(90),
            )
        )
        rotate_then_crop = TransformRecipe(
            steps=(
                TransformStep.rotate(90),
                TransformStep.crop((0.0, 0.0, 0.5, 1.0)),
            )
        )
        first = execute_transform_recipe(source, crop_then_rotate)
        second = execute_transform_recipe(source, rotate_then_crop)
        try:
            self.assertEqual(first.size, (2, 2))
            self.assertEqual(second.size, (1, 4))
        finally:
            first.close()
            second.close()
            source.close()

    def test_clockwise_rotation_and_flips_have_stable_pixel_semantics(self) -> None:
        source = Image.new("RGB", (2, 1))
        source.putdata([(255, 0, 0), (0, 0, 255)])
        rotated = execute_transform_recipe(
            source,
            TransformRecipe(steps=(TransformStep.rotate(90),)),
        )
        flipped = execute_transform_recipe(
            source,
            TransformRecipe(steps=(TransformStep.flip("horizontal"),)),
        )
        try:
            self.assertEqual(rotated.size, (1, 2))
            self.assertEqual(rotated.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(rotated.getpixel((0, 1)), (0, 0, 255))
            self.assertEqual(flipped.getpixel((0, 0)), (0, 0, 255))
            self.assertEqual(flipped.getpixel((1, 0)), (255, 0, 0))
        finally:
            rotated.close()
            flipped.close()
            source.close()

    def test_exact_and_bounded_resize_honor_upscale_policy(self) -> None:
        source = Image.new("RGB", (20, 10), "#778899")
        exact = execute_transform_recipe(
            source,
            TransformRecipe(steps=(TransformStep.resize(exact_width=7, exact_height=9),)),
        )
        bounded = execute_transform_recipe(
            source,
            TransformRecipe(steps=(TransformStep.resize(max_width=40, max_height=40),)),
        )
        enlarged = execute_transform_recipe(
            source,
            TransformRecipe(
                steps=(
                    TransformStep.resize(
                        max_width=40,
                        max_height=40,
                        allow_upscale=True,
                    ),
                )
            ),
        )
        try:
            self.assertEqual(exact.size, (7, 9))
            self.assertEqual(bounded.size, (20, 10))
            self.assertEqual(enlarged.size, (40, 20))
        finally:
            exact.close()
            bounded.close()
            enlarged.close()
            source.close()

    def test_watermark_changes_output_without_touching_source(self) -> None:
        source = Image.new("RGB", (240, 120), "white")
        baseline = source.copy()
        result = execute_transform_recipe(
            source,
            TransformRecipe(
                steps=(
                    TransformStep.watermark(
                        "示例水印",
                        position="居中",
                        opacity=100,
                        size_percent=12,
                    ),
                )
            ),
        )
        try:
            self.assertIsNotNone(ImageChops.difference(result, baseline).getbbox())
            self.assertIsNone(ImageChops.difference(source, baseline).getbbox())
        finally:
            result.close()
            baseline.close()
            source.close()

    def test_opaque_only_formats_composite_partial_alpha_over_configured_background(
        self,
    ) -> None:
        source = Image.new("RGBA", (2, 1), (255, 0, 0, 128))
        source.putpixel((1, 0), (0, 255, 0, 0))
        try:
            for output_format in ("JPEG", "BMP"):
                with self.subTest(output_format=output_format):
                    result = execute_transform_recipe(
                        source,
                        TransformRecipe(
                            export_profile=ExportProfile(
                                output_format=output_format,
                                transparency_background="#000000",
                            )
                        ),
                    )
                    try:
                        self.assertEqual(result.mode, "RGB")
                        self.assertEqual(result.getpixel((0, 0)), (128, 0, 0))
                        self.assertEqual(result.getpixel((1, 0)), (0, 0, 0))
                    finally:
                        result.close()

            self.assertEqual(source.mode, "RGBA")
            self.assertEqual(source.getpixel((0, 0)), (255, 0, 0, 128))
        finally:
            source.close()

    def test_alpha_capable_format_keeps_alpha_when_background_is_configured(self) -> None:
        source = Image.new("RGBA", (1, 1), (255, 0, 0, 128))
        result = execute_transform_recipe(
            source,
            TransformRecipe(
                export_profile=ExportProfile(
                    output_format="PNG",
                    transparency_background="#ffffff",
                )
            ),
        )
        try:
            self.assertEqual(result.mode, "RGBA")
            self.assertEqual(result.getpixel((0, 0)), (255, 0, 0, 128))
        finally:
            result.close()
            source.close()

    def test_gif_partial_alpha_is_composited_when_background_is_configured(self) -> None:
        source = Image.new("RGBA", (2, 1), (40, 80, 120, 128))
        source.putpixel((1, 0), (255, 255, 255, 0))
        result = execute_transform_recipe(
            source,
            TransformRecipe(
                export_profile=ExportProfile(
                    output_format="GIF",
                    transparency_background="#ffffff",
                )
            ),
        )
        try:
            self.assertEqual(result.mode, "RGB")
            self.assertEqual(result.getpixel((0, 0)), (147, 167, 187))
            self.assertEqual(result.getpixel((1, 0)), (255, 255, 255))
        finally:
            result.close()
            source.close()


if __name__ == "__main__":
    unittest.main()
