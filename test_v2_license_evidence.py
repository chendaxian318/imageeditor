from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.verify_v2_license_evidence import verify

ROOT = Path(__file__).resolve().parent
EVALUATION_TIME = datetime(2026, 8, 5, 12, tzinfo=UTC)


def _descriptor(path: Path, root: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class V2LicenseEvidenceTests(unittest.TestCase):
    def _contract(self) -> dict[str, Any]:
        return json.loads(
            (ROOT / "docs/release/V2_LICENSE_EVIDENCE_CONTRACT.json").read_text(encoding="utf-8")
        )

    def _complete(self, root: Path) -> dict[str, Any]:
        grants = []
        for spec in self._contract()["required_grants"]:
            folder = root / spec["id"]
            folder.mkdir()
            files = {}
            for name in spec["required_files"]:
                path = folder / f"{name}.pdf"
                path.write_text(f"{spec['id']}:{name}", encoding="utf-8")
                files[name] = _descriptor(path, root)
            grants.append(
                {
                    "id": spec["id"],
                    "rights_holder": f"权利方-{spec['id']}",
                    "license_identifier": f"授权编号-{spec['id']}",
                    "granted_scopes": spec["required_scopes"],
                    "commercial_use": True,
                    "redistribution": True,
                    "human_approved": True,
                    "approved_by": "发布与法务负责人",
                    "approved_at_utc": "2026-08-01T08:00:00Z",
                    "valid_from_utc": "2026-08-01T00:00:00Z",
                    "expires_at_utc": None,
                    "files": files,
                }
            )
        return {"schema_version": 1, "release": "2.0", "grants": grants}

    def test_complete_hashed_current_human_approved_grants_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blockers = verify(
                self._contract(),
                self._complete(root),
                root,
                evaluation_time=EVALUATION_TIME,
            )
        self.assertEqual(blockers, [])

    def test_empty_template_reports_all_three_missing_grants(self) -> None:
        evidence = json.loads(
            (ROOT / "docs/release/V2_LICENSE_EVIDENCE.template.json").read_text(encoding="utf-8")
        )
        blockers = verify(self._contract(), evidence, ROOT, evaluation_time=EVALUATION_TIME)
        self.assertEqual(sum(item.startswith("V2-LICENSE-MISSING") for item in blockers), 3)

    def test_missing_scope_expiry_and_unapproved_grant_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = self._complete(root)
            grant = evidence["grants"][0]
            grant["granted_scopes"].remove("source_redistribution")
            grant["human_approved"] = False
            grant["expires_at_utc"] = "2026-08-02T00:00:00Z"
            blockers = verify(
                self._contract(),
                evidence,
                root,
                evaluation_time=EVALUATION_TIME,
            )

        self.assertTrue(any("缺少授权范围 source_redistribution" in item for item in blockers))
        self.assertTrue(any("必须由人工明确批准" in item for item in blockers))
        self.assertTrue(any("授权已经过期" in item for item in blockers))

    def test_hash_escape_duplicate_and_false_rights_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = self._complete(root)
            first = evidence["grants"][0]
            first["commercial_use"] = False
            first["files"]["authorization_document"]["sha256"] = "0" * 64
            first["files"]["chain_of_title_review"]["path"] = "../outside.pdf"
            evidence["grants"].append(copy.deepcopy(first))
            blockers = verify(
                self._contract(),
                evidence,
                root,
                evaluation_time=EVALUATION_TIME,
            )

        self.assertTrue(any("未授权商业使用" in item for item in blockers))
        self.assertTrue(any("SHA-256 不匹配" in item for item in blockers))
        self.assertTrue(any("路径越出证据目录" in item for item in blockers))
        self.assertTrue(any("授权重复" in item for item in blockers))


if __name__ == "__main__":
    unittest.main()
