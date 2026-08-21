from __future__ import annotations

import os
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog

from light_image_editor.image_model_api import ImageTextEditApiResult
from light_image_editor.qt_creation_tools import ImageTextCreationPanel
from light_image_editor.qt_viewer import ViewerWindow
from light_image_editor.qt_viewer_widgets import set_viewer_theme, viewer_theme_colors
from light_image_editor.settings import SettingsStore


def _png_bytes(color: str = "#336699") -> bytes:
    image = Image.new("RGB", (96, 64), color)
    try:
        buffer = BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()
    finally:
        image.close()


class QtCreationToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["qt-creation-tools-tests"])
        set_viewer_theme("dark")

    def test_creation_panel_emits_structured_request_without_redundant_consent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(_png_bytes())
            panel = ImageTextCreationPanel(
                source,
                (96, 64),
                endpoint="http://127.0.0.1:9000/v1/images/edits",
                model="local-image-model",
                colors=viewer_theme_colors(),
            )
            requests: list[object] = []
            panel.generateRequested.connect(requests.append)
            try:
                panel.original_text.setText("旧标题")
                panel.replacement_text.setText("新标题")
                panel._submit()
                self.assertEqual(len(requests), 1)
                request = panel.request()
                self.assertEqual(request.original_text, "旧标题")
                self.assertEqual(request.replacement_text, "新标题")
                self.assertEqual(request.model, "local-image-model")
                self.assertTrue(panel._busy)

                panel.set_result(_png_bytes("#cc3344"))
                self.assertFalse(panel._busy)
                self.assertFalse(panel.result_card.isHidden())
                self.assertIsNotNone(panel.result_preview.pixmap())
            finally:
                panel.close()

    def test_creation_panel_owns_local_hd_and_background_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(_png_bytes())
            panel = ImageTextCreationPanel(
                source,
                (96, 64),
                endpoint="https://api.openai.com/v1/images/edits",
                model="gpt-image-2",
                colors=viewer_theme_colors(),
            )
            received: list[object] = []
            panel.localToolRequested.connect(received.append)
            try:
                self.assertEqual(
                    list(panel.upscale_resolution_buttons),
                    ["2K", "4K", "8K"],
                )
                self.assertTrue(panel.upscale_resolution_buttons["2K"].isChecked())
                panel.upscale_resolution_buttons["4K"].click()
                self.assertTrue(panel.upscale_resolution_buttons["4K"].isChecked())
                panel.upscale_button.click()
                self.assertEqual(len(received), 1)
                self.assertEqual(getattr(received[0], "operation"), "upscale")
                self.assertEqual(getattr(received[0], "resolution"), "4K")
                self.assertEqual(getattr(received[0], "output_directory"), source.parent.resolve())
                panel.remove_background_button.click()
                self.assertEqual(getattr(received[1], "operation"), "remove-background")
                panel.set_busy(True)
                self.assertFalse(panel.upscale_button.isEnabled())
                self.assertFalse(panel.remove_background_button.isEnabled())
                self.assertEqual(panel.progress.maximum(), 0)
            finally:
                panel.close()

    def test_ai_service_combo_popups_keep_the_dark_theme_palette(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(_png_bytes())
            colors = viewer_theme_colors()
            panel = ImageTextCreationPanel(
                source,
                (96, 64),
                endpoint="https://api.openai.com/v1/images/edits",
                model="gpt-image-2",
                colors=colors,
            )
            try:
                combos = panel.findChildren(QComboBox)
                self.assertEqual(len(combos), 2)
                for combo in combos:
                    self.assertEqual(combo.view().objectName(), "csimageComboPopup")
                    self.assertEqual(
                        combo.view().palette().color(QPalette.ColorRole.Base).name(),
                        colors["surface_high"],
                    )
                    self.assertEqual(
                        combo.view().palette().color(QPalette.ColorRole.Text).name(),
                        colors["text"],
                    )
            finally:
                panel.close()

    def test_official_provider_hides_technical_fields_and_pastes_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(_png_bytes())
            with patch.dict(
                os.environ,
                {"CSIMAGE_IMAGE_API_KEY": "", "OPENAI_API_KEY": ""},
                clear=False,
            ):
                panel = ImageTextCreationPanel(
                    source,
                    (96, 64),
                    endpoint="https://api.openai.com/v1/images/edits",
                    model="gpt-image-2",
                    colors=viewer_theme_colors(),
                )
            try:
                self.assertEqual(panel.provider_combo.currentData(), "openai")
                self.assertIn("GPT Image 2", panel.model_summary.text())
                self.assertIn("GPT Image 2", panel.api_service_detail.text())
                self.assertEqual(panel.api_status_badge.text(), "需要密钥")
                self.assertFalse(panel.api_toggle.isChecked())
                self.assertTrue(panel.api_card.isHidden())
                self.assertTrue(panel.advanced_toggle.isHidden())
                self.assertTrue(panel.advanced_card.isHidden())
                panel.api_toggle.setChecked(True)
                self.application.processEvents()
                self.assertFalse(panel.api_card.isHidden())
                self.assertFalse(panel.default_config_card.isHidden())
                self.assertEqual(panel.default_config_status.text(), "仅本次使用")
                QApplication.clipboard().setText("sk-test-key")
                panel._paste_api_key()
                self.assertEqual(panel.api_key_edit.text(), "sk-test-key")
                self.assertIn("已填写", panel.api_status_badge.text())
                self.assertIn("不会保存", panel.key_status.text())
                panel.remember_key.setChecked(True)
                self.assertEqual(panel.default_config_status.text(), "✓ 已设为默认")
                self.assertTrue(panel.default_config_card.property("active"))
            finally:
                panel.close()

    def test_custom_provider_exposes_advanced_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(_png_bytes())
            panel = ImageTextCreationPanel(
                source,
                (96, 64),
                endpoint="http://127.0.0.1:9000/v1/images/edits",
                model="local-image-model",
                colors=viewer_theme_colors(),
            )
            try:
                self.assertEqual(panel.provider_combo.currentData(), "custom")
                self.assertFalse(panel.advanced_toggle.isHidden())
                self.assertFalse(panel.advanced_card.isHidden())
                self.assertEqual(
                    panel.endpoint_edit.text(),
                    "http://127.0.0.1:9000/v1/images/edits",
                )
                self.assertEqual(panel.model_edit.text(), "local-image-model")
                self.assertIn("本机服务", panel.api_status_badge.text())
            finally:
                panel.close()

    def test_company_default_is_ready_and_displays_its_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(_png_bytes())
            panel = ImageTextCreationPanel(
                source,
                (96, 64),
                endpoint="https://gateway.example/v1/images/edits",
                model="gpt-image-2-all",
                api_key="company-secret",
                provider_display_name="Company Gateway",
                company_default=True,
                colors=viewer_theme_colors(),
            )
            try:
                self.assertEqual(panel.provider_combo.currentData(), "company")
                self.assertIn("GPT Image 2 All", panel.model_summary.text())
                self.assertIn("公司默认", panel.model_summary.text())
                self.assertIn("可直接使用", panel.api_status_badge.text())
                self.assertTrue(panel.api_key_edit.isHidden())
                self.assertTrue(panel.default_config_card.isHidden())
                self.assertFalse(panel.quality_combo.isEnabled())
                panel.replacement_text.setText("新标题")
                self.assertEqual(panel.request().api_key, "company-secret")
                self.assertEqual(panel.request().model, "gpt-image-2-all")
            finally:
                panel.close()

    def test_viewer_keeps_apilio_endpoint_and_model_without_bundled_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(_png_bytes())
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                with patch(
                    "light_image_editor.secure_image_api_key.SecureImageApiKeyStore.read",
                    return_value=None,
                ):
                    window.open_creation_panel()
                panel = window._creation_panel
                self.assertIsInstance(panel, ImageTextCreationPanel)
                assert isinstance(panel, ImageTextCreationPanel)
                self.assertEqual(panel.provider_combo.currentData(), "custom")
                self.assertEqual(
                    panel.endpoint_edit.text(),
                    "https://api.apilio.ai/v1/images/edits",
                )
                self.assertEqual(panel.model_edit.text(), "gpt-image-2-all")
                self.assertEqual(panel.api_key_edit.text(), "")
                self.assertIn("Apilio", panel.api_service_detail.text())
                self.assertEqual(panel.api_status_badge.text(), "需要密钥")
            finally:
                window.close()
                self.application.processEvents()

    def test_viewer_runs_model_result_preview_and_saves_a_new_png(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            target = root / "source_文字改写.png"
            source.write_bytes(_png_bytes("#112233"))
            generated = _png_bytes("#cc3344")
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                with (
                    patch(
                        "light_image_editor.secure_image_api_key.SecureImageApiKeyStore.read",
                        return_value=None,
                    ),
                    patch(
                        "light_image_editor.internal_default_image_api.IMAGE_API_KEY",
                        "",
                    ),
                ):
                    window.open_creation_panel()
                panel = window._creation_panel
                self.assertIsInstance(panel, ImageTextCreationPanel)
                assert isinstance(panel, ImageTextCreationPanel)
                window.resize(1200, 760)
                window.root.resize(1200, 760)
                self.application.processEvents()
                window._layout_overlays()
                initial_panel_width = panel.width()
                self.assertFalse(window.side_panel_resize_handle.isHidden())
                self.assertEqual(
                    window.side_panel_resize_handle.cursor().shape(),
                    Qt.CursorShape.SizeHorCursor,
                )
                window._resize_side_panel(-80)
                self.assertEqual(panel.width(), initial_panel_width + 80)
                panel.endpoint_edit.setText("http://127.0.0.1:9000/v1/images/edits")
                panel.model_edit.setText("local-image-model")
                panel.replacement_text.setText("新标题")
                with patch(
                    "light_image_editor.image_model_api.edit_image_text",
                    return_value=ImageTextEditApiResult(generated, "test prompt"),
                ):
                    panel._submit()
                    deadline = time.monotonic() + 5
                    while window._creation_result is None and time.monotonic() < deadline:
                        self.application.processEvents()
                        time.sleep(0.01)

                self.assertIsNotNone(window._creation_result)
                self.assertTrue(window._creation_preview_active)
                self.assertFalse(panel.result_card.isHidden())

                with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
                    window._save_creation_result()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    self.application.processEvents()
                    if target.is_file() and window.navigation.current == target.resolve():
                        break
                    time.sleep(0.01)
                self.assertTrue(target.is_file())
                self.assertTrue(source.is_file())
                with Image.open(target) as result:
                    self.assertEqual(result.convert("RGB").getpixel((0, 0)), (204, 51, 68))
            finally:
                window.close()

    def test_viewer_previews_local_tool_result_before_explicit_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            target = root / "source_去背景.png"
            source.write_bytes(_png_bytes("#112233"))
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                with (
                    patch(
                        "light_image_editor.secure_image_api_key.SecureImageApiKeyStore.read",
                        return_value=None,
                    ),
                    patch(
                        "light_image_editor.internal_default_image_api.IMAGE_API_KEY",
                        "",
                    ),
                ):
                    window.open_creation_panel()
                panel = window._creation_panel
                self.assertIsInstance(panel, ImageTextCreationPanel)
                assert isinstance(panel, ImageTextCreationPanel)

                def run_worker(
                    _operation: str,
                    input_path: Path,
                    output_path: Path,
                    **_kwargs: object,
                ) -> dict[str, object]:
                    output_path.write_bytes(input_path.read_bytes())
                    return {"model": "BiRefNet Lite · test"}

                with (
                    patch(
                        "light_image_editor.local_ai_tools.birefnet_status",
                        return_value=SimpleNamespace(available=True),
                    ),
                    patch(
                        "light_image_editor.local_ai_tools.run_worker_process",
                        side_effect=run_worker,
                    ),
                    patch.object(
                        window._conversion_executor,
                        "submit",
                        side_effect=lambda function, *args: function(*args),
                    ),
                ):
                    panel.remove_background_button.click()
                    self.application.processEvents()

                self.assertFalse(target.is_file())
                self.assertIs(window._creation_panel, panel)
                self.assertEqual(window.navigation.current, source.resolve())
                self.assertIsNotNone(window._creation_result)
                self.assertFalse(window._creation_preview_active)
                self.assertFalse(panel.result_card.isHidden())
                self.assertEqual(panel.result_title.text(), "透明背景候选图")

                panel.result_button.click()
                self.assertTrue(window._creation_preview_active)
                panel.original_button.click()
                self.assertFalse(window._creation_preview_active)

                with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
                    panel.save_button.click()
                deadline = time.monotonic() + 5
                while window.navigation.current != target.resolve() and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                self.assertTrue(target.is_file())
                self.assertEqual(window.navigation.current, target.resolve())
            finally:
                window.close()
                self.application.processEvents()

    def test_continuous_creation_chain_reuses_each_accepted_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            target = root / "source_制图结果.png"
            source.write_bytes(_png_bytes("#112233"))
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                with (
                    patch(
                        "light_image_editor.secure_image_api_key.SecureImageApiKeyStore.read",
                        return_value=None,
                    ),
                    patch(
                        "light_image_editor.internal_default_image_api.IMAGE_API_KEY",
                        "",
                    ),
                ):
                    window.open_creation_panel()
                panel = window._creation_panel
                self.assertIsInstance(panel, ImageTextCreationPanel)
                assert isinstance(panel, ImageTextCreationPanel)
                panel.endpoint_edit.setText("http://127.0.0.1:9000/v1/images/edits")
                panel.model_edit.setText("local-image-model")
                panel.replacement_text.setText("新标题")

                with patch(
                    "light_image_editor.image_model_api.edit_image_text",
                    return_value=ImageTextEditApiResult(_png_bytes("#334455"), "prompt"),
                ):
                    panel._submit()
                    deadline = time.monotonic() + 5
                    while window._creation_result is None and time.monotonic() < deadline:
                        self.application.processEvents()
                        time.sleep(0.01)
                panel.continue_button.click()
                self.assertEqual(window._creation_step_labels, ["文字改写"])

                worker_inputs: list[tuple[str, tuple[int, int, int, int]]] = []

                def run_worker(
                    operation: str,
                    input_path: Path,
                    output_path: Path,
                    **kwargs: object,
                ) -> dict[str, object]:
                    with Image.open(input_path) as incoming:
                        pixel = incoming.convert("RGBA").getpixel((0, 0))
                    worker_inputs.append((operation, pixel))
                    color = "#556677" if operation == "remove-background" else "#778899"
                    Image.new("RGBA", (96, 64), color).save(output_path, "PNG")
                    return {
                        "model": "test-local-model",
                        "resolution": kwargs.get("resolution", ""),
                    }

                with (
                    patch(
                        "light_image_editor.local_ai_tools.birefnet_status",
                        return_value=SimpleNamespace(available=True),
                    ),
                    patch(
                        "light_image_editor.local_ai_tools.upscayl_status",
                        return_value=SimpleNamespace(available=True),
                    ),
                    patch(
                        "light_image_editor.local_ai_tools.run_worker_process",
                        side_effect=run_worker,
                    ),
                    patch.object(
                        window._conversion_executor,
                        "submit",
                        side_effect=lambda function, *args: function(*args),
                    ),
                ):
                    panel.remove_background_button.click()
                    self.application.processEvents()
                    panel.continue_button.click()
                    panel.upscale_resolution_buttons["4K"].click()
                    panel.upscale_button.click()
                    self.application.processEvents()
                    panel.continue_button.click()

                self.assertEqual(
                    worker_inputs,
                    [
                        ("remove-background", (51, 68, 85, 255)),
                        ("upscale", (85, 102, 119, 255)),
                    ],
                )
                self.assertEqual(
                    window._creation_step_labels,
                    ["文字改写", "智能去背景", "高清优化 4K"],
                )
                self.assertEqual(
                    window._creation_working_image.convert("RGBA").getpixel((0, 0)),
                    (119, 136, 153, 255),
                )
                self.assertTrue(panel.undo_step_button.isEnabled())

                panel.undo_step_button.click()
                self.assertEqual(window._creation_step_labels, ["文字改写", "智能去背景"])
                self.assertEqual(
                    window._creation_working_image.convert("RGBA").getpixel((0, 0)),
                    (85, 102, 119, 255),
                )

                with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
                    panel.save_button.click()
                deadline = time.monotonic() + 5
                while window.navigation.current != target.resolve() and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                self.assertTrue(target.is_file())
                self.assertEqual(window.navigation.current, target.resolve())
            finally:
                window.close()
                self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
