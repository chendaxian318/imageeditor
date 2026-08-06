from __future__ import annotations

import unittest

from benchmarks.run_four_mode_soak import (
    FourModeSoakThresholds,
    evaluate_four_mode_soak,
)


def _result() -> dict[str, object]:
    return {
        "results": {
            "viewer": {"passed": True, "iterations": 20_000},
            "mode_router": {"passed": True, "switches": 20_000},
            "conversion": {"passed": True, "succeeded": 1_000, "failed": 10},
            "ai": {
                "passed": True,
                "edit_succeeded": 50,
                "generation_succeeded": 50,
                "cancelled_per_kind": 50,
            },
        },
        "memory": {
            "final_growth_mib": 20.0,
            "peak_growth_mib": 40.0,
            "monotonic_non_decreasing": False,
        },
    }


class FourModeSoakGateTests(unittest.TestCase):
    def test_release_shape_passes_every_threshold(self) -> None:
        self.assertEqual(evaluate_four_mode_soak(_result()), [])

    def test_gate_reports_samples_phase_and_memory_failures(self) -> None:
        result = _result()
        result["results"]["viewer"]["iterations"] = 10
        result["results"]["conversion"]["passed"] = False
        result["memory"]["final_growth_mib"] = 200.0
        result["memory"]["peak_growth_mib"] = 400.0
        result["memory"]["monotonic_non_decreasing"] = True

        violations = evaluate_four_mode_soak(result)

        self.assertTrue(any("看图循环" in item for item in violations))
        self.assertTrue(any("conversion 阶段" in item for item in violations))
        self.assertTrue(any("最终 RSS" in item for item in violations))
        self.assertTrue(any("峰值 RSS" in item for item in violations))
        self.assertTrue(any("单调增长" in item for item in violations))

    def test_custom_thresholds_are_enforced(self) -> None:
        result = _result()
        limits = FourModeSoakThresholds(ai_success_per_kind=60)

        violations = evaluate_four_mode_soak(result, limits)

        self.assertTrue(any("改图成功" in item for item in violations))
        self.assertTrue(any("生图成功" in item for item in violations))


if __name__ == "__main__":
    unittest.main()
