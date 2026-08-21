from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QMessageBox,
)

from light_image_editor.crop_presets import CropPresetCatalog, CropRatioPreset
from light_image_editor.qt_image_tools import (
    ConversionRequest,
    CropCanvas,
    ImageConversionPanel,
    ImageEditWorkspace,
)
from light_image_editor.qt_viewer import ViewerWindow
from light_image_editor.qt_viewer_widgets import set_viewer_theme, viewer_theme_colors
from light_image_editor.settings import SettingsStore


def _test_qimage(width: int = 400, height: int = 200) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill("#336699")
    return image


class QtImageToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["qt-image-tools-tests"])
        set_viewer_theme("dark")

    def test_conversion_panel_updates_format_options_target_and_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.png"
            Image.new("RGBA", (400, 200), (51, 102, 153, 160)).save(source)
            with Image.open(source) as estimate:
                panel = ImageConversionPanel(
                    source,
                    (400, 200),
                    "PNG",
                    has_alpha=True,
                    estimate_image=estimate,
                    colors=viewer_theme_colors(),
                )
            try:
                self.assertEqual(panel.output_format, "JPEG")
                self.assertEqual(
                    set(panel.format_buttons),
                    {
                        "JPEG",
                        "PNG",
                        "WEBP",
                        "AVIF",
                        "TIFF",
                        "BMP",
                        "TGA",
                        "DDS",
                        "EXR",
                        "HDR",
                        "KTX2",
                        "GIF",
                        "APNG",
                        "ICO",
                        "SVG",
                        "AI",
                        "EPS",
                        "EMF",
                        "WMF",
                    },
                )
                self.assertEqual(panel.request().target_path.suffix, ".jpg")
                self.assertFalse(panel.background_row.isHidden())
                panel.quality_slider.setValue(88)

                panel._select_format("WEBP")
                self.assertEqual(panel.request().target_path.suffix, ".webp")
                self.assertTrue(panel.background_row.isHidden())
                panel.target_size_enabled.setChecked(True)
                panel.target_size_spin.setValue(240)
                panel._update_estimate()

                request = panel.request()
                self.assertEqual(request.output_format, "WEBP")
                self.assertEqual(request.quality, 88)
                self.assertEqual(request.target_size_kb, 240)
                self.assertTrue(request.open_after)
                self.assertIn("约", panel.estimate_label.text())

                panel._select_format("DDS")
                panel.dds_pixel_format.setCurrentIndex(panel.dds_pixel_format.findData("BC5"))
                dds_request = panel.request()
                self.assertEqual(dds_request.dds_pixel_format, "BC5")
                self.assertTrue(dds_request.generate_mipmaps)
                self.assertFalse(panel.dds_row.isHidden())

                panel._select_format("SVG")
                self.assertIn("不会自动", panel.format_notice.text())
            finally:
                panel.close()

    def test_edit_workspace_right_settings_panel_is_resizable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.png"
            Image.new("RGB", (400, 200), "#336699").save(source)
            workspace = ImageEditWorkspace(
                source,
                _test_qimage(),
                colors=viewer_theme_colors(),
            )
            try:
                self.assertFalse(hasattr(workspace, "upscale_button"))
                self.assertFalse(hasattr(workspace, "remove_background_button"))
                workspace.resize(1180, 760)
                workspace.show()
                self.application.processEvents()
                initial_width = workspace.settings_panel.width()
                workspace.body_splitter.setSizes([560, 560])
                self.application.processEvents()
                self.assertGreater(workspace.settings_panel.width(), initial_width)
                self.assertGreaterEqual(workspace.settings_panel.width(), 320)
                self.assertEqual(
                    workspace.body_splitter.handle(1).cursor().shape(),
                    Qt.CursorShape.SplitHCursor,
                )
                workspace.body_splitter.setSizes([820, 320])
                self.application.processEvents()
                self.assertEqual(
                    workspace.settings_scroll.widget().width(),
                    workspace.settings_scroll.viewport().width(),
                )
                self.assertLessEqual(
                    workspace.custom_ratio_button.geometry().right(),
                    workspace.ratio_card.contentsRect().right(),
                )
                self.assertLessEqual(
                    workspace.manage_presets_button.geometry().right(),
                    workspace.ratio_card.contentsRect().right(),
                )
            finally:
                workspace.close()

    def test_all_image_tool_combo_popups_keep_the_dark_theme_palette(self) -> None:
        colors = viewer_theme_colors()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.png"
            Image.new("RGBA", (80, 60), (51, 102, 153, 160)).save(source)
            with Image.open(source) as estimate:
                conversion = ImageConversionPanel(
                    source,
                    (80, 60),
                    "PNG",
                    has_alpha=True,
                    estimate_image=estimate,
                    colors=colors,
                )
            workspace = ImageEditWorkspace(source, _test_qimage(), colors=colors)
            try:
                combos = conversion.findChildren(QComboBox) + workspace.findChildren(QComboBox)
                self.assertGreaterEqual(len(combos), 5)
                for combo in combos:
                    view = combo.view()
                    self.assertEqual(view.objectName(), "csimageComboPopup")
                    self.assertEqual(
                        view.palette().color(QPalette.ColorRole.Base).name(),
                        colors["surface_high"],
                    )
                    self.assertEqual(
                        view.palette().color(QPalette.ColorRole.Text).name(),
                        colors["text"],
                    )
                    self.assertIn("QAbstractItemView::item", view.styleSheet())
            finally:
                conversion.close()
                workspace.close()

    def test_conversion_panel_expands_to_batch_and_keeps_output_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.jpg"
            output = root / "exports"
            output.mkdir()
            Image.new("RGB", (64, 48), "#336699").save(first)
            Image.new("RGB", (32, 24), "#cc6633").save(second)
            with Image.open(first) as estimate:
                panel = ImageConversionPanel(
                    first,
                    (64, 48),
                    "PNG",
                    has_alpha=False,
                    estimate_image=estimate,
                    colors=viewer_theme_colors(),
                )
            try:
                panel.add_sources([second, second])
                panel.custom_folder.setChecked(True)
                panel.output_directory_edit.setText(str(output))
                panel.filename_suffix.setText("_web")
                panel.conflict_combo.setCurrentIndex(panel.conflict_combo.findData("skip"))
                panel._select_format("WEBP")

                request = panel.request()
                self.assertEqual(request.source_paths, (first.resolve(), second.resolve()))
                self.assertEqual(request.output_directory, output.resolve())
                self.assertEqual(request.filename_suffix, "_web")
                self.assertEqual(request.conflict_policy, "skip")
                self.assertFalse(request.open_after)
                self.assertFalse(panel.source_list.isHidden())
                self.assertFalse(panel.remove_source_button.isHidden())
                self.assertTrue(panel.open_after.isHidden())
                self.assertIn("批量", panel.convert_button.text())
            finally:
                panel.close()

    def test_crop_canvas_applies_ratio_and_resets_to_the_full_image(self) -> None:
        image = _test_qimage()
        canvas = CropCanvas(image, viewer_theme_colors())
        try:
            canvas.resize(800, 500)
            canvas.show()
            canvas.set_crop_visible(True)
            canvas.set_aspect_ratio(1.0)
            self.assertEqual(canvas.crop_box(), (100, 0, 300, 200))
            canvas.reset_crop()
            self.assertEqual(canvas.crop_box(), (0, 0, 400, 200))
        finally:
            canvas.close()

    def test_edit_workspace_locks_size_to_the_selected_crop(self) -> None:
        image = _test_qimage()
        workspace = ImageEditWorkspace(
            Path("sample.png"),
            image,
            colors=viewer_theme_colors(),
        )
        try:
            workspace.canvas.set_aspect_ratio(1.0)
            workspace.width_spin.setValue(100)
            request = workspace.request()
            self.assertEqual(request.crop_box, (100, 0, 300, 200))
            self.assertEqual((request.width, request.height), (100, 100))
            self.assertEqual(request.output_directory, Path.cwd())
            self.assertEqual(request.output_format, "PNG")
            self.assertTrue(workspace.is_dirty())
        finally:
            workspace.close()

    def test_edit_workspace_exposes_ratio_grid_lock_state_and_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.png"
            output = root / "exports"
            output.mkdir()
            workspace = ImageEditWorkspace(
                source,
                _test_qimage(),
                colors=viewer_theme_colors(),
            )
            try:
                self.assertEqual(
                    set(workspace._ratio_buttons),
                    {
                        "自由",
                        "原图",
                        "1:1",
                        "5:3",
                        "13:5",
                        "16:9",
                        "9:16",
                        "5:4",
                        "4:3",
                        "3:4",
                        "3:2",
                        "2:3",
                        "2:1",
                        "1:2",
                        "9:19",
                        "9:20",
                        "自定义",
                    },
                )
                self.assertTrue(workspace.lock_ratio.isChecked())
                self.assertEqual(workspace.lock_ratio_status.text(), "已锁定")
                self.assertTrue(workspace.source_section.isHidden())
                self.assertTrue(workspace.source_card.isHidden())
                self.assertEqual(workspace.width_spin.suffix(), "")
                self.assertEqual(workspace.height_spin.suffix(), "")
                self.assertEqual(
                    workspace.width_spin.buttonSymbols(),
                    QAbstractSpinBox.ButtonSymbols.NoButtons,
                )
                self.assertEqual(
                    workspace.target_size_spin.buttonSymbols(),
                    QAbstractSpinBox.ButtonSymbols.NoButtons,
                )
                self.assertFalse(workspace._ratio_buttons["自由"].isHidden())
                self.assertFalse(workspace._ratio_buttons["16:9"].isHidden())
                self.assertTrue(workspace._ratio_buttons["4:3"].isHidden())
                workspace.ratio_more_combo.setCurrentIndex(
                    workspace.ratio_more_combo.findData("4:3")
                )
                self.assertEqual(workspace._active_ratio_label, "4:3")
                self.assertEqual(workspace.ratio_more_combo.currentData(), "4:3")
                self.assertTrue(workspace.naming_panel.isHidden())
                workspace.naming_toggle.click()
                self.assertFalse(workspace.naming_panel.isHidden())
                workspace.naming_toggle.click()
                self.assertTrue(workspace.naming_panel.isHidden())
                self.assertEqual(
                    set(workspace.size_preset_buttons),
                    {
                        (512, 512),
                        (108, 108),
                        (28, 28),
                        (650, 250),
                        (500, 400),
                        (1440, 1440),
                        (1280, 720),
                        (1920, 1080),
                        (720, 1280),
                        (1080, 1920),
                        (1080, 2160),
                        (1080, 2280),
                        (1080, 2400),
                    },
                )
                self.assertTrue(workspace.size_preset_panel.isHidden())
                workspace._ratio_buttons["1:1"].click()
                visible_square_presets = {
                    size
                    for size, button in workspace.size_preset_buttons.items()
                    if not button.isHidden()
                }
                self.assertEqual(
                    visible_square_presets,
                    {(512, 512), (108, 108), (28, 28), (1440, 1440)},
                )
                workspace.ratio_more_combo.setCurrentIndex(
                    workspace.ratio_more_combo.findData("13:5")
                )
                self.assertEqual(
                    {
                        size
                        for size, button in workspace.size_preset_buttons.items()
                        if not button.isHidden()
                    },
                    {(650, 250)},
                )
                workspace.ratio_more_combo.setCurrentIndex(
                    workspace.ratio_more_combo.findData("5:4")
                )
                self.assertEqual(
                    {
                        size
                        for size, button in workspace.size_preset_buttons.items()
                        if not button.isHidden()
                    },
                    {(500, 400)},
                )
                workspace._ratio_buttons["16:9"].click()
                self.assertEqual(
                    {
                        size
                        for size, button in workspace.size_preset_buttons.items()
                        if not button.isHidden()
                    },
                    {(1280, 720), (1920, 1080)},
                )
                workspace.ratio_more_combo.setCurrentIndex(
                    workspace.ratio_more_combo.findData("9:16")
                )
                self.assertEqual(
                    {
                        size
                        for size, button in workspace.size_preset_buttons.items()
                        if not button.isHidden()
                    },
                    {(720, 1280), (1080, 1920)},
                )
                workspace._ratio_buttons["1:2"].click()
                self.assertFalse(workspace.size_preset_buttons[(1080, 2160)].isHidden())
                workspace.size_preset_buttons[(1080, 2160)].click()
                preset_request = workspace.request()
                self.assertEqual((preset_request.width, preset_request.height), (1080, 2160))
                self.assertEqual(preset_request.crop_box, (150, 0, 250, 200))
                self.assertFalse(workspace.format_buttons["PNG"].isHidden())
                self.assertFalse(workspace.format_buttons["JPEG"].isHidden())
                self.assertTrue(workspace.format_buttons["WEBP"].isHidden())
                workspace.other_format_combo.setCurrentIndex(
                    workspace.other_format_combo.findData("WEBP")
                )
                self.assertEqual(workspace.request().output_format, "WEBP")
                self.assertTrue(workspace._output_filename().endswith(".webp"))
                self.assertIn(".webp", workspace.output_preview_line.text())
                self.assertIn("1080 × 2160", workspace.output_preview_line.text())
                self.assertIn("WEBP", workspace.output_preview_line.text())
                workspace.lock_ratio.setChecked(False)
                self.assertEqual(workspace.lock_ratio_status.text(), "未锁定")

                with patch.object(
                    QFileDialog,
                    "getExistingDirectory",
                    return_value=str(output),
                ):
                    workspace._choose_output_directory()
                self.assertEqual(workspace.request().output_directory, output.resolve())
                self.assertEqual(workspace.output_path_edit.text(), str(output.resolve()))
            finally:
                workspace.close()

    def test_custom_crop_ratio_normalizes_and_applies_to_crop_and_size_presets(self) -> None:
        workspace = ImageEditWorkspace(
            Path("sample.png"),
            _test_qimage(),
            colors=viewer_theme_colors(),
        )
        try:
            workspace._apply_custom_crop_ratio(14, 10)
            self.assertEqual(workspace._custom_ratio, (7, 5))
            self.assertTrue(workspace.custom_ratio_button.isChecked())
            self.assertEqual(workspace.custom_ratio_button.text(), "自定义  7:5")
            self.assertAlmostEqual(workspace.canvas._aspect_ratio or 0.0, 7 / 5)
            self.assertEqual(workspace.canvas.crop_box(), (60, 0, 340, 200))

            workspace._apply_custom_crop_ratio(9, 19)
            self.assertFalse(workspace.size_preset_buttons[(1080, 2280)].isHidden())
            workspace.size_preset_buttons[(1080, 2280)].click()
            self.assertEqual(workspace._custom_ratio, (9, 19))
            self.assertEqual(workspace.custom_ratio_button.text(), "自定义  9:19")
            self.assertFalse(workspace.custom_ratio_button.isChecked())
            self.assertTrue(workspace._ratio_buttons["9:19"].isChecked())
        finally:
            workspace.close()

    def test_edit_workspace_rebuilds_ratio_and_size_buttons_from_catalog(self) -> None:
        workspace = ImageEditWorkspace(
            Path("sample.png"),
            _test_qimage(),
            colors=viewer_theme_colors(),
        )
        changed: list[CropPresetCatalog] = []
        workspace.presetCatalogChanged.connect(changed.append)
        catalog = CropPresetCatalog(
            (
                CropRatioPreset(7, 3, ((1400, 600), (700, 300))),
                CropRatioPreset(4, 5, ((800, 1000),)),
            )
        )
        try:
            workspace._replace_preset_catalog(catalog)
            self.assertEqual(workspace.preset_catalog, catalog)
            self.assertIn("7:3", workspace._ratio_buttons)
            self.assertNotIn("16:9", workspace._ratio_buttons)
            workspace._ratio_buttons["7:3"].click()
            self.assertFalse(workspace.size_preset_buttons[(1400, 600)].isHidden())
            self.assertTrue(workspace.size_preset_buttons[(800, 1000)].isHidden())
            self.assertEqual(changed, [catalog])
        finally:
            workspace.close()

    def test_viewer_persists_portable_ratio_and_size_catalog(self) -> None:
        catalog = CropPresetCatalog((CropRatioPreset(7, 3, ((1400, 600),)),))
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            first = ViewerWindow(store)
            try:
                first._save_crop_preset_catalog(catalog)
            finally:
                first.close()
            second = ViewerWindow(store)
            try:
                self.assertEqual(second._crop_preset_catalog, catalog)
            finally:
                second.close()

    def test_batch_source_list_switches_preview_and_remembers_each_crop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            Image.new("RGB", (400, 200), "#336699").save(first)
            Image.new("RGB", (200, 400), "#cc6633").save(second)
            workspace = ImageEditWorkspace(
                first,
                _test_qimage(400, 200),
                colors=viewer_theme_colors(),
            )
            requested: list[Path] = []
            workspace.sourcePreviewRequested.connect(requested.append)
            try:
                workspace.add_sources([second])
                workspace._apply_crop_ratio("1:1", 1.0)
                workspace.source_list.setCurrentRow(1)
                self.assertEqual(requested[-1], second.resolve())

                workspace.set_source_preview(second, _test_qimage(200, 400))
                self.assertEqual(workspace.active_source_path, second.resolve())
                self.assertEqual(workspace.canvas.image().size().toTuple(), (200, 400))
                self.assertEqual(workspace.canvas.crop_box(), (0, 100, 200, 300))
                self.assertIn("当前：second.png", workspace.source_summary.text())
                self.assertEqual(workspace._output_filename(), "second_edited.png")
                self.assertIn("second_edited.png", workspace.output_preview_line.text())
                self.assertIn("second_edited.png", workspace.output_summary.text())

                workspace.canvas._set_crop_rect(QRectF(0, 50, 150, 150))
                workspace.source_list.setCurrentRow(0)
                workspace.set_source_preview(first, _test_qimage(400, 200))
                self.assertEqual(workspace.canvas.crop_box(), (100, 0, 300, 200))
                self.assertEqual(workspace._output_filename(), "first_edited.png")
                self.assertIn("first_edited.png", workspace.output_summary.text())

                crop_boxes = dict(workspace.request().source_crop_boxes)
                self.assertEqual(crop_boxes[first.resolve()], (100, 0, 300, 200))
                self.assertEqual(crop_boxes[second.resolve()], (0, 50, 150, 200))
            finally:
                workspace.close()

    def test_viewer_loads_the_selected_batch_source_into_the_edit_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            Image.new("RGB", (120, 80), "#336699").save(first)
            Image.new("RGB", (60, 140), "#cc6633").save(second)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(first)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                window.open_conversion_dialog()
                deadline = time.monotonic() + 5
                while window._image_edit_workspace is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                workspace = window._image_edit_workspace
                self.assertIsInstance(workspace, ImageEditWorkspace)
                assert isinstance(workspace, ImageEditWorkspace)
                workspace.add_sources([second])
                workspace.source_list.setCurrentRow(1)

                deadline = time.monotonic() + 5
                while (
                    workspace.active_source_path != second.resolve() and time.monotonic() < deadline
                ):
                    self.application.processEvents()
                    time.sleep(0.01)
                self.assertEqual(workspace.active_source_path, second.resolve())
                self.assertEqual(workspace.canvas.image().size().toTuple(), (60, 140))
            finally:
                workspace = window._image_edit_workspace
                if isinstance(workspace, ImageEditWorkspace):
                    workspace.mark_exported()
                window.close()
                self.application.processEvents()

    def test_edit_workspace_supports_repeat_crop_center_compare_and_full_dirty_state(self) -> None:
        workspace = ImageEditWorkspace(
            Path("sample.png"),
            _test_qimage(),
            colors=viewer_theme_colors(),
        )
        try:
            workspace.show()
            self.application.processEvents()
            self.assertFalse(workspace.is_dirty())
            workspace.canvas.set_aspect_ratio(1.0)
            workspace.canvas._set_crop_rect(workspace.canvas.crop_rect().translated(-60, 0))
            workspace._center_crop()
            self.assertEqual(workspace.canvas.crop_box(), (100, 0, 300, 200))

            QTest.keyPress(workspace.canvas.viewport(), Qt.Key.Key_Space)
            self.assertTrue(workspace._comparing)
            QTest.keyRelease(workspace.canvas.viewport(), Qt.Key.Key_Space)
            self.assertFalse(workspace._comparing)

            workspace._apply_current_crop()
            self.assertEqual(workspace.source_image().size().toTuple(), (200, 200))
            self.assertEqual(workspace.canvas.crop_box(), (0, 0, 200, 200))
            workspace.canvas.set_aspect_ratio(0.5)
            workspace._apply_current_crop()
            self.assertEqual(workspace.source_image().size().toTuple(), (100, 200))
            self.assertTrue(workspace.is_dirty())

            workspace._reset_all()
            self.assertEqual(workspace.source_image().size().toTuple(), (400, 200))
            self.assertFalse(workspace.is_dirty())
            workspace.format_buttons["JPEG"].click()
            workspace.edit_quality_slider.setValue(81)
            workspace.target_size_enabled.setChecked(True)
            workspace.target_size_spin.setValue(120)
            workspace.filename_prefix.setText("game_")
            workspace.filename_suffix.setText("_v2")
            request = workspace.request()
            self.assertEqual(request.target_size_kb, 120)
            self.assertEqual(request.filename_prefix, "game_")
            self.assertEqual(request.filename_suffix, "_v2")
            self.assertTrue(workspace.is_dirty())
            workspace.mark_exported()
            self.assertFalse(workspace.is_dirty())
            workspace.width_spin.setValue(320)
            self.assertTrue(workspace.is_dirty())
        finally:
            workspace.close()

    def test_single_image_workspace_uses_clear_export_and_reset_actions(self) -> None:
        workspace = ImageEditWorkspace(
            Path("sample.png"),
            _test_qimage(),
            colors=viewer_theme_colors(),
        )
        export_only: list[object] = []
        export_and_open: list[object] = []
        workspace.quickExportRequested.connect(export_only.append)
        workspace.saveRequested.connect(export_and_open.append)
        try:
            self.assertEqual(workspace.save_button.text(), "导出")
            self.assertEqual(workspace.quick_export_button.text(), "导出并打开")
            workspace.format_buttons["JPEG"].click()
            workspace.edit_quality_slider.setValue(73)
            workspace.target_size_enabled.click()
            workspace.target_size_spin.setValue(120)
            self.assertTrue(workspace.target_size_spin.isEnabled())
            self.assertEqual(workspace.request().target_size_kb, 120)

            workspace.save_button.click()
            self.assertEqual(len(export_only), 1)
            self.assertEqual(export_and_open, [])
            workspace.quick_export_button.click()
            self.assertEqual(len(export_and_open), 1)

            workspace.width_spin.setValue(320)
            with patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.No,
            ):
                workspace.reset_button.click()
            self.assertEqual(workspace.width_spin.value(), 320)
            with patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                workspace.reset_button.click()
            self.assertEqual(workspace.width_spin.value(), 400)

            workspace.format_buttons["JPEG"].click()
            workspace.edit_quality_slider.setValue(64)
            workspace.set_result_summary(1, 0, 0)
            self.assertEqual(workspace.output_format, "JPEG")
            self.assertEqual(workspace.edit_quality_slider.value(), 64)
            self.assertIn("当前设置已保留", workspace.status_label.text())
        finally:
            workspace.close()

    def test_unified_workspace_batch_format_only_keeps_each_source_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            output = root / "output"
            output.mkdir()
            Image.new("RGB", (120, 80), "#336699").save(first)
            Image.new("RGB", (60, 120), "#cc6633").save(second)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(first)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                window.open_image_edit_workspace()
                self.application.processEvents()
                workspace = window._image_edit_workspace
                self.assertIsInstance(workspace, ImageEditWorkspace)
                assert isinstance(workspace, ImageEditWorkspace)
                workspace.add_sources([second])
                workspace.set_output_directory(output)
                workspace.format_buttons["WEBP"].click()
                workspace.filename_prefix.setText("game_")
                workspace.filename_suffix.setText("_copy")
                request = workspace.request()
                self.assertFalse(request.resize_enabled)
                self.assertIsNone(request.crop_aspect_ratio)
                window._quick_export_image_edit_request(request)

                first_target = output / "game_first_copy.webp"
                second_target = output / "game_second_copy.webp"
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    self.application.processEvents()
                    if first_target.is_file() and second_target.is_file() and not workspace._busy:
                        break
                    time.sleep(0.01)
                self.assertTrue(first_target.is_file())
                self.assertTrue(second_target.is_file())
                with Image.open(first_target) as result:
                    self.assertEqual(result.size, (120, 80))
                with Image.open(second_target) as result:
                    self.assertEqual(result.size, (60, 120))
                self.assertIs(window._image_edit_workspace, workspace)
                self.assertFalse(workspace.is_dirty())
            finally:
                window.close()
                self.application.processEvents()

    def test_batch_export_uses_each_source_own_crop_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            output = root / "output"
            output.mkdir()
            Image.new("RGB", (120, 80), "#336699").save(first)
            Image.new("RGB", (80, 120), "#cc6633").save(second)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(first)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                window.open_image_edit_workspace()
                self.application.processEvents()
                workspace = window._image_edit_workspace
                self.assertIsInstance(workspace, ImageEditWorkspace)
                assert isinstance(workspace, ImageEditWorkspace)
                workspace.add_sources([second])
                workspace.set_output_directory(output)
                workspace.filename_suffix.setText("_crop")
                workspace._apply_crop_ratio("1:1", 1.0)
                workspace.source_list.setCurrentRow(1)

                deadline = time.monotonic() + 5
                while (
                    workspace.active_source_path != second.resolve() and time.monotonic() < deadline
                ):
                    self.application.processEvents()
                    time.sleep(0.01)
                workspace.canvas._set_crop_rect(QRectF(0, 0, 80, 80))
                request = workspace.request()
                crop_boxes = dict(request.source_crop_boxes)
                self.assertEqual(crop_boxes[first.resolve()], (20, 0, 100, 80))
                self.assertEqual(crop_boxes[second.resolve()], (0, 0, 80, 80))
                window._quick_export_image_edit_request(request)

                first_target = output / "first_crop.png"
                second_target = output / "second_crop.png"
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    self.application.processEvents()
                    if first_target.is_file() and second_target.is_file() and not workspace._busy:
                        break
                    time.sleep(0.01)
                with Image.open(first_target) as result:
                    self.assertEqual(result.size, (80, 80))
                with Image.open(second_target) as result:
                    self.assertEqual(result.size, (80, 80))
            finally:
                workspace = window._image_edit_workspace
                if isinstance(workspace, ImageEditWorkspace):
                    workspace.mark_exported()
                window.close()
                self.application.processEvents()

    def test_unsaved_processing_settings_guard_opening_another_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            Image.new("RGB", (120, 80), "#336699").save(first)
            Image.new("RGB", (90, 60), "#cc6633").save(second)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(first)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                window.open_image_edit_workspace()
                self.application.processEvents()
                workspace = window._image_edit_workspace
                self.assertIsInstance(workspace, ImageEditWorkspace)
                assert isinstance(workspace, ImageEditWorkspace)
                workspace.format_buttons["WEBP"].click()
                self.assertTrue(workspace.is_dirty())

                with patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.No,
                ):
                    window.open_path(second)
                self.assertEqual(window.navigation.current, first.resolve())
                self.assertIs(window._image_edit_workspace, workspace)

                with patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    window.open_path(second)
                self.assertIsNone(window._image_edit_workspace)
                self.assertEqual(window.navigation.current, second.resolve())
            finally:
                window.close()
                self.application.processEvents()

    def test_viewer_edit_workspace_saves_a_cropped_resized_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            target = root / "source_edited.png"
            Image.new("RGB", (400, 200), "#336699").save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                window.open_image_edit_workspace()
                self.application.processEvents()
                workspace = window._image_edit_workspace
                self.assertIsNotNone(workspace)
                assert isinstance(workspace, ImageEditWorkspace)
                self.assertTrue(workspace.isVisible() or not window.isVisible())
                self.assertTrue(window.image_view.isHidden())

                workspace.canvas.set_aspect_ratio(1.0)
                workspace.width_spin.setValue(100)
                window._save_image_edit_request(workspace.request())

                self.assertTrue(target.is_file())
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    self.application.processEvents()
                    if (
                        window.navigation.current == target.resolve()
                        and window.image_view.has_image
                    ):
                        break
                    time.sleep(0.01)
                with Image.open(target) as result:
                    self.assertEqual(result.size, (100, 100))
                self.assertIsNone(window._image_edit_workspace)
            finally:
                window.close()
                self.application.processEvents()

    def test_viewer_conversion_keeps_the_source_and_creates_a_new_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            target = root / "source.webp"
            Image.new("RGB", (120, 80), "#336699").save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                assert window.current_image is not None
                request = ConversionRequest("WEBP", target, 86, False)
                with patch("light_image_editor.qt_viewer.QMessageBox.information"):
                    window._save_conversion_request(window.current_image, source, request)

                self.assertTrue(source.is_file())
                self.assertTrue(target.is_file())
                with Image.open(target) as result:
                    self.assertEqual(result.format, "WEBP")
                    self.assertEqual(result.size, (120, 80))
            finally:
                window.close()
                self.application.processEvents()

    def test_viewer_panel_batch_conversion_uses_output_and_transparency_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "transparent.png"
            second = root / "second.png"
            output = root / "exports"
            output.mkdir()
            transparent = Image.new("RGBA", (80, 60), (0, 0, 0, 0))
            transparent.paste((230, 40, 50, 255), (20, 15, 60, 45))
            transparent.save(source)
            transparent.close()
            Image.new("RGB", (40, 30), "#336699").save(second)

            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                window._open_legacy_conversion_dialog()
                panel = window._conversion_panel
                self.assertIsInstance(panel, ImageConversionPanel)
                assert isinstance(panel, ImageConversionPanel)
                panel.add_sources([second])
                panel.custom_folder.setChecked(True)
                panel.output_directory_edit.setText(str(output))
                panel.filename_suffix.setText("_out")
                panel.background_combo.setCurrentIndex(panel.background_combo.findData("#000000"))
                panel._submit()

                first_target = output / "transparent_out.jpg"
                second_target = output / "second_out.jpg"
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    self.application.processEvents()
                    if first_target.is_file() and second_target.is_file() and not panel._busy:
                        break
                    time.sleep(0.01)

                self.assertTrue(first_target.is_file())
                self.assertTrue(second_target.is_file())
                self.assertTrue(source.is_file())
                self.assertTrue(second.is_file())
                self.assertIn("成功 2", panel.status_label.text())
                with Image.open(first_target) as result:
                    corner = result.convert("RGB").getpixel((2, 2))
                    self.assertTrue(all(channel < 12 for channel in corner))
            finally:
                window.close()
                self.application.processEvents()

    def test_conversion_entry_opens_unified_workspace_and_exports_without_resize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            target = root / "source_edited.webp"
            Image.new("RGB", (120, 80), "#336699").save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                window.open_conversion_dialog()
                self.application.processEvents()
                workspace = window._image_edit_workspace
                self.assertIsInstance(workspace, ImageEditWorkspace)
                self.assertIsNone(window._conversion_panel)
                assert isinstance(workspace, ImageEditWorkspace)
                workspace.format_buttons["WEBP"].click()
                request = workspace.request()
                self.assertIsNone(request.crop_box)
                self.assertEqual((request.width, request.height), (120, 80))
                self.assertEqual(request.output_format, "WEBP")

                window._save_image_edit_request(request)
                self.assertTrue(target.is_file())
                deadline = time.monotonic() + 5
                while window.navigation.current != target.resolve() and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                with Image.open(target) as result:
                    self.assertEqual(result.format, "WEBP")
                    self.assertEqual(result.size, (120, 80))
            finally:
                window.close()
                self.application.processEvents()

    def test_workspace_jpeg_target_size_exports_under_limit_and_keeps_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "noise.png"
            target = root / "noise_edited.jpg"
            noise = Image.effect_noise((900, 700), 90).convert("RGB")
            noise.save(source)
            noise.close()
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                window.open_conversion_dialog()
                deadline = time.monotonic() + 5
                while window._image_edit_workspace is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                workspace = window._image_edit_workspace
                self.assertIsInstance(workspace, ImageEditWorkspace)
                assert isinstance(workspace, ImageEditWorkspace)
                workspace.format_buttons["JPEG"].click()
                workspace.edit_quality_slider.setValue(95)
                workspace.target_size_enabled.setChecked(True)
                workspace.target_size_spin.setValue(80)

                window._quick_export_image_edit_request(workspace.request())
                self.application.processEvents()

                self.assertTrue(target.is_file())
                self.assertLessEqual(target.stat().st_size, 80 * 1024)
                self.assertIs(window._image_edit_workspace, workspace)
                self.assertEqual(workspace.output_format, "JPEG")
                self.assertEqual(workspace.edit_quality_slider.value(), 95)
                self.assertTrue(workspace.target_size_enabled.isChecked())
            finally:
                workspace = window._image_edit_workspace
                if isinstance(workspace, ImageEditWorkspace):
                    workspace.mark_exported()
                window.close()
                self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
