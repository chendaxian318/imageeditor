from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_v2_provider_selection import verify

ROOT = Path(__file__).resolve().parent


def _descriptor(path: Path, root: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class V2ProviderSelectionTests(unittest.TestCase):
    def _contract(self) -> dict[str, object]:
        return json.loads(
            (ROOT / "docs/release/V2_PROVIDER_SELECTION_CONTRACT.json").read_text(encoding="utf-8")
        )

    def _complete(self, root: Path) -> dict[str, object]:
        candidates = []
        decisions = []
        roles = {
            "smart_edit": [
                "upscale",
                "remove_background",
                "ocr_text",
                "replace_text",
            ],
            "generation": [
                "text_to_image",
                "reference_image",
                "variation",
                "negative_prompt",
                "fixed_seed",
            ],
        }
        for role, capabilities in roles.items():
            folder = root / role
            folder.mkdir()
            files = {}
            for name in (
                "request",
                "response",
                "quality_result",
                "cost_result",
                "privacy_review",
            ):
                path = folder / f"{name}.json"
                path.write_text(f"{role}:{name}", encoding="utf-8")
                files[name] = _descriptor(path, root)
            candidate_id = f"candidate-{role}"
            provider_id = f"provider-{role}"
            model_id = f"model-{role}"
            candidates.append(
                {
                    "id": candidate_id,
                    "role": role,
                    "provider_name": f"沙盒供应商-{role}",
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "adapter_id": f"adapter-{role}",
                    "protocol": "https-rest",
                    "documentation_url": "https://example.test/docs",
                    "pricing_url": "https://example.test/pricing",
                    "privacy_url": "https://example.test/privacy",
                    "terms_url": "https://example.test/terms",
                    "regions": ["中国大陆以外测试区"],
                    "capabilities": capabilities
                    + ["content_policy", "cancellation", "idempotency"],
                    "image_upload": True,
                    "retention_days": 0,
                    "trains_on_customer_data": False,
                    "training_opt_out_documented": True,
                    "supports_zero_retention": True,
                    "currency": "CNY",
                    "estimated_cost_minor_per_output": 25,
                    "max_input_bytes": 20_000_000,
                    "max_outputs_per_request": 4,
                    "rate_limit_per_minute": 60,
                    "sandbox_passed": True,
                    "files": files,
                }
            )
            decisions.append(
                {
                    "role": role,
                    "candidate_id": candidate_id,
                    "selected_provider_id": provider_id,
                    "selected_model_id": model_id,
                    "human_approved": True,
                    "approved_by": "产品负责人",
                    "approved_at_utc": "2026-08-05T12:00:00Z",
                    "rationale": "真实沙盒质量、隐私和成本证据均符合冻结契约。",
                    "max_approved_cost_minor_per_output": 30,
                }
            )
        return {
            "schema_version": 1,
            "release": "2.0",
            "candidates": candidates,
            "decisions": decisions,
        }

    def test_complete_hashed_candidates_and_human_decisions_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = self._complete(root)
            self.assertEqual(verify(self._contract(), evidence, root), [])

    def test_empty_template_reports_both_missing_roles_and_decisions(self) -> None:
        evidence = json.loads(
            (ROOT / "docs/release/V2_PROVIDER_SELECTION_EVIDENCE.template.json").read_text(
                encoding="utf-8"
            )
        )
        blockers = verify(self._contract(), evidence, ROOT)
        self.assertEqual(
            sum(item.startswith("V2-PROVIDER-MISSING-CANDIDATE") for item in blockers),
            2,
        )
        self.assertEqual(
            sum(item.startswith("V2-PROVIDER-MISSING-DECISION") for item in blockers),
            2,
        )

    def test_missing_capability_and_privacy_violation_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = self._complete(root)
            candidate = evidence["candidates"][0]
            candidate["capabilities"].remove("ocr_text")
            candidate["retention_days"] = 31
            candidate["trains_on_customer_data"] = True
            candidate["privacy_url"] = "http://example.test/privacy"
            blockers = verify(self._contract(), evidence, root)

        self.assertTrue(any("缺少能力 ocr_text" in item for item in blockers))
        self.assertTrue(any("retention_days" in item for item in blockers))
        self.assertTrue(any("禁止将客户数据用于模型训练" in item for item in blockers))
        self.assertTrue(any("privacy_url 必须是官方 HTTPS 地址" in item for item in blockers))

    def test_forged_evidence_and_unapproved_decision_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = self._complete(root)
            candidate = evidence["candidates"][0]
            candidate["files"]["request"]["sha256"] = "0" * 64
            candidate["files"]["response"]["path"] = "../outside.json"
            decision = evidence["decisions"][0]
            decision["human_approved"] = False
            decision["selected_model_id"] = "伪造模型"
            blockers = verify(self._contract(), evidence, root)

        self.assertTrue(any("request SHA-256 不匹配" in item for item in blockers))
        self.assertTrue(any("response 路径越出证据目录" in item for item in blockers))
        self.assertTrue(any("必须由人工明确批准" in item for item in blockers))
        self.assertTrue(any("selected_model_id 与候选不一致" in item for item in blockers))

    def test_duplicates_and_cost_over_approval_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = self._complete(root)
            duplicate = copy.deepcopy(evidence["candidates"][0])
            evidence["candidates"].append(duplicate)
            duplicate_decision = copy.deepcopy(evidence["decisions"][0])
            evidence["decisions"].append(duplicate_decision)
            evidence["decisions"][0]["max_approved_cost_minor_per_output"] = 20
            blockers = verify(self._contract(), evidence, root)

        self.assertTrue(any("候选编号重复" in item for item in blockers))
        self.assertTrue(any("决策角色重复" in item for item in blockers))
        self.assertTrue(any("超过人工批准上限" in item for item in blockers))

    def test_v2_build_runs_dependency_verifiers_before_release_readiness(self) -> None:
        build_text = (ROOT / "build.ps1").read_text(encoding="utf-8")
        provider_invoke = "Invoke-Checked $Python $providerSelectionArguments"
        compatibility_invoke = "Invoke-Checked $Python $compatibilityArguments"
        install_invoke = "Invoke-Checked $Python $installPreflightArguments"
        supply_chain_invoke = "Invoke-Checked $Python $supplyChainArguments"
        license_invoke = "Invoke-Checked $Python $licenseArguments"
        readiness_invoke = "Invoke-Checked $Python $readinessArguments"

        self.assertIn("scripts.verify_v2_provider_selection", build_text)
        self.assertIn("IMAGEEDITOR_V2_PROVIDER_EVIDENCE", build_text)
        self.assertIn("V2_PROVIDER_SELECTION_STATUS.json", build_text)
        self.assertIn('"--provider-status", $providerStatus', build_text)
        self.assertIn('$providerSelectionArguments += "--allow-blocked"', build_text)
        self.assertIn("scripts.verify_v2_compatibility_evidence", build_text)
        self.assertIn("IMAGEEDITOR_V2_COMPATIBILITY_EVIDENCE", build_text)
        self.assertIn("V2_COMPATIBILITY_STATUS.json", build_text)
        self.assertIn('"--compatibility-status", $compatibilityStatus', build_text)
        self.assertIn('"--install-status", $installStatus', build_text)
        self.assertIn('$compatibilityArguments += "--allow-blocked"', build_text)
        self.assertIn("scripts.verify_v2_supply_chain_evidence", build_text)
        self.assertIn("V2_SUPPLY_CHAIN_STATUS.json", build_text)
        self.assertIn('"--supply-chain-status", $supplyChainStatus', build_text)
        self.assertIn('$supplyChainArguments += "--allow-blocked"', build_text)
        self.assertIn("scripts.verify_v2_license_evidence", build_text)
        self.assertIn("IMAGEEDITOR_V2_LICENSE_EVIDENCE", build_text)
        self.assertIn("V2_LICENSE_EVIDENCE_STATUS.json", build_text)
        self.assertIn('"--license-status", $licenseStatus', build_text)
        self.assertIn('$licenseArguments += "--allow-blocked"', build_text)
        self.assertLess(build_text.index(provider_invoke), build_text.index(compatibility_invoke))
        self.assertLess(build_text.index(compatibility_invoke), build_text.index(install_invoke))
        self.assertLess(build_text.index(install_invoke), build_text.index(supply_chain_invoke))
        self.assertLess(build_text.index(supply_chain_invoke), build_text.index(license_invoke))
        self.assertLess(build_text.index(license_invoke), build_text.index(readiness_invoke))

        contract = json.loads(
            (ROOT / "docs/release/V2_INSTALL_CONTRACT.json").read_text(encoding="utf-8")
        )
        self.assertIn("scripts.verify_v2_provider_selection", contract["build_tokens"])
        self.assertIn("scripts.verify_v2_compatibility_evidence", contract["build_tokens"])
        self.assertIn("scripts.verify_v2_supply_chain_evidence", contract["build_tokens"])
        self.assertIn("scripts.verify_v2_license_evidence", contract["build_tokens"])


if __name__ == "__main__":
    unittest.main()
