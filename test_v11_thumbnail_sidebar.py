from __future__ import annotations

import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from typing import cast
from unittest.mock import Mock, patch

from PIL import Image, ImageChops

from image_ops import EditState
from light_image_editor.annotations import AnnotationObject
from light_image_editor.commands import EditCommand, EditTimeline
from light_image_editor.presets import resolve_size_preset
from light_image_editor.preview import render_preview
from light_image_editor.renderer import reduce_timeline, render_image
from light_image_editor.session import SessionStore
from light_image_editor.settings import SettingsStore
from light_image_editor.thumbnails import THUMBNAIL_SIZE, ThumbnailLoader, generate_thumbnail
from light_image_editor.ui.main_window import (
    ZOOM_LEVELS,
    ImageEditorApp,
    _timeline_with_annotation_draft,
)


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


class InteractionLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.callback_errors: list[BaseException] = []

        def record_callback_error(
            _app: ImageEditorApp,
            _exception_type: type[BaseException],
            exception: BaseException,
            _traceback_value: object,
        ) -> None:
            self.callback_errors.append(exception)

        self._callback_patcher = patch.object(
            ImageEditorApp,
            "report_callback_exception",
            new=record_callback_error,
        )
        self._callback_patcher.start()

    def tearDown(self) -> None:
        self._callback_patcher.stop()
        if self.callback_errors:
            self.fail(f"unexpected Tk callback errors: {self.callback_errors!r}")

    def test_percentage_size_presets_always_use_original_dimensions(self) -> None:
        original_size = (1080, 608)
        first = resolve_size_preset(original_size, "50")
        second = resolve_size_preset(original_size, "50")
        quarter = resolve_size_preset(original_size, "25")

        self.assertEqual(first, (540, 304, None))
        self.assertEqual(second, first)
        self.assertEqual(quarter, (270, 152, None))
        self.assertEqual(resolve_size_preset(original_size, "original"), (1080, 608, None))
        self.assertEqual(resolve_size_preset(original_size, "1920"), (None, None, 1920))

    def test_transient_annotation_drag_renders_without_mutating_history(self) -> None:
        base = Image.new("RGB", (240, 120), "white")
        original = AnnotationObject.create(
            "arrow",
            (0.1, 0.2, 0.35, 0.4),
            color="#ff0000",
            stroke_width=8,
        )
        timeline = EditTimeline()
        timeline.commit(
            EditCommand.create(
                "annotation_add",
                {"object": original.to_json()},
                "添加箭头",
            )
        )
        draft = original.translated(0.4, 0.3)
        history_before = (
            timeline.active_signature(),
            timeline.cursor,
            timeline.revision,
            len(timeline.commands),
        )

        transient = _timeline_with_annotation_draft(timeline, draft, original)
        transient_image = render_image(base, transient)
        expected = EditTimeline.from_json(timeline.to_json())
        patch_data = draft.to_json()
        patch_data.pop("id")
        patch_data.pop("kind")
        expected.commit(
            EditCommand.create(
                "annotation_update",
                {"id": original.id, "patch": patch_data},
                "调整箭头",
            )
        )
        expected_image = render_image(base, expected)
        try:
            self.assertEqual(transient_image.tobytes(), expected_image.tobytes())
            self.assertEqual(reduce_timeline(transient).annotations, (draft,))
            self.assertEqual(reduce_timeline(timeline).annotations, (original,))
            self.assertEqual(
                (
                    timeline.active_signature(),
                    timeline.cursor,
                    timeline.revision,
                    len(timeline.commands),
                ),
                history_before,
            )
        finally:
            transient_image.close()
            expected_image.close()
            base.close()

    def test_transient_creation_preview_covers_vector_text_and_region_tools(self) -> None:
        base = Image.effect_noise((180, 120), 80).convert("RGB")
        try:
            for kind in ("arrow", "rectangle", "text", "mosaic", "blur"):
                with self.subTest(kind=kind):
                    timeline = EditTimeline()
                    draft = AnnotationObject.create(
                        kind,
                        (0.2, 0.2, 0.75, 0.75),
                        text="实时文字" if kind == "text" else "",
                        color="#ff0000",
                        stroke_width=8,
                    )
                    transient = _timeline_with_annotation_draft(timeline, draft, None)
                    rendered = render_image(base, transient)
                    try:
                        self.assertEqual(reduce_timeline(transient).annotations, (draft,))
                        self.assertEqual(reduce_timeline(timeline).annotations, ())
                        self.assertEqual((timeline.cursor, timeline.revision), (0, 0))
                        self.assertNotEqual(rendered.tobytes(), base.tobytes())
                    finally:
                        rendered.close()
        finally:
            base.close()

    def test_split_compare_contains_two_complete_images(self) -> None:
        original = Image.new("RGB", (100, 60), "red")
        frame = render_preview(
            original,
            EditState(grayscale=True),
            compare_original=False,
            split_compare=True,
            canvas_size=(240, 100),
            zoom_percent=None,
        )
        try:
            self.assertIsNotNone(frame.split_layout)
            assert frame.split_layout is not None
            before = frame.split_layout.before_panel
            after = frame.split_layout.after_panel
            self.assertEqual(before, (0, 0, 100, 60))
            self.assertEqual(after, (108, 0, 208, 60))
            self.assertEqual(frame.image.getpixel((50, 30)), (255, 0, 0))
            after_pixel = cast(tuple[int, ...], frame.image.getpixel((158, 30)))
            self.assertEqual(after_pixel[0], after_pixel[1])
            self.assertEqual(after_pixel[1], after_pixel[2])
        finally:
            frame.close()
            original.close()

    def test_real_ui_keeps_quick_percentage_fixed_and_shows_two_split_panels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            Image.new("RGB", (1080, 608), "red").save(source)
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                for job in app._startup_jobs:
                    app.after_cancel(job)
                app._startup_jobs.clear()
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app.image_path is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app.image_path)

                app.apply_size_preset("50")
                self.assertEqual((app.width_var.get(), app.height_var.get()), ("540", "304"))
                app.apply_size_preset("50")
                self.assertEqual((app.width_var.get(), app.height_var.get()), ("540", "304"))

                app.split_compare_var.set(True)
                app._toggle_split_compare()
                generation = app.preview_ready_generation
                deadline = time.monotonic() + 5
                while app.preview_ready_generation == generation and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                labels = {
                    str(app.canvas.itemcget(item, "text"))
                    for item in app.canvas.find_all()
                    if app.canvas.type(item) == "text"
                }
                self.assertIn("编辑前", labels)
                self.assertIn("编辑后", labels)
                self.assertFalse(app.compare_original.get())
            finally:
                if app.winfo_exists():
                    app._shutdown_services()
                    app.destroy()

    def test_zoom_combobox_popdown_and_fit_selection_do_not_raise_callback_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            zoom_box: ttk.Combobox | None = None
            try:
                self._cancel_startup_jobs(app)
                app.update()
                zoom_box = next(
                    widget
                    for widget in app.toolbar.winfo_children()
                    if isinstance(widget, ttk.Combobox)
                    and tuple(widget.cget("values")) == ZOOM_LEVELS
                )
                app.set_zoom("100%")
                app.pan_offset = (11, 13)

                app.tk.call("ttk::combobox::Post", str(zoom_box))
                app.update()
                zoom_box.current(0)
                zoom_box.event_generate("<<ComboboxSelected>>")
                app.update()

                self.assertEqual(app.zoom_mode.get(), "适应")
                self.assertEqual(app.pan_offset, (0, 0))
                self.assertEqual(self.callback_errors, [])
            finally:
                if zoom_box is not None:
                    try:
                        app.tk.call("ttk::combobox::Unpost", str(zoom_box))
                    except tk.TclError:
                        pass
                self._close_app(app)

    def test_size_preset_uses_rotated_and_cropped_content_before_resize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            Image.new("RGB", (1080, 608), "red").save(source)
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                self._cancel_startup_jobs(app)
                self._open_and_wait(app, source)

                app.rotate(90)
                app.apply_size_preset("50")
                self.assertEqual((app.width_var.get(), app.height_var.get()), ("304", "540"))
                app.apply_size_preset("50")
                self.assertEqual((app.width_var.get(), app.height_var.get()), ("304", "540"))

                assert app.document is not None
                app._commit_command(
                    EditCommand.create(
                        "crop",
                        {"box": [0.0, 0.0, 0.5, 1.0]},
                        "test crop",
                    )
                )
                app.apply_size_preset("50")
                self.assertEqual((app.width_var.get(), app.height_var.get()), ("152", "540"))
            finally:
                self._close_app(app)

    def test_batch_options_work_without_an_open_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                self._cancel_startup_jobs(app)
                app.format_var.set("PNG")
                app.max_side_var.set("1600")
                options = app._read_options(allow_without_image=True)
                self.assertIsNotNone(options)
                assert options is not None
                self.assertEqual(options.output_format, "PNG")
                self.assertEqual((options.max_width, options.max_height), (1600, 1600))
            finally:
                self._close_app(app)

    def test_quick_export_does_not_clear_unsaved_edit_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            Image.new("RGB", (64, 32), "red").save(source)
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                self._cancel_startup_jobs(app)
                self._open_and_wait(app, source)
                app.rotate(90)
                self.assertTrue(app.has_unsaved_changes())
                app.recent_export_directories = [str(root)]
                with (
                    patch.object(app.session_store, "clear") as clear,
                    patch.object(
                        app,
                        "_save_document_image",
                        side_effect=lambda target, _options: target,
                    ),
                ):
                    app.quick_export()
                    self.assertTrue(app.has_unsaved_changes())
                    clear.assert_not_called()
            finally:
                self._close_app(app)

    def test_failed_load_clears_previous_document_and_file_actions_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = root / "good.png"
            damaged = root / "damaged.png"
            Image.new("RGB", (32, 16), "red").save(good)
            damaged.write_bytes(b"not an image")
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                self._cancel_startup_jobs(app)
                self._open_and_wait(app, good)
                with patch("light_image_editor.ui.main_window.messagebox.showwarning"):
                    app.open_path(damaged, confirm=False)
                    deadline = time.monotonic() + 5
                    while app._last_failed_path is None and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.01)
                self.assertEqual(app._last_failed_path, damaged.resolve())
                self.assertIsNone(app.document)
                self.assertIsNone(app.original)
                self.assertIsNone(app.image_path)
            finally:
                self._close_app(app)

    def test_clipboard_and_recovery_paths_keep_sidebar_in_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            Image.new("RGB", (24, 12), "red").save(source)
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                self._cancel_startup_jobs(app)
                self._open_and_wait(app, source)
                self.assertTrue(app.thumbnail_sidebar.paths)
                clipboard_image = Image.new("RGB", (10, 10), "blue")
                try:
                    with patch(
                        "light_image_editor.ui.main_window.ImageGrab.grabclipboard",
                        return_value=clipboard_image,
                    ):
                        app.paste_from_clipboard()
                finally:
                    clipboard_image.close()
                self.assertEqual(app.thumbnail_sidebar.paths, [])
                self.assertIsNone(app.image_path)
            finally:
                self._close_app(app)

    def test_text_input_guards_paste_apply_and_space_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                self._cancel_startup_jobs(app)
                action = Mock()
                with patch.object(app, "_text_input_has_focus", return_value=True):
                    self.assertIsNone(app._guarded_text_key_action(action))
                    self.assertIsNone(app._guarded_compare_key(True))
                action.assert_not_called()
                self.assertFalse(app.compare_original.get())
            finally:
                self._close_app(app)

    def test_single_point_freehand_preview_does_not_raise_tk_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            Image.new("RGB", (32, 16), "red").save(source)
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                self._cancel_startup_jobs(app)
                self._open_and_wait(app, source)
                assert app.original is not None
                app.base_preview_image = app.original
                app._base_preview_owned = False
                app._annotation_drag_object = AnnotationObject.create(
                    "freehand",
                    (0.5, 0.5, 0.5, 0.5),
                    points=[[0.5, 0.5]],
                )
                app._draw_annotation_overlay()
                self.assertTrue(app.canvas.find_withtag("annotation-ui"))
            finally:
                self._close_app(app)

    def test_annotation_drag_follows_motion_and_commits_once_on_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            Image.new("RGB", (240, 120), "white").save(source)
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                self._cancel_startup_jobs(app)
                self._open_and_wait(app, source)
                deadline = time.monotonic() + 5
                while app.base_preview_image is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app.base_preview_image)
                assert app.document is not None

                arrow = AnnotationObject.create(
                    "arrow",
                    (0.1, 0.2, 0.3, 0.4),
                    color="#ff0000",
                    stroke_width=8,
                )
                self.assertTrue(
                    app._commit_command(
                        EditCommand.create(
                            "annotation_add",
                            {"object": arrow.to_json()},
                            "添加箭头",
                        ),
                        refresh=False,
                    )
                )
                initial_generation = app.preview_ready_generation
                app._refresh_preview()
                deadline = time.monotonic() + 5
                while (
                    app.preview_ready_generation <= initial_generation
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)
                self.assertGreater(app.preview_ready_generation, initial_generation)
                assert app.base_preview_image is not None
                initial_pixel = cast(tuple[int, ...], app.base_preview_image.getpixel((48, 36)))
                self.assertEqual(initial_pixel[:3], (255, 0, 0))
                app._set_annotation_tool("select")
                app.selected_annotation_id = arrow.id
                app._load_annotation_style(arrow)
                app._annotation_start = (0.1, 0.2)
                app._annotation_original_object = arrow
                app._annotation_drag_object = arrow
                app._annotation_drag_action = "move"
                history_before = (
                    app.document.timeline.cursor,
                    app.document.timeline.revision,
                    len(app.document.timeline.commands),
                )

                with patch.object(
                    app,
                    "_canvas_to_normalized",
                    side_effect=[(0.3, 0.4), (0.4, 0.5), (0.5, 0.6)],
                ):
                    for _ in range(3):
                        app._drag_annotation_selection(Mock(x=0, y=0))

                draft = app._annotation_drag_object
                self.assertIsNotNone(draft)
                assert draft is not None
                self.assertEqual(draft.bounds, (0.5, 0.6, 0.7, 0.8))
                self.assertIsNotNone(app._annotation_preview_job)
                preview_timeline = app._timeline_for_preview()
                self.assertIsNotNone(preview_timeline)
                assert preview_timeline is not None
                self.assertEqual(reduce_timeline(preview_timeline).annotations, (draft,))
                self.assertEqual(reduce_timeline(app.document.timeline).annotations, (arrow,))
                self.assertEqual(
                    (
                        app.document.timeline.cursor,
                        app.document.timeline.revision,
                        len(app.document.timeline.commands),
                    ),
                    history_before,
                )
                live_lines = [
                    item
                    for item in app.canvas.find_withtag("annotation-ui")
                    if app.canvas.type(item) == "line"
                ]
                self.assertTrue(live_lines)
                self.assertTrue(
                    any(app.canvas.itemcget(item, "arrow") == "last" for item in live_lines)
                )
                drag_generation = app.preview_ready_generation
                deadline = time.monotonic() + 5
                live_pixels: tuple[tuple[int, ...], tuple[int, ...]] | None = None
                while time.monotonic() < deadline:
                    app.update()
                    if app.base_preview_image is not None:
                        old_pixel = cast(tuple[int, ...], app.base_preview_image.getpixel((48, 36)))
                        new_pixel = cast(
                            tuple[int, ...], app.base_preview_image.getpixel((144, 84))
                        )
                        if old_pixel[:3] == (255, 255, 255) and new_pixel[:3] == (255, 0, 0):
                            live_pixels = (old_pixel, new_pixel)
                            break
                    time.sleep(0.01)
                self.assertGreater(app.preview_ready_generation, drag_generation)
                self.assertIsNotNone(live_pixels)

                with patch.object(app, "_refresh_preview") as refresh:
                    app._finish_annotation_selection(Mock())
                refresh.assert_called_once_with()
                self.assertEqual(
                    (
                        app.document.timeline.cursor,
                        app.document.timeline.revision,
                        len(app.document.timeline.commands),
                    ),
                    (history_before[0] + 1, history_before[1] + 1, history_before[2] + 1),
                )
                self.assertEqual(reduce_timeline(app.document.timeline).annotations, (draft,))

                committed_count = len(app.document.timeline.commands)
                with patch.object(app, "_refresh_preview"):
                    app._finish_annotation_selection(Mock())
                self.assertEqual(len(app.document.timeline.commands), committed_count)
                app.document.timeline.undo()
                self.assertEqual(reduce_timeline(app.document.timeline).annotations, (arrow,))
                app.document.timeline.redo()
                self.assertEqual(reduce_timeline(app.document.timeline).annotations, (draft,))
            finally:
                self._close_app(app)

    def test_fullscreen_relaxes_window_constraints_and_handles_tk_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            settings.save({"schema_version": 1, "onboarding_completed": True})
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            app.withdraw()
            try:
                self._cancel_startup_jobs(app)
                app.minsize(1040, 680)
                app.maxsize(800, 600)
                app.toggle_fullscreen()
                self.assertTrue(app._is_fullscreen)
                app.toggle_fullscreen()
                self.assertFalse(app._is_fullscreen)

                with patch.object(
                    app,
                    "attributes",
                    side_effect=tk.TclError("fullscreen unavailable"),
                ):
                    app.toggle_fullscreen()
                self.assertFalse(app._is_fullscreen)
                self.assertIn("无法切换全屏", app.status_var.get())
            finally:
                self._close_app(app)

    def test_corrupted_numeric_settings_fall_back_instead_of_crashing_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            settings.save(
                {
                    "schema_version": 1,
                    "onboarding_completed": True,
                    "decode_workers": "broken",
                    "preview_cache": None,
                    "quality": {},
                    "watermark_opacity": "NaN",
                    "watermark_size_percent": [],
                    "slideshow_interval": "Infinity",
                    "batch_concurrency": "none",
                    "batch_memory_mb": {},
                    "recent_files": 7,
                }
            )
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                self._cancel_startup_jobs(app)
                self.assertEqual(app.decode_workers_var.get(), 2)
                self.assertEqual(app.preview_cache_var.get(), 3)
                self.assertEqual(app.quality_var.get(), 85)
                self.assertEqual(app.watermark_opacity_var.get(), 35)
                self.assertEqual(app.watermark_size_var.get(), 5)
                self.assertEqual(app.slideshow_interval_var.get(), 3.0)
                self.assertEqual(app.batch_concurrency_var.get(), 2)
                self.assertEqual(app.batch_memory_var.get(), 512)
                self.assertEqual(app.recent_files, [])
            finally:
                self._close_app(app)

    @staticmethod
    def _cancel_startup_jobs(app: ImageEditorApp) -> None:
        for job in app._startup_jobs:
            app.after_cancel(job)
        app._startup_jobs.clear()

    @staticmethod
    def _open_and_wait(app: ImageEditorApp, source: Path) -> None:
        app.open_path(source, confirm=False)
        deadline = time.monotonic() + 5
        while app.image_path is None and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        if app.image_path is None:
            raise AssertionError(f"timed out loading {source}")

    @staticmethod
    def _close_app(app: ImageEditorApp) -> None:
        if app.winfo_exists():
            app._shutdown_services()
            app.destroy()

    def test_split_compare_preserves_aspect_ratio_after_rotation(self) -> None:
        original = Image.new("RGB", (100, 60), "red")
        frame = render_preview(
            original,
            EditState(rotation=90),
            compare_original=False,
            split_compare=True,
            canvas_size=(240, 120),
            zoom_percent=None,
        )
        try:
            self.assertEqual(frame.image.size, (208, 100))
            self.assertEqual(frame.image.getpixel((50, 20)), (255, 0, 0))
            self.assertEqual(frame.image.getpixel((128, 50)), (255, 0, 0))
            self.assertEqual(frame.image.getpixel((8, 0)), (31, 35, 43))
            self.assertEqual(frame.image.getpixel((108, 8)), (31, 35, 43))
        finally:
            frame.close()
            original.close()


if __name__ == "__main__":
    unittest.main()
