from __future__ import annotations

import re
import unittest
from pathlib import Path

from PIL import Image

from light_image_editor.identity import APP_ID, PRODUCT_NAME_EN, PRODUCT_NAME_ZH, VERSION

ROOT = Path(__file__).resolve().parent


class IdentityAndResourceTests(unittest.TestCase):
    def test_product_identity_is_stable_and_versioned(self) -> None:
        self.assertEqual(PRODUCT_NAME_ZH, "CSImage")
        self.assertEqual(PRODUCT_NAME_EN, "CSImage")
        self.assertEqual(APP_ID, "io.github.chendaxian318.imageeditor")
        self.assertRegex(VERSION, re.compile(r"^\d+\.\d+\.\d+$"))

    def test_application_icon_contains_required_windows_sizes(self) -> None:
        icon_path = ROOT / "resources" / "icons" / "app.ico"
        with Image.open(icon_path) as icon:
            self.assertEqual(icon.format, "ICO")
            sizes = set(icon.info.get("sizes", set()))

        required = {
            (16, 16),
            (20, 20),
            (24, 24),
            (32, 32),
            (40, 40),
            (48, 48),
            (64, 64),
            (128, 128),
            (256, 256),
        }
        self.assertTrue(required.issubset(sizes))


if __name__ == "__main__":
    unittest.main()
