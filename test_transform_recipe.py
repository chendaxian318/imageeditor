from __future__ import annotations

import copy
import unittest

from light_image_editor.batch import BatchRecipe
from light_image_editor.domain.recipes import (
    TRANSFORM_RECIPE_SCHEMA_VERSION,
    ExportProfile,
    TransformRecipe,
    TransformStep,
    from_batch_recipe,
)


class TransformStepTests(unittest.TestCase):
    def test_supported_steps_are_canonicalized(self) -> None:
        self.assertEqual(TransformStep.rotate(-90).params, {"degrees": 270})
        self.assertEqual(TransformStep.flip("horizontal").params, {"axis": "horizontal"})
        self.assertEqual(
            TransformStep.crop((0.1, 0.2, 0.8, 0.9)).params,
            {"box": [0.1, 0.2, 0.8, 0.9]},
        )
        self.assertEqual(
            TransformStep.resize(max_width=1920).params,
            {"allow_upscale": False, "max_width": 1920},
        )

    def test_step_json_round_trip(self) -> None:
        step = TransformStep.resize(exact_width=1280, exact_height=720, allow_upscale=True)
        self.assertEqual(TransformStep.from_json(step.to_json()), step)

    def test_exact_resize_allows_upscale_but_bound_resize_does_not(self) -> None:
        exact = TransformStep.resize(exact_width=1280, exact_height=720)
        bounded = TransformStep.resize(max_width=1280, max_height=720)
        self.assertTrue(exact.params["allow_upscale"])
        self.assertFalse(bounded.params["allow_upscale"])
        with self.assertRaises(ValueError):
            TransformStep.resize(
                exact_width=1280,
                exact_height=720,
                allow_upscale=False,
            )

    def test_step_validation_is_strict(self) -> None:
        invalid_steps = (
            lambda: TransformStep("crop", {"box": [0, 0, 0, 1]}),
            lambda: TransformStep.rotate(45),
            lambda: TransformStep("flip", {"axis": "diagonal"}),
            lambda: TransformStep.resize(exact_width=100),
            lambda: TransformStep.resize(max_width=0),
            lambda: TransformStep("resize", {"max_width": 100, "extra": True}),
            lambda: TransformStep("unknown", {}),
        )
        for create in invalid_steps:
            with self.subTest(create=create), self.assertRaises(ValueError):
                create()


class ExportProfileTests(unittest.TestCase):
    def test_format_specific_advanced_options_round_trip(self) -> None:
        profiles = (
            ExportProfile(output_format="WEBP", webp_lossless=True),
            ExportProfile(output_format="PNG", png_palette_colors=64),
            ExportProfile(output_format="TIFF", tiff_compression="tiff_lzw"),
        )

        for profile in profiles:
            with self.subTest(output_format=profile.output_format):
                self.assertEqual(ExportProfile.from_json(profile.to_json()), profile)

    def test_export_profile_json_round_trip_and_normalization(self) -> None:
        profile = ExportProfile(
            output_format="jpg",
            quality=92,
            target_size_kb=500,
            preserve_metadata=False,
            rename_template="{stem}_{date}_{index:03d}",
            conflict_policy="OVERWRITE",
            preserve_animation=False,
            transparency_background="#A1B2C3",
            allow_in_place=True,
            backup_on_overwrite=True,
        )
        self.assertEqual(profile.output_format, "JPEG")
        self.assertEqual(profile.conflict_policy, "overwrite")
        self.assertEqual(profile.transparency_background, "#a1b2c3")
        self.assertTrue(profile.backup_on_overwrite)
        self.assertEqual(ExportProfile.from_json(profile.to_json()), profile)

    def test_export_profile_rejects_invalid_semantics(self) -> None:
        invalid_profiles = (
            lambda: ExportProfile(quality=0),
            lambda: ExportProfile(target_size_kb=0),
            lambda: ExportProfile(output_format="PNG", target_size_kb=100),
            lambda: ExportProfile(output_format="WEBP", webp_lossless=True, target_size_kb=100),
            lambda: ExportProfile(output_format="JPEG", webp_lossless=True),
            lambda: ExportProfile(output_format="JPEG", png_palette_colors=64),
            lambda: ExportProfile(output_format="PNG", png_palette_colors=1),
            lambda: ExportProfile(output_format="PNG", png_palette_colors=257),
            lambda: ExportProfile(output_format="JPEG", tiff_compression="tiff_lzw"),
            lambda: ExportProfile(output_format="TIFF", tiff_compression="zip"),
            lambda: ExportProfile(preserve_metadata=1),  # type: ignore[arg-type]
            lambda: ExportProfile(backup_on_overwrite=1),  # type: ignore[arg-type]
            lambda: ExportProfile(transparency_background="white"),
            lambda: ExportProfile(transparency_background="#ffffff00"),
            lambda: ExportProfile(transparency_background=123),  # type: ignore[arg-type]
            lambda: ExportProfile(rename_template="{unsupported}"),
            lambda: ExportProfile(conflict_policy="rename"),
        )
        for create in invalid_profiles:
            with self.subTest(create=create), self.assertRaises(ValueError):
                create()

    def test_optional_transparency_background_is_backward_compatible_and_strict(self) -> None:
        old_payload = ExportProfile(output_format="JPEG").to_json()
        old_payload.pop("transparency_background")

        restored = ExportProfile.from_json(old_payload)

        self.assertIsNone(restored.transparency_background)
        self.assertEqual(ExportProfile(output_format="gif").output_format, "GIF")

    def test_export_profile_rejects_unknown_json_fields(self) -> None:
        payload = ExportProfile().to_json()
        payload["unknown"] = True
        with self.assertRaises(ValueError):
            ExportProfile.from_json(payload)


