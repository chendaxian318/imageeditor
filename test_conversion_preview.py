from __future__ import annotations

import unittest

from PIL import Image

from image_ops import EditState
from light_image_editor.commands import EditCommand, EditTimeline
from light_image_editor.domain import TransformRecipe, TransformStep
from light_image_editor.preview import PreviewRenderer, render_preview


class ConversionPreviewTests(unittest.TestCase):
    def test_recipe_preview_is_owned_and_does_not_mutate_source(self) -> None:
        source = Image.new("RGB", (80, 40), "#7d6892")
        before = source.tobytes()
        recipe = TransformRecipe(
            steps=(
                TransformStep.rotate(90),
                TransformStep.resize(exact_width=20, exact_height=30),
            )
        )

        frame = render_preview(
            source,
            EditState(),
            compare_original=False,
            canvas_size=(200, 200),
            zoom_percent=100,
            conversion_recipe=recipe,
        )
        try:
            self.assertEqual(frame.working.size, (20, 30))
            self.assertEqual(frame.image.size, (20, 30))
            self.assertTrue(frame.owns_working)
            self.assertEqual(source.size, (80, 40))
            self.assertEqual(source.tobytes(), before)
        finally:
            frame.close()
            source.close()

    def test_conversion_recipe_takes_precedence_over_edit_timeline(self) -> None:
        source = Image.new("RGB", (100, 60), "#55778f")
        timeline = EditTimeline()
        timeline.commit(
            EditCommand.create(
                "resize",
                {"exact_width": 11, "exact_height": 12},
                "编辑时间线尺寸",
            )
        )
        recipe = TransformRecipe(steps=(TransformStep.resize(exact_width=25, exact_height=15),))

        frame = render_preview(
            source,
            EditState(),
            compare_original=False,
            canvas_size=(200, 200),
            zoom_percent=100,
            timeline=timeline,
            conversion_recipe=recipe,
        )
        try:
            self.assertEqual(frame.working.size, (25, 15))
        finally:
            frame.close()
            source.close()

    def test_background_renderer_accepts_conversion_recipe(self) -> None:
        source = Image.new("RGB", (64, 64), "#6f8a5f")
        renderer = PreviewRenderer()
        try:
            future = renderer.render(
                source,
                EditState(),
                compare_original=False,
                canvas_size=(100, 100),
                zoom_percent=100,
                conversion_recipe=TransformRecipe(
                    steps=(TransformStep.crop((0.25, 0.25, 0.75, 0.75)),)
                ),
            )
            frame = future.result(timeout=5)
            try:
                self.assertEqual(frame.working.size, (32, 32))
            finally:
                frame.close()
        finally:
            renderer.shutdown()
            source.close()


if __name__ == "__main__":
    unittest.main()
