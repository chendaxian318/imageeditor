from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from runpy import run_path
from typing import cast

import image_editor
from light_image_editor.app import create_application
from light_image_editor.files import (
    folder_position,
    list_folder_images,
    unique_export_path,
    validate_filename_affix,
)
from light_image_editor.settings import CONFIG_PATH, SettingsStore
from light_image_editor.ui import ImageEditorApp


class SettingsStoreTests(unittest.TestCase):
    def test_config_path_preserves_the_legacy_location(self) -> None:
        self.assertEqual(CONFIG_PATH, Path.home() / ".light_image_editor.json")

    def test_missing_and_invalid_config_load_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            self.assertEqual(store.load(), {})

            path.write_text("not json", encoding="utf-8")
            self.assertEqual(store.load(), {})

    def test_settings_round_trip_uses_readable_utf8_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            expected = {"format": "PNG", "recent_files": ["示例.png"]}

            store.save(expected)

            self.assertEqual(store.load(), expected)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), expected)


class FileServiceTests(unittest.TestCase):
    def test_folder_images_keep_supported_files_in_natural_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            paths = [folder / "image10.png", folder / "image2.jpg", folder / "notes.txt"]
            for path in paths:
                path.touch()

            images = list_folder_images(folder / "image2.jpg")

            self.assertEqual([path.name for path in images], ["image2.jpg", "image10.png"])
            self.assertEqual(folder_position(folder / "image10.png", images), (2, 2))

    def test_unique_export_path_preserves_legacy_numbering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "photo.png").touch()
            (folder / "photo_2.png").touch()

            self.assertEqual(unique_export_path(folder, "photo", ".png").name, "photo_3.png")

    def test_filename_validation_is_available_from_file_service(self) -> None:
        self.assertEqual(validate_filename_affix("prefix", "  web_ "), "web_")
        with self.assertRaises(ValueError):
            validate_filename_affix("suffix", "bad:name")


class EntryPointTests(unittest.TestCase):
    def test_entry_point_does_not_eagerly_import_the_legacy_tk_editor(self) -> None:
        command = (
            "import image_editor, sys; print('light_image_editor.ui.main_window' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "False")

    def test_production_viewer_entry_does_not_import_hidden_development_modules(self) -> None:
        command = (
            "import csimage_viewer, sys; "
            "print(any(name == 'tkinter' or name.startswith('light_image_editor.ui') "
            "for name in sys.modules))"
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "False")

    def test_pyinstaller_scope_uses_viewer_entry_and_excludes_hidden_product_areas(self) -> None:
        config = run_path("packaging/windows/viewer_build_config.py")
        self.assertEqual(config["VIEWER_ENTRYPOINT"], "csimage_viewer.py")
        excluded = set(config["VIEWER_EXCLUDES"])
        self.assertIn("tkinter", excluded)
        self.assertIn("light_image_editor.ui", excluded)
        self.assertIn("light_image_editor.domain", excluded)
        self.assertIn("light_image_editor.providers", excluded)
        self.assertIn("PySide6.QtWebEngineWidgets", excluded)
        self.assertNotIn("PySide6.QtCore", excluded)
        self.assertNotIn("PySide6.QtGui", excluded)
        self.assertNotIn("PySide6.QtWidgets", excluded)
        self.assertNotIn("PySide6.QtSvg", excluded)
        bundle_paths = set(config["EXCLUDED_BUNDLE_PATHS"])
        self.assertIn("pyside6/qt6quick.dll", bundle_paths)
        self.assertIn("pyside6/qt6qml.dll", bundle_paths)
        self.assertIn("pyside6/qt6pdf.dll", bundle_paths)
        self.assertNotIn("pyside6/qt6svg.dll", bundle_paths)
        self.assertIn("pyside6/opengl32sw.dll", bundle_paths)
        for spec_name in ("image_editor.spec", "image_editor_portable.spec"):
            spec = Path("packaging/windows") / spec_name
            source = spec.read_text(encoding="utf-8")
            self.assertIn("ROOT / VIEWER_ENTRYPOINT", source)
            self.assertIn("excludes=list(VIEWER_EXCLUDES)", source)
            self.assertIn("KEEP_VIEWER_BUNDLE_ENTRY(entry)", source)

    def test_lightweight_viewer_modules_do_not_import_excluded_product_areas(self) -> None:
        config = run_path("packaging/windows/viewer_build_config.py")
        hidden = tuple(config["HIDDEN_DEVELOPMENT_MODULES"])
        runtime_sources = (
            Path("light_image_editor/qt_viewer.py"),
            Path("light_image_editor/qt_image_tools.py"),
            Path("light_image_editor/qt_creation_tools.py"),
        )
        violations: list[str] = []
        for source_path in runtime_sources:
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                imported: list[str] = []
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    prefix = "light_image_editor." if node.level == 1 else ""
                    imported = [prefix + node.module]
                for module_name in imported:
                    if any(
                        module_name == excluded or module_name.startswith(f"{excluded}.")
                        for excluded in hidden
                    ):
                        violations.append(
                            f"{source_path}:{getattr(node, 'lineno', 0)} imports {module_name}"
                        )
        self.assertEqual(violations, [])

    def test_command_line_file_is_opened_after_window_creation(self) -> None:
        class FakeApplication:
            def __init__(self) -> None:
                self.delay: int | None = None
                self.callback = None
                self.opened: list[str | Path] = []

            def after(self, delay_ms: int, callback):
                self.delay = delay_ms
                self.callback = callback

            def open_path(self, path: str | Path, confirm: bool = True) -> None:
                self.opened.append(path)

            def mainloop(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "existing.png"
            image.touch()
            app = cast(FakeApplication, create_application([str(image)], FakeApplication))

            self.assertEqual(app.delay, 100)
            self.assertIsNotNone(app.callback)
            callback = app.callback
            if callback is None:
                self.fail("command-line open callback was not scheduled")
            callback()
            self.assertEqual(app.opened, [str(image)])

    def test_legacy_module_keeps_public_imports(self) -> None:
        self.assertIs(image_editor.ImageEditorApp, ImageEditorApp)
        self.assertIs(image_editor.validate_filename_affix, validate_filename_affix)
        self.assertTrue(callable(image_editor.copy_image_to_clipboard))


if __name__ == "__main__":
    unittest.main()
