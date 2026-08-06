from __future__ import annotations

import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from image_ops import ExportOptions
from light_image_editor.domain import (
    ConversionTaskStatus,
    ExportProfile,
    RecipeStore,
    TransformRecipe,
    TransformStep,
)
from light_image_editor.session import SessionStore
from light_image_editor.settings import SettingsStore
from light_image_editor.shell import ModeId
from light_image_editor.ui.main_window import ImageEditorApp


class ConversionWorkspaceIntegrationTests(unittest.TestCase):
    @staticmethod
    def _create_app(directory: str) -> ImageEditorApp:
        root = Path(directory)
        settings = SettingsStore(root / "settings.json")
        settings.save(
            {
                "schema_version": 1,
                "onboarding_completed": True,
                "theme_mode": "light",
                "thumbnail_sidebar_visible": True,
                "edit_panel_visible": True,
            }
        )
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
    def _pump_until(
        app: ImageEditorApp,
        predicate: Callable[[], bool],
        timeout: float = 8.0,
    ) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        if not predicate():
            raise AssertionError("等待界面状态超时")

    @staticmethod
    def _close_app(app: ImageEditorApp) -> None:
        if app.winfo_exists():
            app._shutdown_services()
            app.destroy()

    def test_convert_mode_uses_real_queue_without_hiding_legacy_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "当前图片.png"
            second = root / "第二张.png"
            Image.new("RGB", (48, 32), "#7f6a9b").save(source)
            Image.new("RGB", (32, 48), "#6a9b82").save(second)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._pump_until(app, lambda: app.document is not None)
                app.mode_buttons[ModeId.CONVERT].command()
                app.update_idletasks()

                self.assertTrue(app.conversion_workspace.winfo_ismapped())
                self.assertFalse(app.thumbnail_sidebar.winfo_ismapped())
                self.assertTrue(app.canvas_frame.winfo_ismapped())
                self.assertTrue(app.panel_shell.winfo_ismapped())
                self.assertTrue(app.conversion_job_box.winfo_ismapped())
                self.assertTrue(app.resize_box.winfo_ismapped())
                self.assertTrue(app.export_box.winfo_ismapped())
                self.assertEqual(len(app.conversion_workspace.items), 1)
                queued = app.conversion_workspace.items[0]
                self.assertEqual(queued.path, source.resolve())
                self.assertIsNotNone(queued.asset)
                self.assertIsNotNone(queued.revision)

                app.conversion_workspace.add_files((second,))
                self.assertEqual(len(app.conversion_workspace.items), 2)
                app.mode_buttons[ModeId.VIEWER].command()
                app.update_idletasks()
                self.assertFalse(app.conversion_workspace.winfo_ismapped())
                self.assertTrue(app.thumbnail_sidebar.winfo_ismapped())

                app.mode_buttons[ModeId.CONVERT].command()
                app.update_idletasks()
                self.assertTrue(app.conversion_workspace.winfo_ismapped())
                self.assertEqual(len(app.conversion_workspace.items), 2)
            finally:
                self._close_app(app)

    def test_advanced_format_controls_are_contextual_and_update_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            try:
                app.mode_buttons[ModeId.CONVERT].command()

                app.format_var.set("WEBP")
                app._format_changed()
                self.assertTrue(app.webp_lossless_check.instate(("!disabled",)))
                self.assertEqual(str(app.png_palette_box.cget("state")), "disabled")
                app.target_size_var.set("80")
                app.webp_lossless_var.set(True)
                app._advanced_format_changed()
                self.assertEqual(app.target_size_var.get(), "")
                self.assertTrue(app.target_size_entry.instate(("disabled",)))
                self.assertTrue(app.conversion_draft_history.current.export_profile.webp_lossless)

                app.format_var.set("PNG")
                app._format_changed()
                self.assertFalse(app.webp_lossless_var.get())
                self.assertEqual(str(app.png_palette_box.cget("state")), "readonly")
                app.png_palette_var.set("64 色")
                app._advanced_format_changed()
                self.assertEqual(
                    app.conversion_draft_history.current.export_profile.png_palette_colors,
                    64,
                )

                app.format_var.set("TIFF")
                app._format_changed()
                self.assertEqual(str(app.tiff_compression_box.cget("state")), "readonly")
                app.tiff_compression_var.set("Deflate")
                app._advanced_format_changed()
                profile = app.conversion_draft_history.current.export_profile
                self.assertEqual(profile.output_format, "TIFF")
                self.assertEqual(profile.tiff_compression, "tiff_adobe_deflate")
            finally:
                self._close_app(app)

    def test_conversion_edits_and_undo_never_mutate_the_edit_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "隔离测试.png"
            Image.new("RGB", (80, 60), "#78658f").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._pump_until(app, lambda: app.document is not None)
                app.mode_buttons[ModeId.EDIT].command()

                # Establish a non-default edit document so returning from conversion
                # has observable state to restore into the shared controls.
                app.width_var.set("36")
                app.height_var.set("24")
                app.watermark_text_var.set("改图水印")
                app.rotate(90)
                document = app.document
                self.assertIsNotNone(document)
                assert document is not None
                document.mark_clean()
                self.assertFalse(document.dirty)
                document_state = document.state
                document_timeline = document.timeline.to_json()
                document_base_revision = document.base_revision
                document_dirty = document.dirty
                document_operations = tuple(document.operations)

                def assert_document_unchanged() -> None:
                    self.assertEqual(document.timeline.to_json(), document_timeline)
                    self.assertEqual(document.base_revision, document_base_revision)
                    self.assertEqual(document.dirty, document_dirty)
                    self.assertEqual(document.state, document_state)
                    self.assertEqual(tuple(document.operations), document_operations)

                app.mode_buttons[ModeId.CONVERT].command()
                app.rotate(90)
                self.assertEqual(app.conversion_draft_history.current.rotation, 90)
                assert_document_unchanged()

                app._syncing_document = True
                try:
                    app.width_var.set("20")
                    app.height_var.set("10")
                    app.max_side_var.set("")
                finally:
                    app._syncing_document = False
                app._update_conversion_draft_from_controls()
                resize = app.conversion_draft_history.current.resize_params
                self.assertEqual((resize["exact_width"], resize["exact_height"]), (20, 10))
                assert_document_unchanged()

                app._syncing_document = True
                try:
                    app.watermark_text_var.set("转图水印")
                    app.watermark_position_var.set("左上")
                    app.watermark_opacity_var.set(52)
                    app.watermark_size_var.set(7)
                finally:
                    app._syncing_document = False
                app._update_conversion_draft_from_controls()
                watermark = app.conversion_draft_history.current.watermark_params
                self.assertEqual(watermark["text"], "转图水印")
                self.assertEqual(watermark["position"], "左上")
                assert_document_unchanged()

                app.undo()
                self.assertEqual(app.conversion_draft_history.current.watermark_params, {})
                self.assertEqual(app.width_var.get(), "20")
                self.assertEqual(app.height_var.get(), "10")
                assert_document_unchanged()

                app.mode_buttons[ModeId.EDIT].command()
                app.update_idletasks()
                self.assertEqual(app.rotation, 90)
                self.assertEqual(app.width_var.get(), "36")
                self.assertEqual(app.height_var.get(), "24")
                self.assertEqual(app.watermark_text_var.get(), "改图水印")
                self.assertEqual(
                    app.watermark_position_var.get(),
                    document_state.watermark_position,
                )
                self.assertEqual(app.watermark_opacity_var.get(), document_state.watermark_opacity)
                assert_document_unchanged()
            finally:
                self._close_app(app)

    def test_running_state_freezes_and_restores_conversion_queue_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "冻结队列.png"
            Image.new("RGB", (40, 30), "#668096").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._pump_until(app, lambda: app.document is not None)
                app.mode_buttons[ModeId.CONVERT].command()
                workspace = app.conversion_workspace
                self.assertEqual(len(workspace.items), 1)
                self.assertIsNotNone(workspace.selected_input)

                workspace.set_running(True)
                self.assertTrue(workspace.tree.instate(("disabled",)))
                for button in (
                    workspace.add_files_button,
                    workspace.add_folder_button,
                    workspace.toggle_button,
                    workspace.remove_button,
                    workspace.clear_button,
                ):
                    self.assertIn("disabled", button.state())
                workspace.remove_button.invoke()
                self.assertEqual(len(workspace.items), 1)

                workspace.set_running(False)
                self.assertFalse(workspace.tree.instate(("disabled",)))
                for button in (
                    workspace.add_files_button,
                    workspace.add_folder_button,
                    workspace.toggle_button,
                    workspace.remove_button,
                    workspace.clear_button,
                ):
                    self.assertNotIn("disabled", button.state())
                workspace.toggle_button.invoke()
                self.assertFalse(workspace.items[0].enabled)
            finally:
                self._close_app(app)

    def test_imported_recipe_is_inert_until_queue_run_then_writes_verified_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "输入"
            inputs.mkdir()
            first = inputs / "一.png"
            second = inputs / "二.png"
            Image.new("RGB", (40, 30), "#955f67").save(first)
            Image.new("RGB", (40, 30), "#5f7595").save(second)
            output = root / "输出"
            recipe_path = root / "缩放配方.lierecipe"
            recipe = TransformRecipe(
                name="20×10 PNG",
                steps=(
                    TransformStep.rotate(90),
                    TransformStep.resize(exact_width=20, exact_height=10),
                    TransformStep.watermark(
                        "导入水印",
                        position="左上",
                        opacity=48,
                        size_percent=6,
                    ),
                ),
                export_profile=ExportProfile(
                    output_format="PNG",
                    quality=91,
                    preserve_metadata=False,
                    rename_template="{stem}_{index:04d}",
                    conflict_policy="unique",
                ),
            )
            RecipeStore().save(recipe, recipe_path)
            app = self._create_app(directory)
            try:
                app.open_path(first, confirm=False)
                self._pump_until(app, lambda: app.document is not None)
                app.mode_buttons[ModeId.CONVERT].command()
                app.conversion_workspace.add_files((second,))
                app.conversion_output_dir_var.set(str(output))
                preview_generation = app.preview_ready_generation

                with patch(
                    "light_image_editor.ui.main_window.filedialog.askopenfilename",
                    return_value=str(recipe_path),
                ):
                    app._import_conversion_recipe()

                self.assertIsNotNone(app._conversion_recipe_override)
                self.assertEqual(app.rotation, 90)
                self.assertEqual(app.width_var.get(), "20")
                self.assertEqual(app.height_var.get(), "10")
                self.assertEqual(app.max_side_var.get(), "")
                self.assertEqual(app.watermark_text_var.get(), "导入水印")
                self.assertEqual(app.watermark_position_var.get(), "左上")
                self.assertEqual(app.watermark_opacity_var.get(), 48)
                self.assertEqual(app.watermark_size_var.get(), 6)
                self.assertEqual(app.format_var.get(), "PNG")
                self.assertEqual(app.quality_var.get(), 91)
                self.assertFalse(app.preserve_metadata_var.get())
                self.assertEqual(app.batch_rename_template_var.get(), "{stem}_{index:04d}")
                self.assertIn("20×10 PNG", app.conversion_recipe_status_var.get())
                self.assertIsNone(app._conversion_runner)
                self.assertFalse(output.exists())
                self._pump_until(
                    app,
                    lambda: (
                        app.preview_ready_generation > preview_generation
                        and app.base_preview_image is not None
                        and app.base_preview_image.size == (20, 10)
                    ),
                )
                self.assertIsNotNone(app.preview)
                self.assertFalse(output.exists())

                app._start_conversion_queue()
                self._pump_until(
                    app,
                    lambda: app._conversion_thread is None,
                    timeout=12.0,
                )

                runner = app._conversion_runner
                self.assertIsNotNone(runner)
                assert runner is not None
                records = runner.tasks
                self.assertEqual(len(records), 2)
                self.assertTrue(
                    all(record.status is ConversionTaskStatus.SUCCEEDED for record in records)
                )
                outputs = sorted(output.glob("*.png"))
                self.assertEqual(len(outputs), 2)
                for path in outputs:
                    with Image.open(path) as decoded:
                        self.assertEqual(decoded.size, (20, 10))
                        self.assertEqual(decoded.format, "PNG")
                self.assertIn("成功 2", app.conversion_job_status_var.get())
            finally:
                self._close_app(app)

    def test_single_conversion_save_uses_configured_background_without_confirmation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "transparent.png"
            target = root / "flattened.jpg"
            Image.new("RGBA", (4, 3), (255, 0, 0, 128)).save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._pump_until(app, lambda: app.document is not None)
                app.mode_buttons[ModeId.CONVERT].command()
                app._syncing_document = True
                try:
                    app.format_var.set("JPEG")
                    app.conversion_background_var.set("#000000")
                finally:
                    app._syncing_document = False
                app._update_conversion_draft_from_controls()

                with patch("light_image_editor.ui.main_window.messagebox.askyesno") as confirm:
                    saved = app._save_conversion_image(
                        target,
                        ExportOptions(output_format="JPEG", quality=100),
                    )

                self.assertEqual(saved, target)
                confirm.assert_not_called()
                with Image.open(target) as decoded:
                    pixel = decoded.convert("RGB").getpixel((0, 0))
                    self.assertLessEqual(abs(pixel[0] - 128), 3)
                    self.assertLessEqual(pixel[1], 3)
                    self.assertLessEqual(pixel[2], 3)
            finally:
                self._close_app(app)


if __name__ == "__main__":
    unittest.main()