class TransformRecipeTests(unittest.TestCase):
    def test_version_one_recipe_migrates_to_advanced_format_defaults(self) -> None:
        payload = TransformRecipe(
            name="旧版配方",
            export_profile=ExportProfile(output_format="PNG"),
        ).to_json()
        payload["schema_version"] = 1
        payload["export"].pop("webp_lossless")
        payload["export"].pop("png_palette_colors")
        payload["export"].pop("tiff_compression")

        migrated = TransformRecipe.from_json(payload)

        self.assertEqual(migrated.schema_version, TRANSFORM_RECIPE_SCHEMA_VERSION)
        self.assertFalse(migrated.export_profile.webp_lossless)
        self.assertIsNone(migrated.export_profile.png_palette_colors)
        self.assertEqual(migrated.export_profile.tiff_compression, "raw")

    def test_full_recipe_json_round_trip_is_lossless(self) -> None:
        recipe = TransformRecipe(
            name="社交媒体配方",
            steps=(
                TransformStep.rotate(90),
                TransformStep.flip("horizontal"),
                TransformStep.crop((0.1, 0.1, 0.9, 0.9)),
                TransformStep.resize(exact_width=1080, exact_height=1080),
            ),
            export_profile=ExportProfile(
                output_format="WEBP",
                quality=88,
                target_size_kb=350,
                preserve_metadata=False,
                rename_template="{stem}_social_{index:02d}",
                conflict_policy="unique",
            ),
        )

        payload = recipe.to_json()
        self.assertEqual(payload["schema_version"], TRANSFORM_RECIPE_SCHEMA_VERSION)
        self.assertEqual(TransformRecipe.from_json(copy.deepcopy(payload)), recipe)

    def test_recipe_json_is_detached_from_domain_objects(self) -> None:
        recipe = TransformRecipe(steps=(TransformStep.crop((0.1, 0.2, 0.8, 0.9)),))
        payload = recipe.to_json()
        payload["steps"][0]["params"]["box"][0] = 0.5
        self.assertEqual(recipe.steps[0].params["box"][0], 0.1)

    def test_recipe_rejects_unknown_version_fields_and_duplicates(self) -> None:
        payload = TransformRecipe().to_json()
        payload["schema_version"] = 999
        with self.assertRaises(ValueError):
            TransformRecipe.from_json(payload)

        payload = TransformRecipe().to_json()
        payload["unexpected"] = "value"
        with self.assertRaises(ValueError):
            TransformRecipe.from_json(payload)

        with self.assertRaises(ValueError):
            TransformRecipe(steps=(TransformStep.rotate(90), TransformStep.rotate(180)))
        with self.assertRaises(ValueError):
            TransformRecipe(
                steps=(TransformStep.flip("horizontal"), TransformStep.flip("horizontal"))
            )

    def test_existing_batch_recipe_maps_without_mutation(self) -> None:
        batch = BatchRecipe(
            output_format="WEBP",
            quality=74,
            max_width=1920,
            max_height=1080,
            watermark_text="样张",
            watermark_position="左下",
            watermark_opacity=42,
            watermark_size_percent=7,
            strip_metadata=True,
            rename_template="{stem}_converted_{index:03d}",
            conflict_policy="overwrite",
            preserve_animation=False,
            allow_in_place=True,
        )

        recipe = from_batch_recipe(batch, name="旧批处理迁移")

        self.assertEqual(recipe.name, "旧批处理迁移")
        self.assertEqual([step.kind for step in recipe.steps], ["resize", "watermark"])
        self.assertEqual(recipe.steps[0].params["max_width"], 1920)
        self.assertEqual(recipe.steps[1].params["text"], "样张")
        self.assertEqual(recipe.export_profile.output_format, "WEBP")
        self.assertEqual(recipe.export_profile.quality, 74)
        self.assertFalse(recipe.export_profile.preserve_metadata)
        self.assertEqual(recipe.export_profile.rename_template, batch.rename_template)
        self.assertEqual(recipe.export_profile.conflict_policy, "overwrite")
        self.assertFalse(recipe.export_profile.preserve_animation)
        self.assertTrue(recipe.export_profile.allow_in_place)
        self.assertFalse(recipe.export_profile.backup_on_overwrite)
        self.assertEqual(batch.max_width, 1920)

        self.assertEqual(TransformRecipe.from_batch_recipe(batch), from_batch_recipe(batch))


if __name__ == "__main__":
    unittest.main()
