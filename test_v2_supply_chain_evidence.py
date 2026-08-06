from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.verify_v2_supply_chain_evidence import verify

ROOT = Path(__file__).resolve().parent
THUMBPRINT = "A" * 40


class V2SupplyChainEvidenceTests(unittest.TestCase):
    def _contract(self) -> dict[str, Any]:
        return json.loads(
            (ROOT / "docs/release/V2_SUPPLY_CHAIN_CONTRACT.json").read_text(encoding="utf-8")
        )

    def _complete(self, root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
        paths = [item["path"] for item in self._contract()["required_artifacts"]]
        hashes = {}
        signatures = []
        defender = []
        reviewed = []
        for index, relative in enumerate(paths):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"signed-artifact-{index}".encode())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            hashes[relative] = digest
            signatures.append(
                {
                    "File": relative,
                    "Status": "Valid",
                    "Thumbprint": THUMBPRINT,
                    "TimestampSubject": "CN=可信时间戳服务",
                    "Sha256": digest,
                    "Size": path.stat().st_size,
                }
            )
            defender.append({"File": relative, "ExitCode": 0, "Passed": True})
            reviewed.append({"File": relative, "Sha256": digest, "Result": "passed"})
        local_scan = {
            "SchemaVersion": 1,
            "GeneratedUtc": "2026-08-05T12:00:00Z",
            "ApplicationVersion": "2.0.0",
            "AuthenticodePassed": True,
            "Signatures": signatures,
            "DefenderStatus": "passed",
            "DefenderSignatureVersion": "1.2.3.4",
            "DefenderScans": defender,
            "MultiEngineReview": "approved",
            "PublicReleaseReady": True,
        }
        multi_engine = {
            "SchemaVersion": 1,
            "Approved": True,
            "Provider": "批准的隔离复核流程",
            "ReviewedUtc": "2026-08-05T12:30:00Z",
            "Reviewer": "发布负责人",
            "Artifacts": reviewed,
        }
        return local_scan, multi_engine, hashes

    def test_complete_cross_hashed_supply_chain_evidence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_scan, multi_engine, _hashes = self._complete(root)
            blockers = verify(self._contract(), local_scan, multi_engine, root, THUMBPRINT)
        self.assertEqual(blockers, [])

    def test_empty_templates_and_missing_artifacts_remain_blocked(self) -> None:
        local_scan = json.loads(
            (ROOT / "docs/release/V2_LOCAL_SECURITY_SCAN.template.json").read_text(encoding="utf-8")
        )
        multi_engine = json.loads(
            (ROOT / "docs/release/V2_MULTI_ENGINE_REVIEW.template.json").read_text(encoding="utf-8")
        )
        blockers = verify(self._contract(), local_scan, multi_engine, ROOT, "")

        self.assertEqual(sum("缺少最终产物" in item for item in blockers), 4)
        self.assertTrue(any("批准发布者证书指纹" in item for item in blockers))
        self.assertTrue(any("必须由人工明确批准" in item for item in blockers))

    def test_forged_signature_scan_and_review_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_scan, multi_engine, _hashes = self._complete(root)
            local_scan["Signatures"][0]["Thumbprint"] = "B" * 40
            local_scan["Signatures"][1]["TimestampSubject"] = ""
            local_scan["DefenderScans"][2]["Passed"] = False
            multi_engine["Artifacts"][3]["Sha256"] = "0" * 64
            blockers = verify(self._contract(), local_scan, multi_engine, root, THUMBPRINT)

        self.assertTrue(any("证书指纹不匹配" in item for item in blockers))
        self.assertTrue(any("缺少可信时间戳" in item for item in blockers))
        self.assertTrue(any("Defender 扫描未通过" in item for item in blockers))
        self.assertTrue(any("SHA-256 与最终产物不匹配" in item for item in blockers))

    def test_duplicates_unknown_artifacts_and_summary_booleans_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_scan, multi_engine, _hashes = self._complete(root)
            local_scan["AuthenticodePassed"] = False
            local_scan["PublicReleaseReady"] = False
            local_scan["Signatures"].append(copy.deepcopy(local_scan["Signatures"][0]))
            multi_engine["Artifacts"][0]["File"] = "../outside.exe"
            blockers = verify(self._contract(), local_scan, multi_engine, root, THUMBPRINT)

        self.assertTrue(any("Authenticode 汇总未通过" in item for item in blockers))
        self.assertTrue(any("未达到公开发布条件" in item for item in blockers))
        self.assertTrue(any("签名产物重复" in item for item in blockers))
        self.assertTrue(any("未知产物 ../outside.exe" in item for item in blockers))


if __name__ == "__main__":
    unittest.main()
