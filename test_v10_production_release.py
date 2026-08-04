from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from light_image_editor.update_security import RsaPublicKey
from light_image_editor.updater import parse_signed_manifest
from scripts.generate_update_key import generate
from scripts.generate_update_manifest import create_manifest
from scripts.verify_production_release import verify

ROOT = Path(__file__).resolve().parent


class StableManifestTests(unittest.TestCase):
    def test_signed_stable_manifest_carries_verified_rollback_asset(self) -> None:
        modulus, exponent, private_exponent = generate(1024)
        private_key: dict[str, object] = {
            "key_id": "test",
            "modulus_hex": format(modulus, "x"),
            "private_exponent_hex": format(private_exponent, "x"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = root / "setup.exe"
            rollback = root / "rollback.exe"
            installer.write_bytes(b"signed-v1")
            rollback.write_bytes(b"signed-v09")
            envelope = create_manifest(
                installer,
                version="1.0.0",
                channel="stable",
                url="https://example.invalid/v1.exe",
                thumbprint="AA BB",
                private_key=private_key,
                rollback_installer=rollback,
                rollback_version="0.9.0",
                rollback_url="https://example.invalid/v09.exe",
            )
        manifest = parse_signed_manifest(
            json.dumps(envelope).encode(),
            {"test": RsaPublicKey("test", modulus, exponent)},
            "stable",
        )
        self.assertEqual(manifest.rollback_version, "0.9.0")
        rollback_asset = manifest.rollback_asset
        self.assertIsNotNone(rollback_asset)
        assert rollback_asset is not None
        self.assertEqual(rollback_asset.authenticode_thumbprints, ("AABB",))


class ProductionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gates: dict[str, object] = {
            "release": "1.0.0",
            "gates": [
                {"id": "V1-TEST", "status": "passed", "closure": "done"},
                {"id": "V1-MALWARE", "status": "blocked", "closure": "external scan"},
            ],
        }
        self.priorities: dict[str, object] = {
            "release": "1.0.0",
            "items": [
                {
                    "id": "P0-SIGN",
                    "priority": "P0",
                    "status": "external",
                    "introduced_after_freeze": False,
                }
            ],
        }
        start = date(2026, 8, 1)
        self.stability: dict[str, object] = {
            "required_consecutive_days": 7,
            "entries": [
                {
                    "date": (start + timedelta(days=index)).isoformat(),
                    "new_p0": 0,
                    "new_p1": 0,
                    "open_p0": 0,
                    "open_p1": 0,
                }
                for index in range(7)
            ],
        }
        self.evidence: dict[str, object] = {
            "application_version": "1.0.0",
            "source": {
                "dirty": False,
                "tags": ["v1.0.0"],
                "annotated_tags": ["v1.0.0"],
            },
            "build": {"tests_executed_in_this_build": True},
        }

    def create_bundle(self, root: Path) -> None:
        for name in (
            "LightImageEditor-Rollback-x64-0.9.0.exe",
            "update-stable.json",
            "SHA256SUMS.txt",
            "SBOM.cdx.json",
            "THIRD_PARTY_LICENSES.md",
            "V1_PRIORITY_MATRIX.json",
            "RELEASE_NOTES-v1.0.0.md",
            "POST_RELEASE_MONITORING.md",
            "HOTFIX_RUNBOOK.md",
        ):
            (root / name).write_text("evidence", encoding="utf-8")
        artifact_paths = (
            "LightImageEditor/ImageEditor.exe",
            "LightImageEditor-Portable-x64-1.0.0.exe",
            "LightImageEditor-Setup-x64-1.0.0.exe",
            "SBOM.cdx.json",
        )
        artifacts = []
        for relative in artifact_paths:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(relative.encode())
            artifacts.append(
                {
                    "path": relative,
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        self.evidence["artifacts"] = artifacts

    def test_draft_allows_only_pending_external_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory)
            self.create_bundle(release)
            security = {
                "AuthenticodePassed": True,
                "DefenderStatus": "passed",
                "MultiEngineReview": "required-before-publication",
                "PublicReleaseReady": False,
            }
            self.assertEqual(
                verify(
                    self.gates,
                    self.priorities,
                    self.stability,
                    self.evidence,
                    security,
                    release,
                    candidate=True,
                ),
                [],
            )
            blockers = verify(
                self.gates,
                self.priorities,
                self.stability,
                self.evidence,
                security,
                release,
                candidate=False,
            )
            self.assertTrue(any("V1-MALWARE" in blocker for blocker in blockers))

    def test_repository_truthfully_blocks_unmet_v1_gates(self) -> None:
        gates = json.loads(
            (ROOT / "docs/release/V1_RELEASE_GATES.json").read_text(encoding="utf-8")
        )
        self.assertTrue(any(gate["status"] == "blocked" for gate in gates["gates"]))
        self.assertEqual(gates["release"], "1.0.0")
        priorities = json.loads(
            (ROOT / "docs/release/V1_PRIORITY_MATRIX.json").read_text(encoding="utf-8")
        )
        incomplete_p0 = [
            item["id"]
            for item in priorities["items"]
            if item["priority"] == "P0" and item["status"] != "passed"
        ]
        self.assertEqual(incomplete_p0, ["P0-LICENSE", "P0-SIGN"])

    def test_new_p1_or_p2_requires_equal_scope_exchange(self) -> None:
        priorities = {
            "release": "1.0.0",
            "items": [
                {
                    "id": "P2-OCR",
                    "priority": "P2",
                    "status": "passed",
                    "introduced_after_freeze": True,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory)
            self.create_bundle(release)
            security = {
                "AuthenticodePassed": True,
                "DefenderStatus": "passed",
                "MultiEngineReview": "required-before-publication",
                "PublicReleaseReady": False,
            }
            blockers = verify(
                self.gates,
                priorities,
                self.stability,
                self.evidence,
                security,
                release,
                candidate=True,
            )
            self.assertTrue(any("V1-SCOPE" in blocker for blocker in blockers))


class ProductionContractTests(unittest.TestCase):
    def test_release_documents_and_two_stage_workflows_exist(self) -> None:
        for relative in (
            "docs/DEFAULT_APP_HELP.md",
            "docs/release/RELEASE_NOTES-v1.0.0.md",
            "docs/release/RELEASE_PAGE-v1.0.0.md",
            "docs/release/POST_RELEASE_MONITORING.md",
            "docs/release/HOTFIX_RUNBOOK.md",
            "docs/release/V1_PRIORITY_MATRIX.json",
            ".github/workflows/production-candidate.yml",
            ".github/workflows/publish-production.yml",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        candidate = (ROOT / ".github/workflows/production-candidate.yml").read_text()
        publisher = (ROOT / ".github/workflows/publish-production.yml").read_text()
        self.assertIn("--draft", candidate)
        self.assertIn("actions/attest@v4", candidate)
        self.assertIn("MULTI_ENGINE_REVIEW.json", publisher)
        self.assertIn("--draft=false", publisher)

    def test_production_build_requires_signing_and_rollback(self) -> None:
        build = (ROOT / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("ProductionCandidate", build)
        self.assertIn("Production build requires Authenticode", build)
        self.assertIn("Production build requires the previous-version rollback installer", build)
        security = (ROOT / "scripts/inspect_release.ps1").read_text(encoding="utf-8")
        self.assertIn("TimeStamperCertificate", security)
        self.assertIn("GetFullPath($ReleasePath)", security)


if __name__ == "__main__":
    unittest.main()
