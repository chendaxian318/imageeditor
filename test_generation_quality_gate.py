from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from benchmarks.run_generation_gate import (
    PROFILES,
    GenerationGateThresholds,
    evaluate_generation_gate,
    run,
)
from light_image_editor.providers import MOCK_MODEL_ID, MockProvider


def _passing_result() -> dict[str, object]:
    return {
        "text_to_image": {
            "minimum_candidates_per_request": 4,
            "all_candidate_counts_match": True,
            "all_outputs_valid_png": True,
            "fixed_seed_reproduces_each_candidate": True,
            "minimum_candidate_uniqueness_ratio": 1.0,
            "distinct_asset_and_revision_identity": True,
            "provenance_complete": True,
            "minimum_unique_color_count": 12,
            "minimum_luma_span": 80.0,
            "elapsed_ms": {"p95": 100.0},
        },
        "multi_reference": {
            "reference_count": 4,
            "all_outputs_valid_png": True,
            "all_output_counts_match": True,
            "deterministic_pixels": True,
            "distinct_asset_and_revision_identity": True,
            "provenance_records_every_reference": True,
            "fingerprint_changes_with_strength": True,
            "fingerprint_changes_with_order": True,
            "maximum_weighted_color_mae": 0.5,
            "elapsed_ms": {"p95": 100.0},
        },
        "variation": {
            "all_outputs_valid_png": True,
            "all_output_counts_match": True,
            "deterministic_pixels": True,
            "distinct_asset_and_revision_identity": True,
            "source_lineage_and_strength_match": True,
            "minimum_changed_pixel_ratio": 0.8,
            "elapsed_ms": {"p95": 100.0},
        },
    }


class GenerationQualityGateTests(unittest.TestCase):
    def test_profiles_keep_smoke_small_and_standard_at_full_ui_capacity(self) -> None:
        self.assertLess(
            int(PROFILES["smoke"]["image_size"][0]),
            int(PROFILES["standard"]["image_size"][0]),
        )
        self.assertEqual(PROFILES["standard"]["candidate_count"], 4)
        self.assertEqual(PROFILES["standard"]["reference_count"], 4)

    def test_passing_generation_quality_result_has_no_violations(self) -> None:
        self.assertEqual(evaluate_generation_gate(_passing_result()), [])

    def test_gate_reports_independent_output_reference_and_variation_failures(self) -> None:
        result = _passing_result()
        text = result["text_to_image"]  # type: ignore[assignment]
        text["all_outputs_valid_png"] = False  # type: ignore[index]
        text["all_candidate_counts_match"] = False  # type: ignore[index]
        text["fixed_seed_reproduces_each_candidate"] = False  # type: ignore[index]
        text["minimum_candidate_uniqueness_ratio"] = 0.5  # type: ignore[index]
        text["provenance_complete"] = False  # type: ignore[index]
        text["minimum_unique_color_count"] = 1  # type: ignore[index]
        references = result["multi_reference"]  # type: ignore[assignment]
        references["provenance_records_every_reference"] = False  # type: ignore[index]
        references["all_output_counts_match"] = False  # type: ignore[index]
        references["fingerprint_changes_with_strength"] = False  # type: ignore[index]
        references["fingerprint_changes_with_order"] = False  # type: ignore[index]
        references["maximum_weighted_color_mae"] = 10.0  # type: ignore[index]
        variation = result["variation"]  # type: ignore[assignment]
        variation["source_lineage_and_strength_match"] = False  # type: ignore[index]
        variation["all_output_counts_match"] = False  # type: ignore[index]
        variation["minimum_changed_pixel_ratio"] = 0.0  # type: ignore[index]

        violations = evaluate_generation_gate(result, GenerationGateThresholds())

        self.assertGreaterEqual(len(violations), 14)
        self.assertTrue(any("固定种子" in message for message in violations))
        self.assertTrue(any("参考图权重" in message for message in violations))
        self.assertTrue(any("来源血缘" in message for message in violations))

    def test_tiny_real_gate_exercises_generation_reference_and_variation(self) -> None:
        result = run(
            "smoke",
            image_size=(64, 48),
            repetitions=2,
            candidate_count=2,
            reference_count=2,
        )

        self.assertTrue(result["passed"], result["violations"])
        self.assertEqual(result["text_to_image"]["output_count"], 4)
        self.assertTrue(result["text_to_image"]["fixed_seed_reproduces_each_candidate"])
        self.assertEqual(result["multi_reference"]["maximum_weighted_color_mae"], 0)
        self.assertGreater(result["variation"]["minimum_changed_pixel_ratio"], 0)

    def test_gate_rejects_invalid_custom_fixture_sizes_and_counts(self) -> None:
        with self.assertRaisesRegex(ValueError, "image_size"):
            run("smoke", image_size=(31, 64))
        with self.assertRaisesRegex(ValueError, "candidate_count"):
            run("smoke", candidate_count=5)
        with self.assertRaisesRegex(ValueError, "reference_count"):
            run("smoke", reference_count=1)

    def test_gate_accepts_an_explicit_provider_and_model_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = MockProvider(output_directory=Path(directory) / "outputs")
            result = run(
                "smoke",
                image_size=(64, 48),
                repetitions=2,
                candidate_count=2,
                reference_count=2,
                provider=provider,
                model_id=MOCK_MODEL_ID,
            )

        self.assertTrue(result["passed"], result["violations"])
        self.assertEqual(result["provider"]["provider_id"], provider.manifest.provider_id)
        self.assertEqual(result["provider"]["model"], MOCK_MODEL_ID)


if __name__ == "__main__":
    unittest.main()
