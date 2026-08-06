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


if __name__ == "__main__":
    unittest.main()
