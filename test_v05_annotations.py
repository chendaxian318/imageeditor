from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from light_image_editor.annotations import (
    AnnotationObject,
    hit_radius_px,
    object_at,
    parse_crop_ratio,
)
from light_image_editor.commands import EditCommand, EditTimeline
from light_image_editor.document import Document
from light_image_editor.image_loader import decode_image
from light_image_editor.projects import load_project, save_project
from light_image_editor.renderer import reduce_timeline, render_image


class AnnotationModelTests(unittest.TestCase):
    def test_all_annotation_objects_round_trip_and_remain_editable(self) -> None:
        for kind in (
            "text",
            "arrow",
            "line",
            "rectangle",
            "ellipse",
            "freehand",
            "mosaic",
            "blur",
            "redact",
            "number",
        ):
            annotation = AnnotationObject.create(
                kind,
                (0.1, 0.2, 0.6, 0.7),
                points=[[0.1, 0.2], [0.4, 0.5], [0.6, 0.7]] if kind == "freehand" else [],
                text="示例" if kind == "text" else "",
            )
            restored = AnnotationObject.from_json(annotation.to_json())
            moved = restored.translated(0.1, -0.1)
            resized = moved.resized((0.2, 0.2, 0.8, 0.8))
            self.assertEqual(restored, annotation)
            self.assertEqual(resized.bounds, (0.2, 0.2, 0.8, 0.8))

    def test_hit_testing_respects_tolerance_and_topmost_order(self) -> None:
        bottom = AnnotationObject.create("rectangle", (0.1, 0.1, 0.8, 0.8))
        top = AnnotationObject.create("ellipse", (0.2, 0.2, 0.6, 0.6))
        self.assertEqual(object_at((bottom, top), (0.4, 0.4), 0.01), top)
        self.assertIsNone(object_at((bottom,), (0.95, 0.95), 0.01))

    def test_hit_targets_scale_to_two_hundred_percent_dpi(self) -> None:
        self.assertEqual(hit_radius_px(96), 8)
        self.assertEqual(hit_radius_px(192), 16)

    def test_resizing_reverse_arrow_preserves_its_direction(self) -> None:
        arrow = AnnotationObject.create("arrow", (0.8, 0.7, 0.2, 0.1))

        resized = arrow.resized((0.1, 0.2, 0.9, 0.8))

        self.assertEqual(resized.bounds, (0.1, 0.2, 0.9, 0.8))
        self.assertEqual((resized.x1, resized.y1), (0.9, 0.8))
        self.assertEqual((resized.x2, resized.y2), (0.1, 0.2))

        horizontal = AnnotationObject.create("arrow", (0.2, 0.5, 0.8, 0.5))
        diagonal = horizontal.resized((0.1, 0.2, 0.9, 0.8))
        self.assertEqual((diagonal.x1, diagonal.y1), (0.1, 0.2))
        self.assertEqual((diagonal.x2, diagonal.y2), (0.9, 0.8))

    def test_custom_crop_ratio_accepts_decimal_dimensions(self) -> None:
        ratio = parse_crop_ratio("2.39:1")
        self.assertIsNotNone(ratio)
        assert ratio is not None
        self.assertAlmostEqual(ratio, 2.39)
        self.assertIsNone(parse_crop_ratio("自由"))
        with self.assertRaises(ValueError):
            parse_crop_ratio("3/2")


