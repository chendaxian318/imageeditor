from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_v2_compatibility_evidence import verify

ROOT = Path(__file__).resolve().parent


def _descriptor(path: Path, root: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class V2CompatibilityEvidenceTests(unittest.TestCase):
    def _complete(self, root: Path) -> tuple[dict[str, object], dict[str, object]]:
        contract = json.loads(
            (ROOT / "docs/release/V2_COMPATIBILITY_CONTRACT.json").read_text(encoding="utf-8")
        )
        runs = []
        for scenario in contract["scenarios"]:
            folder = root / scenario["id"]
            folder.mkdir()
            paths = {}
            for name in ("install_log", "diagnostic_report", "test_result"):
                path = folder / f"{name}.json"
                path.write_text(f"{scenario['id']}:{name}", encoding="utf-8")
                paths[name] = _descriptor(path, root)
            runs.append(
                {
                    "scenario_id": scenario["id"],
                    "captured_at_utc": "2026-08-05T12:00:00+00:00",
                    "os_name": scenario["os_name"],
                    "os_build": scenario["minimum_build"],
                    "architecture": "x64",
                    "account_type": scenario["account_type"],
                    "network_mode": scenario["network_mode"],
                    "display_mode": scenario["display_mode"],
                    "dpi_scales": scenario["required_dpi"],
                    "package_version": "2.0.0",
                    "package_kinds": scenario["required_packages"],
                    "install_cycles": scenario["minimum_install_cycles"],
                    "machine_label": f"lab-{scenario['id']}",
                    "passed": True,
                    "files": paths,
                }
            )
        return contract, {"schema_version": 1, "release": "2.0", "runs": runs}

    def test_complete_hashed_matrix_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract, evidence = self._complete(root)
            self.assertEqual(verify(contract, evidence, root), [])

    def test_empty_template_reports_every_missing_scenario(self) -> None:
        contract = json.loads(
            (ROOT / "docs/release/V2_COMPATIBILITY_CONTRACT.json").read_text(encoding="utf-8")
        )
        evidence = json.loads(
            (ROOT / "docs/release/V2_COMPATIBILITY_EVIDENCE.template.json").read_text(
                encoding="utf-8"
            )
        )
        blockers = verify(contract, evidence, ROOT)
        self.assertEqual(sum(item.startswith("V2-COMPAT-MISSING") for item in blockers), 5)

    def test_hash_mismatch_path_escape_and_duplicate_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract, evidence = self._complete(root)
            first = evidence["runs"][0]
            first["files"]["install_log"]["sha256"] = "0" * 64
            first["files"]["diagnostic_report"]["path"] = "../outside.json"
            evidence["runs"].append(dict(first))

            blockers = verify(contract, evidence, root)

        self.assertTrue(any("SHA-256 不匹配" in item for item in blockers))
        self.assertTrue(any("路径越出证据目录" in item for item in blockers))
        self.assertTrue(any("场景证据重复" in item for item in blockers))


if __name__ == "__main__":
    unittest.main()
