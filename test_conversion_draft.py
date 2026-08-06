from __future__ import annotations

import unittest

from light_image_editor.domain import (
    ConversionControlState,
    ConversionDraft,
    ConversionDraftHistory,
    ExportProfile,
    TransformRecipe,
    TransformStep,
)


class ConversionDraftTests(unittest.TestCase):
    def test_imported_step_order_is_preserved_exactly(self) -> None:
        recipe = TransformRecipe(
            name="保留顺序",
            steps=(
                TransformStep.watermark("先加水印"),
                TransformStep.rotate(90),
                TransformStep.resize(max_width=800),
            ),
            export_profile=ExportProfile(output_format="PNG"),
        )

        draft = ConversionDraft.from_recipe(recipe)

        self.assertEqual(
            [step.kind for step in draft.to_recipe().steps],
            ["watermark", "rotate", "resize"],
        )
        self.assertIsNot(draft.to_recipe(), recipe)

    def test_replacing_existing_operation_keeps_imported_position(self) -> None:
        draft = ConversionDraft.from_recipe(
            TransformRecipe(
                steps=(
                    TransformStep.watermark("水印"),
                    TransformStep.rotate(90),
                    TransformStep.resize(max_width=400),
                )
            )
        )

        updated = draft.with_rotation(270)

        self.assertEqual([step.kind for step in updated.steps], ["watermark", "rotate", "resize"])
        self.assertEqual(updated.rotation, 270)
        self.assertEqual(draft.rotation, 90)

    def test_new_operations_use_canonical_pipeline_without_mutating_source(self) -> None:
        draft = ConversionDraft()

        updated = (
            draft.with_resize(max_width=1200, max_height=1200)
            .with_crop((0.1, 0.2, 0.9, 0.8))
            .with_flip("vertical", True)
            .with_rotation(90)
            .with_watermark("版权", position="左上", opacity=40, size_percent=6)
        )

        self.assertEqual(
            [_step_key(step) for step in updated.steps],
            ["rotate", "flip:vertical", "crop", "watermark", "resize"],
        )
        self.assertEqual(draft.steps, ())
        self.assertEqual(updated.crop_box, (0.1, 0.2, 0.9, 0.8))
        self.assertEqual(updated.resize_params["max_width"], 1200)
        self.assertEqual(updated.watermark_params["text"], "版权")

    def test_disabling_operations_and_export_changes_are_validated(self) -> None:
        draft = (
            ConversionDraft()
            .with_rotation(180)
            .with_flip("horizontal", True)
            .with_resize(exact_width=320, exact_height=200)
            .with_watermark("临时")
        )

        cleared = (
            draft.with_rotation(360)
            .with_flip("horizontal", False)
            .with_resize()
            .with_watermark("  ")
            .update_export_profile(
                output_format="WEBP",
                quality=76,
                target_size_kb=120,
            )
        )

        self.assertEqual(cleared.steps, ())
        self.assertEqual(cleared.export_profile.output_format, "WEBP")
        self.assertEqual(cleared.export_profile.target_size_kb, 120)
        with self.assertRaises(ValueError):
            cleared.update_export_profile(output_format="PNG", target_size_kb=100)

    def test_invalid_rotation_and_resize_do_not_create_partial_drafts(self) -> None:
        draft = ConversionDraft()

        with self.assertRaises(ValueError):
            draft.with_rotation(45)
        with self.assertRaises(ValueError):
            draft.with_resize(exact_width=20)

        self.assertEqual(draft.steps, ())

    def test_history_is_bounded_and_discards_redo_branch(self) -> None:
        history = ConversionDraftHistory(limit=3)
        first = history.current.with_rotation(90)
        second = first.with_rotation(180)
        third = second.with_rotation(270)
        self.assertTrue(history.push(first))
        self.assertTrue(history.push(second))
        self.assertTrue(history.push(third))

        self.assertEqual(history.length, 3)
        self.assertEqual(history.current.rotation, 270)
        undone = history.undo()
        self.assertIsNotNone(undone)
        assert undone is not None
        self.assertEqual(undone.rotation, 180)
        replacement = history.current.with_flip("horizontal", True)
        self.assertTrue(history.push(replacement))
        self.assertFalse(history.can_redo)
        self.assertIsNone(history.redo())

    def test_history_ignores_equal_drafts_and_supports_reset(self) -> None:
        initial = ConversionDraft().with_rotation(90)
        history = ConversionDraftHistory(initial)

        self.assertFalse(history.push(initial))
        self.assertFalse(history.can_undo)
        reset = history.reset(ConversionDraft().with_flip("vertical", True))
        self.assertTrue(reset.flip_vertical)
        self.assertEqual(history.length, 1)

    def test_control_state_round_trip_preserves_recipe_identity_and_profile(self) -> None:
        draft = ConversionDraft.from_recipe(
            TransformRecipe(
                name="网页导出",
                steps=(
                    TransformStep.rotate(90),
                    TransformStep.resize(max_width=1600, max_height=1600),
                ),
                export_profile=ExportProfile(
                    output_format="WEBP",
                    quality=78,
                    target_size_kb=240,
                    preserve_metadata=False,
                    rename_template="{stem}-web-{index:02d}",
                    conflict_policy="error",
                    transparency_background="#234567",
                    backup_on_overwrite=True,
                ),
            )
        )

        controls = ConversionControlState.from_draft(draft)
        restored = controls.apply_to(draft)

        self.assertEqual(controls.max_side_text, "1600")
        self.assertEqual(controls.transparency_background, "#234567")
        self.assertTrue(controls.transparency_background_explicit)
        self.assertEqual(restored.name, "网页导出")
        self.assertEqual(restored.rotation, 90)
        self.assertEqual(restored.export_profile, draft.export_profile)
        self.assertEqual(restored.export_profile.transparency_background, "#234567")

        control_without_background_field = ConversionControlState(
            output_format="WEBP",
            quality=80,
        )
        retained = control_without_background_field.apply_to(draft)
        self.assertEqual(retained.export_profile.transparency_background, "#234567")

        explicitly_cleared = ConversionControlState(
            output_format="WEBP",
            quality=80,
            transparency_background=None,
            transparency_background_explicit=True,
        ).apply_to(draft)
        self.assertIsNone(explicitly_cleared.export_profile.transparency_background)

        explicitly_changed = ConversionControlState(
            output_format="WEBP",
            quality=80,
            transparency_background="#ABCDEF",
            transparency_background_explicit=True,
        ).apply_to(draft)
        self.assertEqual(
            explicitly_changed.export_profile.transparency_background,
            "#abcdef",
        )

    def test_control_state_validates_conflicting_and_format_specific_values(self) -> None:
        draft = ConversionDraft()
        with self.assertRaises(ValueError):
            ConversionControlState(width_text="100", max_side_text="800").apply_to(draft)
        with self.assertRaises(ValueError):
            ConversionControlState(output_format="PNG", target_size_text="20").apply_to(draft)
        with self.assertRaises(ValueError):
            ConversionControlState(
                transparency_background_explicit=1  # type: ignore[arg-type]
            ).apply_to(draft)

        exact = ConversionControlState(width_text="320", height_text="180").apply_to(draft)
        self.assertEqual(exact.resize_params["exact_width"], 320)
        self.assertTrue(exact.resize_params["allow_upscale"])


def _step_key(step: TransformStep) -> str:
    return f"flip:{step.params['axis']}" if step.kind == "flip" else step.kind


if __name__ == "__main__":
    unittest.main()
