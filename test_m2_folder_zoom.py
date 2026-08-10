from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from PIL import Image

from light_image_editor.session import SessionStore
from light_image_editor.settings import SettingsStore
from light_image_editor.shell import create_default_feature_registry
from light_image_editor.ui.main_window import ImageEditorApp


class ViewerFolderAndZoomTests(unittest.TestCase):
    @staticmethod
    def _create_app(directory: str) -> ImageEditorApp:
        root = Path(directory)
        settings = SettingsStore(root / "settings.json")
        settings.save({"schema_version": 1, "onboarding_completed": True})
        app = ImageEditorApp(
            settings_store=settings,
            session_store=SessionStore(root / "session.json"),
        )
        for job in app._startup_jobs:
            app.after_cancel(job)
        app._startup_jobs.clear()
        app.update_idletasks()
        app.update()
        return app

    @staticmethod
    def _close_app(app: ImageEditorApp) -> None:
        if app.winfo_exists():
            app._shutdown_services()
            app.destroy()

    @staticmethod
    def _wait_for_document(app: ImageEditorApp) -> None:
        deadline = time.monotonic() + 5
        while app.document is None and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        if app.document is None:
            raise AssertionError("图片载入超时")

    @staticmethod
    def _wait_for_preview(app: ImageEditorApp, generation: int) -> None:
        deadline = time.monotonic() + 5
        while app.preview_ready_generation == generation and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        if app.preview_ready_generation == generation:
            raise AssertionError("预览刷新超时")

    def test_folder_button_uses_natural_order_and_has_keyboard_feature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (32, 24), "red").save(root / "image10.png")
            Image.new("RGB", (32, 24), "blue").save(root / "image2.png")
            app = self._create_app(directory)
            try:
                with patch(
                    "light_image_editor.ui.main_window.filedialog.askdirectory",
                    return_value=str(root),
                ):
                    app.viewer_open_folder_button.command()
                self._wait_for_document(app)
                self.assertEqual(
                    [path.name for path in app.navigation.files],
                    ["image2.png", "image10.png"],
                )
                self.assertIsNotNone(app.image_path)
                assert app.image_path is not None
                self.assertEqual(app.image_path.name, "image2.png")
                self.assertTrue(app.bind("<Control-Shift-o>"))
                registry = create_default_feature_registry()
                feature = registry.resolve_shortcut("Ctrl+Shift+O")
                self.assertIsNotNone(feature)
                assert feature is not None
                self.assertEqual(feature.id, "viewer.open_folder")
                self.assertTrue(callable(app.resolve_feature_callback(feature.id)))
            finally:
                self._close_app(app)

    def test_empty_folder_and_rejected_unsaved_confirmation_preserve_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (64, 48), "navy").save(source)
            empty = root / "empty"
            empty.mkdir()
            other = root / "other"
            other.mkdir()
            Image.new("RGB", (40, 30), "green").save(other / "other.png")
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_document(app)
                document = app.document
                handoff = app.mode_router.context.asset_id
                with patch("light_image_editor.ui.main_window.messagebox.showwarning"):
                    self.assertFalse(app.open_directory_path(empty, confirm=False))
                self.assertIs(app.document, document)
                self.assertEqual(app.mode_router.context.asset_id, handoff)

                app.rotate(90)
                with patch(
                    "light_image_editor.ui.main_window.messagebox.askyesno",
                    return_value=False,
                ):
                    self.assertFalse(app.open_directory_path(other, confirm=True))
                self.assertIs(app.document, document)
                self.assertTrue(app.document is not None and app.document.dirty)
            finally:
                self._close_app(app)

    def test_bottom_gallery_switches_between_folder_and_favorites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "当前图片.png"
            Image.new("RGB", (64, 48), "navy").save(source)
            Image.new("RGB", (64, 48), "green").save(root / "同目录.png")
            favorite_dir = root / "收藏目录"
            favorite_dir.mkdir()
            favorite = favorite_dir / "收藏图片.png"
            Image.new("RGB", (80, 60), "gold").save(favorite)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_document(app)
                app.viewer_favorite_paths = [str(favorite)]
                app._update_viewer_gallery_button()

                app.viewer_gallery_button.command()
                app.update_idletasks()
                self.assertTrue(app.thumbnail_filmstrip.winfo_ismapped())
                self.assertTrue(app.viewer_gallery_scope_bar.winfo_ismapped())
                self.assertTrue(app.viewer_gallery_folder_button.selected)
                self.assertFalse(app.viewer_gallery_favorites_button.selected)
                self.assertEqual(
                    {path.name for path in app.thumbnail_filmstrip.paths},
                    {"当前图片.png", "同目录.png"},
                )

                app.viewer_gallery_favorites_button.command()
                app.update_idletasks()
                self.assertFalse(app.viewer_gallery_folder_button.selected)
                self.assertTrue(app.viewer_gallery_favorites_button.selected)
                self.assertEqual(app.thumbnail_filmstrip.paths, [favorite])

                self.assertTrue(app._activate_thumbnail(favorite))
                deadline = time.monotonic() + 5
                while app.image_path != favorite.resolve() and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertEqual(app.image_path, favorite.resolve())

                app.toggle_viewer_favorite()
                self.assertEqual(app.thumbnail_filmstrip.paths, [])
                self.assertEqual(app.viewer_favorite_paths, [])
            finally:
                self._close_app(app)

    def test_viewer_ctrl_z_undo_and_redo_keep_viewer_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "撤回测试.png"
            Image.new("RGB", (96, 64), "navy").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_document(app)
                self.assertEqual(app.mode_router.active_mode.value, "viewer")

                app.rotate(90)
                self.assertEqual(app.rotation, 90)
                result = app._invoke_custom_shortcut(
                    "undo",
                    cast(Any, SimpleNamespace(widget=app.canvas)),
                )
                app.update_idletasks()
                self.assertEqual(result, "break")
                self.assertEqual(app.rotation, 0)
                self.assertEqual(app.mode_router.active_mode.value, "viewer")
                self.assertFalse(app.panel_shell.winfo_ismapped())

                app._invoke_custom_shortcut(
                    "redo",
                    cast(Any, SimpleNamespace(widget=app.canvas)),
                )
                app.update_idletasks()
                self.assertEqual(app.rotation, 90)
                self.assertEqual(app.mode_router.active_mode.value, "viewer")
                self.assertFalse(app.panel_shell.winfo_ismapped())
            finally:
                self._close_app(app)

    def test_viewer_light_markup_draws_rectangle_and_arrow_without_overwriting_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "标注原图.png"
            Image.new("RGB", (320, 240), "white").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_document(app)
                deadline = time.monotonic() + 5
                while app.base_preview_image is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app.base_preview_image)

                app.toggle_viewer_markup()
                app.update_idletasks()
                self.assertTrue(app._viewer_markup_active)
                self.assertTrue(app.viewer_markup_bar.winfo_ismapped())
                self.assertTrue(app.dock_markup_button.selected)
                self.assertEqual(app._viewer_markup_tool, "rectangle")

                def event_at(nx: float, ny: float) -> SimpleNamespace:
                    return SimpleNamespace(
                        x=round(
                            app.preview_offset[0] + nx * source_image_width * app.preview_scale
                        ),
                        y=round(
                            app.preview_offset[1] + ny * source_image_height * app.preview_scale
                        ),
                    )

                source_image_width, source_image_height = (320, 240)
                rectangle_start = event_at(0.18, 0.20)
                rectangle_end = event_at(0.58, 0.62)
                app._canvas_press(cast(Any, rectangle_start))
                app._canvas_drag(cast(Any, rectangle_end))
                self.assertIsNone(app._annotation_preview_job)
                app._canvas_release(cast(Any, rectangle_end))
                self.assertEqual(
                    [annotation.kind for annotation in app._annotation_objects()],
                    ["rectangle"],
                )
                self.assertEqual(app._viewer_markup_tool, "rectangle")

                app._set_viewer_markup_tool("arrow")
                arrow_start = event_at(0.25, 0.76)
                arrow_end = event_at(0.72, 0.30)
                app._canvas_press(cast(Any, arrow_start))
                app._canvas_drag(cast(Any, arrow_end))
                live_arrow_items = app.canvas.find_withtag("annotation-ui")
                self.assertTrue(live_arrow_items)
                app._canvas_release(cast(Any, arrow_end))
                self.assertEqual(
                    app.canvas.find_withtag("annotation-ui"),
                    live_arrow_items,
                )
                self.assertEqual(
                    [annotation.kind for annotation in app._annotation_objects()],
                    ["rectangle", "arrow"],
                )

                app.viewer_markup_undo_button.command()
                self.assertEqual(
                    [annotation.kind for annotation in app._annotation_objects()],
                    ["rectangle"],
                )
                app.viewer_markup_cancel_button.command()
                app.update_idletasks()
                self.assertFalse(app._viewer_markup_active)
                self.assertFalse(app.viewer_markup_bar.winfo_ismapped())
                self.assertFalse(app.dock_markup_button.selected)
                self.assertEqual(app._annotation_objects(), ())

                with Image.open(source) as unchanged:
                    self.assertEqual(unchanged.getpixel((10, 10)), (255, 255, 255))
            finally:
                self._close_app(app)

    def test_viewer_markup_copy_restores_session_and_replace_writes_source_with_backup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "操作选择.png"
            Image.new("RGB", (320, 240), "white").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_document(app)
                deadline = time.monotonic() + 5
                while app.base_preview_image is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                def event_at(nx: float, ny: float) -> SimpleNamespace:
                    return SimpleNamespace(
                        x=round(app.preview_offset[0] + nx * 320 * app.preview_scale),
                        y=round(app.preview_offset[1] + ny * 240 * app.preview_scale),
                    )

                app.toggle_viewer_markup()
                start = event_at(0.15, 0.18)
                end = event_at(0.62, 0.66)
                app._canvas_press(cast(Any, start))
                app._canvas_drag(cast(Any, end))
                app._canvas_release(cast(Any, end))
                with patch(
                    "light_image_editor.ui.main_window.copy_image_to_clipboard"
                ) as clipboard_copy:
                    app.viewer_markup_copy_button.command()
                clipboard_copy.assert_called_once()
                self.assertFalse(app._viewer_markup_active)
                self.assertEqual(app._annotation_objects(), ())
                with Image.open(source) as unchanged:
                    self.assertEqual(unchanged.getpixel((10, 10)), (255, 255, 255))

                app.toggle_viewer_markup()
                app._set_viewer_markup_tool("arrow")
                start = event_at(0.20, 0.75)
                end = event_at(0.78, 0.28)
                app._canvas_press(cast(Any, start))
                app._canvas_drag(cast(Any, end))
                app._canvas_release(cast(Any, end))
                with patch(
                    "light_image_editor.ui.main_window.messagebox.askyesno",
                    return_value=True,
                ):
                    app.viewer_markup_replace_button.command()
                self.assertFalse(app._viewer_markup_active)
                self.assertTrue(source.with_name(source.name + ".bak").exists())
                with Image.open(source) as replaced:
                    self.assertNotEqual(
                        replaced.convert("RGB").getextrema(),
                        ((255, 255), (255, 255), (255, 255)),
                    )
            finally:
                self._close_app(app)

    def test_zoom_anchor_math_and_continuous_wheel_keep_source_pixel_fixed(self) -> None:
        pan = ImageEditorApp._anchored_pan_offset(
            anchor=(200, 150),
            old_offset=(0, 0),
            old_scale=0.5,
            new_scale=1.0,
            content_size=(1600, 1200),
            canvas_size=(800, 600),
        )
        self.assertEqual(pan, (200, 150))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large.png"
            Image.new("RGB", (1200, 900), "#735f9e").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_document(app)
                generation = app.preview_ready_generation
                app.set_zoom("100%")
                self._wait_for_preview(app, generation)
                anchor = (app.canvas.winfo_width() // 2 + 80, app.canvas.winfo_height() // 2)
                before = (
                    (anchor[0] - app.preview_offset[0]) / app.preview_scale,
                    (anchor[1] - app.preview_offset[1]) / app.preview_scale,
                )
                generation = app.preview_ready_generation
                app._set_zoom_percent(150, anchor=anchor)
                self._wait_for_preview(app, generation)
                after = (
                    (anchor[0] - app.preview_offset[0]) / app.preview_scale,
                    (anchor[1] - app.preview_offset[1]) / app.preview_scale,
                )
                self.assertAlmostEqual(before[0], after[0], delta=1.0)
                self.assertAlmostEqual(before[1], after[1], delta=1.0)
                self.assertIn("150%", app.canvas_metadata.cget("text"))

                generation = app.preview_ready_generation
                event = cast(Any, SimpleNamespace(delta=-120, x=anchor[0], y=anchor[1]))
                self.assertEqual(app._zoom_with_wheel(event), "break")
                self._wait_for_preview(app, generation)
                self.assertEqual(app.zoom_mode.get(), "136%")
                self.assertIn("136%", app.status_var.get())
            finally:
                self._close_app(app)

    def test_viewer_left_drag_moves_cached_preview_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "pan.png"
            Image.new("RGB", (1800, 1200), "#274060").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_document(app)
                generation = app.preview_ready_generation
                app.set_zoom("200%")
                self._wait_for_preview(app, generation)

                start = cast(
                    Any,
                    SimpleNamespace(
                        x=app.canvas.winfo_width() // 2,
                        y=app.canvas.winfo_height() // 2,
                    ),
                )
                app._canvas_press(start)
                before_offset = app.preview_offset
                before_generation = app._preview_generation
                app._canvas_drag(cast(Any, SimpleNamespace(x=start.x + 120, y=start.y + 70)))

                self.assertNotEqual(app.preview_offset, before_offset)
                self.assertEqual(app._preview_generation, before_generation)
                app._canvas_release(cast(Any, SimpleNamespace(x=start.x + 120, y=start.y + 70)))
                self.assertIsNone(app.pan_start)
                self.assertEqual(app._preview_generation, before_generation)

                generation = app.preview_ready_generation
                app.reset_view()
                self._wait_for_preview(app, generation)
                self.assertEqual(app._viewer_pan_axes(), (False, False))
                fitted_offset = app.preview_offset
                app._canvas_press(start)
                self.assertIsNone(app.pan_start)
                app._canvas_drag(cast(Any, SimpleNamespace(x=start.x + 120, y=start.y + 70)))
                self.assertEqual(app.preview_offset, fitted_offset)
                self.assertEqual(str(app.canvas.cget("cursor")), "arrow")
            finally:
                self._close_app(app)

    def test_viewer_resize_defers_expensive_preview_until_drag_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resize.png"
            Image.new("RGB", (1200, 800), "#203044").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_document(app)
                if app._preview_job is not None:
                    app.after_cancel(app._preview_job)
                    app._preview_job = None
                first = cast(
                    Any,
                    SimpleNamespace(
                        widget=app.canvas,
                        width=app.canvas.winfo_width() - 10,
                        height=app.canvas.winfo_height() - 10,
                    ),
                )
                second = cast(
                    Any,
                    SimpleNamespace(
                        widget=app.canvas,
                        width=app.canvas.winfo_width() - 20,
                        height=app.canvas.winfo_height() - 20,
                    ),
                )
                generation = app._preview_generation
                with patch.object(app, "_refresh_preview") as refresh:
                    app._on_configure(first)
                    app._on_configure(second)
                    self.assertTrue(app._viewer_resize_preview_pending)
                    self.assertIsNone(app._preview_job)
                    self.assertEqual(app._preview_generation, generation + 1)
                    refresh.assert_not_called()
                    app._finish_window_resize()
                    refresh.assert_called_once_with()
                    self.assertFalse(app._viewer_resize_preview_pending)
            finally:
                self._close_app(app)


if __name__ == "__main__":
    unittest.main()
