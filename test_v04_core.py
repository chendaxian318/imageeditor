from __future__ import annotations

import unittest

import numpy as np
from PIL import Image

from light_image_editor.commands import EditCommand, EditStage, EditTimeline
from light_image_editor.renderer import (
    detect_straighten_angle,
    histogram,
    reduce_timeline,
    render_image,
)


class CommandTimelineTests(unittest.TestCase):
    def test_one_hundred_commands_undo_redo_without_state_drift(self) -> None:
        timeline = EditTimeline()
        for index in range(100):
            timeline.commit(
                EditCommand.create(
                    "adjust",
                    {"name": "brightness", "value": index % 51 - 25},
                    f"亮度 {index}",
                )
            )
        final_signature = timeline.active_signature()
        final_plan = reduce_timeline(timeline)
        for _ in range(100):
            self.assertIsNotNone(timeline.undo())
        self.assertEqual(timeline.cursor, 0)
        for _ in range(100):
            self.assertIsNotNone(timeline.redo())
        self.assertEqual(timeline.active_signature(), final_signature)
        self.assertEqual(reduce_timeline(timeline), final_plan)

    def test_merge_replaces_one_slider_transaction(self) -> None:
        timeline = EditTimeline()
        timeline.commit(
            EditCommand.create(
                "adjust",
                {"name": "contrast", "value": 10},
                "对比度 10",
                merge_key="contrast-drag",
            )
        )
        timeline.commit(
            EditCommand.create(
                "adjust",
                {"name": "contrast", "value": 35},
                "对比度 35",
                merge_key="contrast-drag",
            ),
            merge=True,
        )
        self.assertEqual(len(timeline.commands), 1)
        self.assertEqual(reduce_timeline(timeline).color.contrast, 35)

    def test_stage_is_validated_and_history_branch_is_truncated(self) -> None:
        timeline = EditTimeline()
        timeline.commit(EditCommand.create("rotate", {"degrees": 90}, "右转"))
        timeline.commit(EditCommand.create("crop", {"box": [0.1, 0.1, 0.9, 0.9]}, "裁剪"))
        self.assertEqual(timeline.commands[0].stage, EditStage.GEOMETRY)
        timeline.undo()
        timeline.commit(EditCommand.create("flip_horizontal", {}, "水平翻转"))
        self.assertEqual(
            [command.kind for command in timeline.commands], ["rotate", "flip_horizontal"]
        )


class RendererTests(unittest.TestCase):
    def test_preview_and_export_use_identical_full_resolution_pixels(self) -> None:
        base = Image.new("RGBA", (64, 48), (80, 120, 180, 128))
        timeline = EditTimeline()
        commands = (
            EditCommand.create("fine_rotate", {"angle": 2.5, "auto_crop": True}, "精细旋转"),
            EditCommand.create("levels", {"black": 5, "white": 245, "gamma": 1.1}, "色阶"),
            EditCommand.create(
                "curve",
                {"channel": "rgb", "points": [[0.0, 0.0], [0.5, 0.6], [1.0, 1.0]]},
                "RGB 曲线",
            ),
            EditCommand.create("adjust", {"name": "denoise", "value": 20}, "降噪"),
            EditCommand.create("adjust", {"name": "sharpen", "value": 30}, "锐化"),
        )
        for command in commands:
            timeline.commit(command)
        preview = render_image(base, timeline)
        exported = render_image(base, timeline)
        self.assertEqual(preview.mode, "RGBA")
        self.assertEqual(preview.size, exported.size)
        self.assertEqual(preview.tobytes(), exported.tobytes())

    def test_histogram_counts_every_sample_pixel(self) -> None:
        image = Image.new("RGB", (20, 10), (10, 20, 30))
        values = histogram(image)
        self.assertEqual(sum(values["red"]), 200)
        self.assertEqual(values["red"][10], 200)
        self.assertEqual(values["green"][20], 200)
        self.assertEqual(values["blue"][30], 200)

    def test_auto_straighten_detects_a_clear_rotated_grid(self) -> None:
        array = np.full((400, 500), 255, dtype=np.uint8)
        array[::40, :] = 0
        array[:, ::50] = 0
        image = Image.fromarray(array).rotate(4.0, expand=True, fillcolor=255)
        result = detect_straighten_angle(image)
        self.assertTrue(result.reliable, result)
        self.assertAlmostEqual(abs(result.angle), 4.0, delta=0.4)

    def test_auto_straighten_rejects_flat_image(self) -> None:
        result = detect_straighten_angle(Image.new("RGB", (200, 200), "gray"))
        self.assertFalse(result.reliable)
        self.assertEqual(result.angle, 0.0)


if __name__ == "__main__":
    unittest.main()
