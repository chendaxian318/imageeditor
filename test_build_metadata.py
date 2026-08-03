from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from light_image_editor.identity import EXECUTABLE_NAME, VERSION
from scripts.generate_version_info import render_version_info, version_tuple, write_version_info
from scripts.verify_locked_environment import environment_errors, read_lock


class VersionInfoTests(unittest.TestCase):
    def test_version_tuple_is_padded_for_windows(self) -> None:
        self.assertEqual(version_tuple("1.2.3"), (1, 2, 3, 0))

    def test_version_tuple_rejects_non_numeric_release(self) -> None:
        with self.assertRaises(ValueError):
            version_tuple("release")

    def test_render_uses_single_source_product_version(self) -> None:
        content = render_version_info()

        self.assertIn(f"StringStruct('ProductVersion', '{VERSION}')", content)
        self.assertIn(f"StringStruct('OriginalFilename', '{EXECUTABLE_NAME}')", content)

    def test_generation_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "version.txt"
            write_version_info(output)
            first = output.read_bytes()
            write_version_info(output)

            self.assertEqual(output.read_bytes(), first)


class DependencyLockTests(unittest.TestCase):
    def test_lock_contains_exact_versions_only(self) -> None:
        requirements = read_lock(Path("requirements-dev.lock"))

        self.assertEqual(requirements["pillow"], "11.3.0")
        self.assertEqual(requirements["pyinstaller"], "6.17.0")
        self.assertEqual(requirements["ruff"], "0.16.1")
        self.assertGreaterEqual(len(requirements), 10)

    def test_environment_comparison_reports_drift(self) -> None:
        errors = environment_errors(
            {"one": "1.0", "two": "2.0"},
            {"one": "1.1", "extra": "3.0", "pip": "99.0"},
        )

        self.assertEqual(
            errors,
            [
                "version: one==1.1 (expected 1.0)",
                "missing: two==2.0",
                "unexpected: extra==3.0",
            ],
        )


if __name__ == "__main__":
    unittest.main()
