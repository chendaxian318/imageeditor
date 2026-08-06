from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from scripts.verify_v2_release_readiness import summarize, verify

ROOT = Path(__file__).resolve().parent


def _gate(
    gate_id: str,
    *,
    status: str = "passed",
    evidence: list[str] | None = None,
    closure: str = "",
) -> dict[str, object]:
    return {
        "id": gate_id,
        "area": "测试",
        "title": "测试门禁",
        "status": status,
        "evidence": ["evidence.txt"] if evidence is None else evidence,
        "closure": closure,
    }


def _document(gates: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "release": "2.0",
        "generated_at": "2026-08-05",
        "policy": "所有门禁必须通过。",
        "gates": gates,
    }


def _release_status(*, passed: bool) -> dict[str, object]:
    blockers = [] if passed else ["缺少真实候选"]
    return {
        "schema_version": 1,
        "release": "2.0",
        "passed": passed,
        "blocker_count": len(blockers),
        "blockers": blockers,
    }


def _install_status(*, passed: bool) -> dict[str, object]:
    blockers = [] if passed else ["缺少候选安装包"]
    return {
        "schema_version": 1,
        "target_version": "2.0.0",
        "passed": passed,
        "blocker_count": len(blockers),
        "blockers": blockers,
    }


class V2ReleaseReadinessTests(unittest.TestCase):
    def test_repository_gate_file_truthfully_blocks_unfinished_release_work(self) -> None:
        document = json.loads(
            (ROOT / "docs/release/V2_RELEASE_GATES.json").read_text(encoding="utf-8")
        )
        blockers = verify(document, ROOT)

        self.assertTrue(any(item.startswith("V2-SMART-EDIT:") for item in blockers))
        self.assertTrue(any(item.startswith("V2-GENERATION:") for item in blockers))
        self.assertFalse(any(item.startswith("V2-STABILITY:") for item in blockers))
        self.assertTrue(any(item.startswith("V2-SIGN-MALWARE:") for item in blockers))
        self.assertFalse(any("缺少证据" in item for item in blockers))
        summary = summarize(document, blockers)
        self.assertFalse(summary["passed"])
        self.assertEqual(summary["gate_count"], 15)
        self.assertEqual(summary["status_counts"]["passed"], 8)

    def test_all_passed_gates_with_repository_evidence_are_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evidence.txt").write_text("verified", encoding="utf-8")
            document = _document([_gate("V2-READY")])

            blockers = verify(document, root)

        self.assertEqual(blockers, [])
        self.assertTrue(summarize(document, blockers)["passed"])

    def test_contract_rejects_duplicates_unknown_fields_and_missing_passed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = _document(
                [
                    _gate("V2-DUPLICATE", evidence=["missing.txt"]),
                    _gate("V2-DUPLICATE", evidence=[]),
                ]
            )
            document["unexpected"] = True

            blockers = verify(document, root)

        self.assertTrue(any("顶层字段" in item for item in blockers))
        self.assertTrue(any("门禁编号重复" in item for item in blockers))
        self.assertTrue(any("缺少证据" in item for item in blockers))
        self.assertTrue(any("必须提供证据" in item for item in blockers))

    def test_evidence_cannot_escape_repository_and_open_gate_needs_closure(self) -> None:
        document = _document(
            [
                _gate("V2-ESCAPE", evidence=["../outside.txt"]),
                _gate("V2-OPEN", status="blocked", closure=""),
            ]
        )
        original = deepcopy(document)

        blockers = verify(document, ROOT)

        self.assertTrue(any("证据路径越出仓库" in item for item in blockers))
        self.assertTrue(any("未通过门禁必须说明关闭条件" in item for item in blockers))
        self.assertEqual(document, original)

    def test_untrusted_non_scalar_status_and_closure_fail_without_crashing(self) -> None:
        gate = _gate("V2-MALFORMED")
        gate["status"] = ["passed"]
        gate["closure"] = {"unexpected": True}
        document = _document([gate])

        blockers = verify(document, ROOT)
        summary = summarize(document, blockers)

        self.assertTrue(any("status 无效" in item for item in blockers))
        self.assertFalse(summary["passed"])
        self.assertEqual(summary["status_counts"]["passed"], 0)

    def test_passed_ai_gate_rejects_blocked_provider_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evidence.txt").write_text("verified", encoding="utf-8")
            status_path = root / "docs/release/v2-provider-selection-status.json"
            status_path.parent.mkdir(parents=True)
            status_path.write_text(
                json.dumps(_release_status(passed=False), ensure_ascii=False),
                encoding="utf-8",
            )
            document = _document(
                [
                    _gate(
                        "V2-SMART-EDIT",
                        evidence=[
                            "evidence.txt",
                            "docs/release/v2-provider-selection-status.json",
                        ],
                    )
                ]
            )

            blockers = verify(document, root)

        self.assertTrue(any("选型尚未通过" in item for item in blockers))

    def test_build_status_override_must_pass_and_gate_must_reference_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evidence.txt").write_text("verified", encoding="utf-8")
            repository_status = root / "docs/release/v2-provider-selection-status.json"
            repository_status.parent.mkdir(parents=True)
            repository_status.write_text(
                json.dumps(_release_status(passed=False), ensure_ascii=False),
                encoding="utf-8",
            )
            build_status = root / "generated-provider-status.json"
            build_status.write_text(
                json.dumps(_release_status(passed=True), ensure_ascii=False),
                encoding="utf-8",
            )
            referenced = _document(
                [
                    _gate(
                        "V2-GENERATION",
                        evidence=[
                            "evidence.txt",
                            "docs/release/v2-provider-selection-status.json",
                        ],
                    )
                ]
            )
            missing_reference = _document([_gate("V2-AI-PRIVACY-COST")])

            self.assertEqual(
                verify(referenced, root, provider_status_path=build_status),
                [],
            )
            blockers = verify(
                missing_reference,
                root,
                provider_status_path=build_status,
            )

        self.assertTrue(any("必须引用生产 Provider 选型状态证据" in item for item in blockers))

    def test_passed_compatibility_gate_requires_fresh_passing_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evidence.txt").write_text("verified", encoding="utf-8")
            repository_status = root / "docs/release/v2-compatibility-evidence-status.json"
            repository_status.parent.mkdir(parents=True)
            repository_status.write_text(
                json.dumps(_release_status(passed=False), ensure_ascii=False),
                encoding="utf-8",
            )
            build_status = root / "generated-compatibility-status.json"
            build_status.write_text(
                json.dumps(_release_status(passed=True), ensure_ascii=False),
                encoding="utf-8",
            )
            document = _document(
                [
                    _gate(
                        "V2-COMPAT",
                        evidence=[
                            "evidence.txt",
                            "docs/release/v2-compatibility-evidence-status.json",
                        ],
                    )
                ]
            )

            blockers = verify(document, root)
            overridden = verify(
                document,
                root,
                compatibility_status_path=build_status,
            )

        self.assertTrue(any("外部实机兼容性尚未通过" in item for item in blockers))
        self.assertEqual(overridden, [])

    def test_passed_install_gate_requires_fresh_passing_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evidence.txt").write_text("verified", encoding="utf-8")
            repository_status = root / "docs/release/v2-install-preflight.json"
            repository_status.parent.mkdir(parents=True)
            repository_status.write_text(
                json.dumps(_install_status(passed=False), ensure_ascii=False),
                encoding="utf-8",
            )
            build_status = root / "generated-install-status.json"
            build_status.write_text(
                json.dumps(_install_status(passed=True), ensure_ascii=False),
                encoding="utf-8",
            )
            document = _document(
                [
                    _gate(
                        "V2-INSTALL-UPGRADE",
                        evidence=[
                            "evidence.txt",
                            "docs/release/v2-install-preflight.json",
                        ],
                    )
                ]
            )

            blockers = verify(document, root)
            overridden = verify(document, root, install_status_path=build_status)

        self.assertTrue(any("安装候选预检尚未通过" in item for item in blockers))
        self.assertEqual(overridden, [])

    def test_passed_supply_chain_gate_requires_fresh_passing_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evidence.txt").write_text("verified", encoding="utf-8")
            repository_status = root / "docs/release/v2-supply-chain-status.json"
            repository_status.parent.mkdir(parents=True)
            repository_status.write_text(
                json.dumps(_release_status(passed=False), ensure_ascii=False),
                encoding="utf-8",
            )
            build_status = root / "generated-supply-chain-status.json"
            build_status.write_text(
                json.dumps(_release_status(passed=True), ensure_ascii=False),
                encoding="utf-8",
            )
            document = _document(
                [
                    _gate(
                        "V2-SIGN-MALWARE",
                        evidence=[
                            "evidence.txt",
                            "docs/release/v2-supply-chain-status.json",
                        ],
                    )
                ]
            )

            blockers = verify(document, root)
            overridden = verify(
                document,
                root,
                supply_chain_status_path=build_status,
            )

        self.assertTrue(any("签名与恶意软件扫描尚未通过" in item for item in blockers))
        self.assertEqual(overridden, [])

    def test_passed_license_gate_requires_fresh_passing_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evidence.txt").write_text("verified", encoding="utf-8")
            repository_status = root / "docs/release/v2-license-evidence-status.json"
            repository_status.parent.mkdir(parents=True)
            repository_status.write_text(
                json.dumps(_release_status(passed=False), ensure_ascii=False),
                encoding="utf-8",
            )
            build_status = root / "generated-license-status.json"
            build_status.write_text(
                json.dumps(_release_status(passed=True), ensure_ascii=False),
                encoding="utf-8",
            )
            document = _document(
                [
                    _gate(
                        "V2-LICENSE",
                        evidence=[
                            "evidence.txt",
                            "docs/release/v2-license-evidence-status.json",
                        ],
                    )
                ]
            )

            blockers = verify(document, root)
            overridden = verify(
                document,
                root,
                license_status_path=build_status,
            )

        self.assertTrue(any("许可授权尚未通过" in item for item in blockers))
        self.assertEqual(overridden, [])


if __name__ == "__main__":
    unittest.main()