class AnnotationRendererTests(unittest.TestCase):
    def test_desktop_sized_arrow_uses_antialiased_edge_pixels(self) -> None:
        base = Image.new("RGB", (320, 240), "white")
        arrow = AnnotationObject.create(
            "arrow",
            (0.15, 0.75, 0.82, 0.22),
            color="#ff0000",
            stroke_width=5,
        )
        timeline = EditTimeline()
        timeline.commit(
            EditCommand.create("annotation_add", {"object": arrow.to_json()}, "添加箭头")
        )

        rendered = render_image(base, timeline).convert("RGB")
        extrema = rendered.getextrema()
        self.assertEqual(extrema[0], (255, 255))
        self.assertLess(extrema[1][0], 255)
        green_histogram = rendered.getchannel("G").histogram()
        self.assertTrue(any(green_histogram[1:255]))

    def test_annotation_objects_remain_editable_after_project_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            project = folder / "annotations.lieproj"
            Image.new("RGB", (100, 80), "white").save(source)
            document = Document.from_decoded(decode_image(source), source)
            annotation = AnnotationObject.create("text", (0.1, 0.1, 0.6, 0.3), text="教程步骤")
            document.commit_command(
                EditCommand.create("annotation_add", {"object": annotation.to_json()}, "添加文字")
            )
            save_project(document, project)

            loaded = load_project(project).document
            restored = reduce_timeline(loaded.timeline).annotations[0]
            self.assertEqual(restored, annotation)
            loaded.commit_command(
                EditCommand.create(
                    "annotation_update",
                    {"id": restored.id, "patch": {"text": "仍可编辑"}},
                    "修改文字",
                )
            )
            self.assertEqual(reduce_timeline(loaded.timeline).annotations[0].text, "仍可编辑")

    def test_add_update_delete_are_serializable_non_destructive_commands(self) -> None:
        base = Image.new("RGB", (120, 80), "white")
        annotation = AnnotationObject.create("rectangle", (0.1, 0.1, 0.5, 0.5))
        timeline = EditTimeline()
        timeline.commit(
            EditCommand.create("annotation_add", {"object": annotation.to_json()}, "添加矩形")
        )
        first = render_image(base, timeline)
        timeline.commit(
            EditCommand.create(
                "annotation_update",
                {"id": annotation.id, "patch": {"color": "#0000ff", "opacity": 50}},
                "修改矩形",
            )
        )
        updated = reduce_timeline(timeline).annotations[0]
        self.assertEqual(updated.color, "#0000ff")
        self.assertEqual(updated.opacity, 50)
        timeline.commit(EditCommand.create("annotation_delete", {"id": annotation.id}, "删除矩形"))
        self.assertEqual(render_image(base, timeline).tobytes(), base.tobytes())
        timeline.undo()
        self.assertNotEqual(render_image(base, timeline).tobytes(), base.tobytes())
        self.assertNotEqual(first.tobytes(), base.tobytes())

    def test_mosaic_and_blur_are_local_and_undo_restores_pixels(self) -> None:
        base = Image.effect_noise((160, 120), 80).convert("RGB")
        for kind in ("mosaic", "blur"):
            with self.subTest(kind=kind):
                annotation = AnnotationObject.create(kind, (0.25, 0.25, 0.75, 0.75))
                timeline = EditTimeline()
                timeline.commit(
                    EditCommand.create(
                        "annotation_add", {"object": annotation.to_json()}, f"添加{kind}"
                    )
                )
                rendered = render_image(base, timeline)
                self.assertEqual(
                    rendered.crop((0, 0, 35, 25)).tobytes(), base.crop((0, 0, 35, 25)).tobytes()
                )
                self.assertNotEqual(
                    rendered.crop((50, 40, 110, 80)).tobytes(),
                    base.crop((50, 40, 110, 80)).tobytes(),
                )
                timeline.undo()
                self.assertEqual(render_image(base, timeline).tobytes(), base.tobytes())

    def test_perspective_and_canvas_expansion_are_geometry_commands(self) -> None:
        base = Image.new("RGB", (100, 60), "red")
        timeline = EditTimeline()
        timeline.commit(
            EditCommand.create(
                "perspective",
                {"quad": [0.1, 0.0, 0.9, 0.0, 1.0, 1.0, 0.0, 1.0]},
                "透视校正",
            )
        )
        timeline.commit(
            EditCommand.create(
                "canvas_expand",
                {"margins": [10, 5, 20, 15], "background": "#00ff00"},
                "扩展画布",
            )
        )
        rendered = render_image(base, timeline)
        self.assertEqual(rendered.size, (130, 80))
        self.assertEqual(rendered.getpixel((0, 0)), (0, 255, 0))

    def test_one_hundred_vector_objects_render_within_tkinter_budget(self) -> None:
        base = Image.new("RGB", (1920, 1080), "white")
        timeline = EditTimeline()
        for index in range(100):
            column = index % 10
            row = index // 10
            annotation = AnnotationObject.create(
                "rectangle",
                (
                    column / 10 + 0.01,
                    row / 10 + 0.01,
                    column / 10 + 0.08,
                    row / 10 + 0.08,
                ),
            )
            timeline.commit(
                EditCommand.create("annotation_add", {"object": annotation.to_json()}, "添加矩形")
            )
        started = time.perf_counter()
        rendered = render_image(base, timeline)
        elapsed = time.perf_counter() - started
        self.assertEqual(rendered.size, base.size)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
