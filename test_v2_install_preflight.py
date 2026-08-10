from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.verify_v2_install_preflight import summarize, verify

ROOT = Path(__file__).resolve().parent


class V2InstallPreflightTests(unittest.TestCase):
    @staticmethod
    def _write_fake_pe(path: Path, machine: int = 0x8664) -> None:
        payload = bytearray(512)
        payload[:2] = b"MZ"
        struct.pack_into("<I", payload, 0x3C, 128)
        payload[128:132] = b"PE\0\0"
        struct.pack_into("<H", payload, 132, machine)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def _write_complete_repository(self, root: Path) -> dict[str, object]:
        files = {
            "build.ps1": "build-token",
            "packaging/windows/LightImageEditor.iss": "installer-token",
            "scripts/test_install_cycles.ps1": "lifecycle-token",
            "light_image_editor/version.py": '__version__ = "2.0.0"',
            "docs/release/RELEASE_NOTES-v2.0.0.md": "notes",
            "docs/release/RELEASE_PAGE-v2.0.0.md": "page",
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        contract: dict[str, object] = {
            "schema_version": 1,
            "target_version": "2.0.0",
            "previous_stable_version": "1.1.3",
            "required_files": list(files)[:4],
            "build_tokens": ["build-token"],
            "installer_tokens": ["installer-token"],
            "lifecycle_tokens": ["lifecycle-token"],
        }
        release = root / "release"
        executable_names = (
            "LightImageEditor-Portable-x64-2.0.0.exe",
            "LightImageEditor-Setup-x64-2.0.0.exe",
            "LightImageEditor-Rollback-x64-1.1.3.exe",
            "LightImageEditor/ImageEditor.exe",
        )
        for name in executable_names:
            self._write_fake_pe(release / name)
        sbom = release / "SBOM.cdx.json"
        sbom.write_text(
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.6",
                    "metadata": {"component": {"version": "2.0.0"}},
                    "components": [],
                }
            ),
            encoding="utf-8",
        )
        lifecycle = {
            "schema_version": 1,
            "captured_at_utc": "2026-08-06T00:00:00+00:00",
            "target_version": "2.0.0",
            "previous_version": "1.1.3",
            "target_installer_sha256": hashlib.sha256(
                (release / executable_names[1]).read_bytes()
            ).hexdigest(),
            "previous_installer_sha256": hashlib.sha256(
                (release / executable_names[2]).read_bytes()
            ).hexdigest(),
            "iteration_count": 3,
            "cycles": [
                {
                    "Cycle": index,
                    "PreviousInstall": True,
                    "UpgradeFromPrevious": True,
                    "TargetRepairInstall": True,
                    "PreserveUninstall": True,
                    "ClearUninstall": True,
                    "ElapsedSeconds": 1.0,
                }
                for index in range(1, 4)
            ],
            "passed": True,
        }
        (release / "V2_INSTALL_LIFECYCLE.json").write_text(
            json.dumps(lifecycle),
            encoding="utf-8",
        )
        checked_names = (*executable_names, "SBOM.cdx.json")
        checksum_lines = []
        for name in checked_names:
            digest = hashlib.sha256((release / name).read_bytes()).hexdigest()
            checksum_lines.append(f"{digest}  {name}")
        (release / "SHA256SUMS.txt").write_text(
            "\n".join(checksum_lines) + "\n",
            encoding="utf-8",
        )
        return contract

    def test_repository_preflight_truthfully_reports_version_docs_and_artifacts(self) -> None:
        contract = json.loads(
            (ROOT / "docs/release/V2_INSTALL_CONTRACT.json").read_text(encoding="utf-8")
        )
        blockers = verify(contract, ROOT)

        self.assertFalse(any(item.startswith("V2-INSTALL-VERSION:") for item in blockers))
        self.assertFalse(any("仍固定引用 v1.0.0" in item for item in blockers))
        self.assertFalse(any(item.startswith("V2-INSTALL-DOC:") for item in blockers))
        self.assertTrue(any(item.startswith("V2-INSTALL-ARTIFACT:") for item in blockers))
        self.assertFalse(summarize(contract, blockers)["passed"])

    def test_minimal_complete_repository_passes_without_artifact_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = self._write_complete_repository(root)

            blockers = verify(contract, root, require_artifacts=False)

        self.assertEqual(blockers, [])

    def test_contract_rejects_path_escape_and_missing_token(self) -> None:
        contract = json.loads(
            (ROOT / "docs/release/V2_INSTALL_CONTRACT.json").read_text(encoding="utf-8")
        )
        contract["required_files"] = ["../outside.txt", "build.ps1"]
        contract["build_tokens"] = ["definitely-not-present"]

        blockers = verify(contract, ROOT, require_artifacts=False)

        self.assertTrue(any("路径越出仓库" in item for item in blockers))
        self.assertTrue(any("缺少契约" in item for item in blockers))

    def test_complete_candidate_validates_pe_sbom_and_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = self._write_complete_repository(root)

            blockers = verify(contract, root)

        self.assertEqual(blockers, [])

    def test_candidate_integrity_failures_are_reported_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = self._write_complete_repository(root)
            portable = root / "release/LightImageEditor-Portable-x64-2.0.0.exe"
            self._write_fake_pe(portable, machine=0x014C)
            (root / "release/LightImageEditor/ImageEditor.exe").write_bytes(b"not-a-pe")
            sbom_path = root / "release/SBOM.cdx.json"
            sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
            sbom["metadata"]["component"]["version"] = "1.1.3"
            sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
            lifecycle_path = root / "release/V2_INSTALL_LIFECYCLE.json"
            lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
            lifecycle["passed"] = False
            lifecycle["cycles"][1]["UpgradeFromPrevious"] = False
            lifecycle_path.write_text(json.dumps(lifecycle), encoding="utf-8")

            blockers = verify(contract, root)

        self.assertTrue(any("x64 应用机器类型错误" in item for item in blockers))
        self.assertTrue(any("不是有效 PE 文件" in item for item in blockers))
        self.assertTrue(any(item.startswith("V2-INSTALL-SBOM:") for item in blockers))
        self.assertGreaterEqual(
            sum(item.startswith("V2-INSTALL-LIFECYCLE:") for item in blockers),
            2,
        )
        self.assertGreaterEqual(
            sum(item.startswith("V2-INSTALL-CHECKSUM:") for item in blockers),
            3,
        )

    def test_lifecycle_script_installs_previous_version_before_target(self) -> None:
        script = (ROOT / "scripts/test_install_cycles.ps1").read_text(encoding="utf-8")

        previous_install = script.index(
            "Invoke-External -FilePath $PreviousInstaller -Arguments $installArguments"
        )
        target_upgrade = script.index(
            "Invoke-External -FilePath $Installer -Arguments $installArguments",
            previous_install,
        )

        self.assertLess(previous_install, target_upgrade)
        self.assertIn("TargetVersion must be newer than PreviousVersion", script)
        self.assertIn("UpgradeFromPrevious did not preserve settings and cache", script)
        self.assertIn("TargetRepairInstall", script)
        self.assertNotIn("same signed recipe again exercises the overwrite-upgrade", script)


if __name__ == "__main__":
    unittest.main()
