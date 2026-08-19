from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, cast

from light_image_editor.diagnostics import CrashStore, DiagnosticReporter, upload_crash_report
from light_image_editor.settings import SettingsStore
from light_image_editor.update_security import RsaPublicKey, sign_rs256
from light_image_editor.updater import (
    UpdateAsset,
    UpdateCache,
    UpdateError,
    UpdateManifest,
    UpdateService,
    parse_signed_manifest,
)
from scripts.generate_sbom import create_sbom
from scripts.generate_update_key import generate

ROOT = Path(__file__).resolve().parent


class UpdateSecurityTests(unittest.TestCase):
    modulus: int
    exponent: int
    private_exponent: int
    key: RsaPublicKey

    @classmethod
    def setUpClass(cls) -> None:
        cls.modulus, cls.exponent, cls.private_exponent = generate(1024)
        cls.key = RsaPublicKey("test", cls.modulus, cls.exponent)

    def envelope(self, payload: dict[str, object]) -> bytes:
        return json.dumps(
            {
                "schema_version": 1,
                "key_id": "test",
                "payload": payload,
                "signature": sign_rs256(payload, self.modulus, self.private_exponent),
            }
        ).encode()

    def payload(self, content: bytes = b"verified installer") -> dict[str, object]:
        return {
            "channel": "beta",
            "version": "0.8.1",
            "published_utc": "2026-08-04T00:00:00Z",
            "minimum_version": "0.7.0",
            "release_notes_url": "https://example.invalid/release",
            "assets": {
                "windows_x64": {
                    "url": "https://example.invalid/setup.exe",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                    "authenticode_thumbprints": ["AABBCC"],
                }
            },
        }

    def test_signed_manifest_accepts_only_untampered_expected_channel(self) -> None:
        manifest = parse_signed_manifest(self.envelope(self.payload()), {"test": self.key}, "beta")
        self.assertEqual(manifest.version, "0.8.1")
        tampered = json.loads(self.envelope(self.payload()))
        tampered["payload"]["version"] = "9.9.9"
        with self.assertRaisesRegex(UpdateError, "signature"):
            parse_signed_manifest(json.dumps(tampered).encode(), {"test": self.key}, "beta")
        with self.assertRaisesRegex(UpdateError, "channel"):
            parse_signed_manifest(self.envelope(self.payload()), {"test": self.key}, "stable")

    def manifest(
        self, content: bytes, *, sha256: str | None = None, size: int | None = None
    ) -> UpdateManifest:
        return UpdateManifest(
            channel="beta",
            version="0.8.1",
            published_utc="2026-08-04T00:00:00Z",
            minimum_version="0.7.0",
            release_notes_url="",
            asset=UpdateAsset(
                "https://example.invalid/setup.exe",
                sha256 or hashlib.sha256(content).hexdigest(),
                len(content) if size is None else size,
                ("AABBCC",),
            ),
            key_id="test",
        )

    def test_download_requires_size_hash_and_authenticode_then_enables_rollback(self) -> None:
        first = b"first verified installer"
        second = b"second verified installer"
        with tempfile.TemporaryDirectory() as directory:
            cache = UpdateCache(directory)
            service = UpdateService(
                {"test": self.key},
                cache=cache,
                fetcher=lambda _url: io.BytesIO(first),
                authenticode_verifier=lambda _path, _thumbprints: True,
            )
            first_path = service.download(self.manifest(first))
            self.assertTrue(first_path.is_file())
            service.fetcher = lambda _url: io.BytesIO(second)
            second_manifest = self.manifest(second)
            second_manifest = UpdateManifest(**{**second_manifest.__dict__, "version": "0.8.2"})
            service.download(second_manifest)
            self.assertEqual(cache.rollback_installer(lambda _path, _thumbprints: True), first_path)

            first_path.write_bytes(b"tampered after download")
            self.assertIsNone(cache.rollback_installer(lambda _path, _thumbprints: True))

            for bad_manifest, expected in (
                (self.manifest(second, size=len(second) + 1), "interrupted"),
                (self.manifest(second, sha256="0" * 64), "hash"),
            ):
                with self.assertRaisesRegex(UpdateError, expected):
                    service.download(bad_manifest)
            service.authenticode_verifier = lambda _path, _thumbprints: False
            with self.assertRaisesRegex(UpdateError, "Authenticode"):
                service.download(self.manifest(second))
            self.assertEqual(list(Path(directory).glob("*.part")), [])


