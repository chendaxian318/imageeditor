from __future__ import annotations

import unittest

from benchmarks.run_conversion_gate import (
    PROFILES,
    ConversionGateThresholds,
    evaluate_conversion_gate,
    run,
)


def _passing_result() -> dict[str, object]:
    return {
        "fixtures": {
            "valid_count": 1_000,
            "corrupt_count": 4,
            "cancel_count": 128,
            "max_workers": 4,
        },
        "normal_run": {
            "counts": {
                "pending": 0,
                "running": 0,
                "succeeded": 1_000,
                "failed": 4,
                "cancelled": 0,
            },
            "peak_in_flight": 4,
            "verified_outputs": 1_000,
            "output_decode_failures": [],
        },
        "cancellation": {
            "requested_items": 128,
            "workers_became_ready": True,
            "started_after_cancel": 0,
            "cancel_latency_ms": 250.0,
            "peak_in_flight": 4,
            "counts": {
                "pending": 0,
                "running": 0,
                "succeeded": 0,
                "failed": 0,
                "cancelled": 128,
            },
            "outputs_written": 0,
        },
        "memory": {"final_growth_mib": 64.0, "peak_growth_mib": 128.0},
    }


class ConversionPerformanceGateTests(unittest.TestCase):
    def test_profiles_keep_ci_smoke_small_and_standard_at_one_thousand(self) -> None:
        self.assertLess(int(PROFILES["smoke"]["valid_count"]), 100)
        self.assertEqual(PROFILES["standard"]["valid_count"], 1_000)

    def test_passing_result_has_no_violations(self) -> None:
        self.assertEqual(evaluate_conversion_gate(_passing_result()), [])

    def test_gate_reports_reliability_cancellation_and_memory_failures(self) -> None:
        result = _passing_result()
        result["fixtures"] = {
            "valid_count": 999,
            "corrupt_count": 4,
            "cancel_count": 128,
            "max_workers": 4,
        }
        result["normal_run"] = {
            "counts": {
                "pending": 0,
                "running": 0,
                "succeeded": 998,
                "failed": 3,
                "cancelled": 1,
            },
            "peak_in_flight": 5,
            "verified_outputs": 997,
            "output_decode_failures": ["broken.png"],
        }
        result["cancellation"] = {
            "requested_items": 128,
            "workers_became_ready": False,
            "started_after_cancel": 1,
            "cancel_latency_ms": 1_001.0,
            "peak_in_flight": 5,
            "counts": {
                "pending": 1,
                "running": 0,
                "succeeded": 1,
                "failed": 0,
                "cancelled": 126,
            },
            "outputs_written": 1,
        }
        result["memory"] = {"final_growth_mib": 193.0, "peak_growth_mib": 385.0}

        violations = evaluate_conversion_gate(result, ConversionGateThresholds())

        self.assertGreaterEqual(len(violations), 12)
        self.assertTrue(any("在途" in violation for violation in violations))
        self.assertTrue(any("重新解码" in violation for violation in violations))
        self.assertTrue(any("取消后" in violation for violation in violations))
        self.assertTrue(any("RSS" in violation for violation in violations))

    def test_tiny_real_gate_exercises_runner_end_to_end(self) -> None:
        result = run(
            "smoke",
            valid_count=6,
            corrupt_count=1,
            cancel_count=6,
            max_workers=2,
            image_size=(16, 12),
        )

        self.assertTrue(result["passed"], result["violations"])
        self.assertEqual(result["normal_run"]["counts"]["succeeded"], 6)
        self.assertEqual(result["normal_run"]["counts"]["failed"], 1)
        self.assertEqual(result["normal_run"]["verified_outputs"], 6)
        self.assertLessEqual(result["normal_run"]["peak_in_flight"], 2)
        self.assertEqual(result["cancellation"]["started_after_cancel"], 0)
        self.assertEqual(result["cancellation"]["outputs_written"], 0)


if __name__ == "__main__":
    unittest.main()
