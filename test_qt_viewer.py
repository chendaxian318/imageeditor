from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QFileDialog

from light_image_editor.qt_viewer import ViewerWindow
from light_image_editor.settings import SettingsStore


class QtViewerEmptyStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["qt-viewer-tests"])

    def test_empty_state_opens_an_image_and_hides_stale_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.png"
            Image.new("RGB", (80, 60), "#336699").save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.show()
                self.application.processEvents()
                self.assertTrue(window.empty_state.isVisible())
                self.assertEqual(window.empty_open_button.text(), "打开图片")
                self.assertEqual(window.empty_hint.text(), "点击按钮选择图片，或直接拖放图片到这里")
                self.assertFalse(window.previous_button.isVisible())
                self.assertFalse(window.next_button.isVisible())
                self.assertFalse(window.info_label.isVisible())
                self.assertFalse(window.folder_button.isVisible())

                with patch.object(
                    QFileDialog,
                    "getOpenFileNames",
                    return_value=([str(source)], "图片"),
                ):
                    window.empty_open_button.click()

                deadline = time.monotonic() + 5
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                self.assertTrue(window.image_view.has_image)
                self.assertFalse(window.empty_state.isVisible())
                self.assertTrue(window.info_label.isVisible())
                self.assertTrue(window.folder_button.isVisible())
            finally:
                window.close()
                self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