class DiagnosticsAndMigrationTests(unittest.TestCase):
    def test_acknowledged_crash_stops_prompting_but_remains_exportable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CrashStore(root)
            report = store.record(RuntimeError, RuntimeError("boom"), None)
            self.assertEqual(store.pending(), [report])
            store.acknowledge(report)
            self.assertEqual(store.pending(), [])
            self.assertEqual(store.reports(), [report])

            target = root / "diagnostics.zip"
            DiagnosticReporter(root).export(target, {})
            with zipfile.ZipFile(target) as archive:
                self.assertTrue(any(name.startswith("crashes/") for name in archive.namelist()))

    def test_report_redacts_private_values_and_excludes_recent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            (root / "logs" / "application.log").write_text(
                "user@example.com 10.0.0.8 token=secret-value", encoding="utf-8"
            )
            target = root / "report.zip"
            DiagnosticReporter(root).export(
                target,
                {"recent_files": [r"C:\private\photo.png"], "update_channel": "beta"},
                "contact user@example.com from 10.0.0.8 api_key=top-secret",
            )
            with zipfile.ZipFile(target) as archive:
                content = "\n".join(archive.read(name).decode() for name in archive.namelist())
            for secret in (
                "user@example.com",
                "10.0.0.8",
                "secret-value",
                "top-secret",
                "photo.png",
            ):
                self.assertNotIn(secret, content)
            self.assertIn('"recent_files_count": 1', content)

    def test_crash_upload_requires_saved_and_per_report_consent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "crash.json"
            report.write_text('{"token":"secret"}', encoding="utf-8")
            sent: list[bytes] = []

            def sender(payload: bytes) -> bool:
                sent.append(payload)
                return True

            with self.assertRaises(PermissionError):
                upload_crash_report(
                    report, consent_enabled=False, user_confirmed=True, sender=sender
                )
            self.assertTrue(
                upload_crash_report(
                    report,
                    consent_enabled=True,
                    user_confirmed=True,
                    sender=sender,
                )
            )
            self.assertNotIn(b"secret", sent[0])

    def test_legacy_settings_migrate_with_backup_and_preserve_recents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            legacy = {"recent_files": ["D:/photo.png"], "custom_future_value": 42}
            path.write_text(json.dumps(legacy), encoding="utf-8")
            migrated = SettingsStore(path).load_migrated()
            self.assertEqual(migrated["recent_files"], legacy["recent_files"])
            self.assertEqual(migrated["custom_future_value"], 42)
            self.assertEqual(migrated["schema_version"], 1)
            self.assertTrue(path.with_suffix(".json.pre-v1.bak").is_file())


class ReleaseArtifactTests(unittest.TestCase):
    def test_sbom_and_packaging_release_contract(self) -> None:
        sbom = create_sbom(ROOT / "requirements.lock")
        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        components = cast(list[dict[str, Any]], sbom["components"])
        names = {str(item["name"]).casefold() for item in components}
        self.assertEqual(
            names,
            {
                "attrs",
                "cffi",
                "flatbuffers",
                "numpy",
                "onnxruntime",
                "openexr",
                "pillow",
                "psd-tools",
                "pycparser",
                "pypdfium2",
                "pyktx",
                "protobuf",
                "pyside6",
                "pyside6-addons",
                "pyside6-essentials",
                "shiboken6",
            },
        )
        for name in ("image_editor.spec", "image_editor_portable.spec"):
            source = (ROOT / "packaging" / "windows" / name).read_text(encoding="utf-8")
            self.assertIn('"resources/update"', source)
        installer = (ROOT / "packaging" / "windows" / "LightImageEditor.iss").read_text(
            encoding="utf-8"
        )
        self.assertIn("PreserveUserData", installer)
        self.assertIn("GetCmdTail", installer)
        self.assertIn("/CLEARUSERDATA", installer)
        lifecycle = (ROOT / "scripts" / "test_install_cycles.ps1").read_text(encoding="utf-8")
        self.assertIn("use a disposable clean profile", lifecycle)
        self.assertIn("preserve user settings", lifecycle)
        self.assertIn("CLEARUSERDATA uninstall", lifecycle)
        for document in (
            "docs/beta/BETA_PLAN.md",
            "docs/beta/P0_P1_REGISTER.md",
            "docs/beta/KNOWN_ISSUES.md",
            ".github/ISSUE_TEMPLATE/beta_feedback.yml",
        ):
            self.assertTrue((ROOT / document).is_file(), document)


if __name__ == "__main__":
    unittest.main()
