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
        self.assertEqual(requirements["attrs"], "26.1.0")
        self.assertEqual(requirements["pypdfium2"], "5.12.1")
        self.assertEqual(requirements["psd-tools"], "1.17.4")
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


class ReleaseRecipeTests(unittest.TestCase):
    def test_installed_build_is_onedir_and_portable_build_is_onefile(self) -> None:
        installed = Path("packaging/windows/image_editor.spec").read_text(encoding="utf-8")
        portable = Path("packaging/windows/image_editor_portable.spec").read_text(encoding="utf-8")

        self.assertIn("exclude_binaries=True", installed)
        self.assertIn("COLLECT(", installed)
        self.assertNotIn("COLLECT(", portable)
        self.assertIn("a.binaries", portable)
        self.assertIn("a.datas", portable)

    def test_standard_installer_registers_apps_and_uses_safe_ipc_exit(self) -> None:
        script = Path("packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")

        self.assertIn("PrivilegesRequired=lowest", script)
        self.assertIn(r"Software\RegisteredApplications", script)
        self.assertIn("UninstallDisplayName", script)
        self.assertIn("--request-exit-for-upgrade", script)
        self.assertIn("PrepareToInstall", script)
        self.assertIn("InitializeUninstall", script)
        self.assertIn("uninsdeletekeyifempty", script)
        self.assertNotIn("taskkill", script.casefold())

    def test_build_stops_on_failures_and_pins_installer_download(self) -> None:
        script = Path("build.ps1").read_text(encoding="utf-8")

        self.assertIn("$LASTEXITCODE", script)
        self.assertIn("InnoInstallerSha256", script)
        self.assertIn("innosetup-7.0.2-x64.exe", script)

    def test_final_checksum_manifest_includes_release_gate_result(self) -> None:
        script = Path("build.ps1").read_text(encoding="utf-8")
        checksum_call = "Write-ReleaseChecksums -ReleasePath $Release -OutputPath $checksumPath"
        gate_call = "Invoke-Checked $Python $releaseGateArguments"

        first_checksum = script.find(checksum_call)
        gate = script.find(gate_call)
        final_checksum = script.rfind(checksum_call)

        self.assertGreaterEqual(first_checksum, 0)
        self.assertGreater(gate, first_checksum)
        self.assertGreater(final_checksum, gate)


if __name__ == "__main__":
    unittest.main()
