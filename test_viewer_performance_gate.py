from __future__ import annotations

import unittest

from benchmarks.run_benchmarks import _metric
from benchmarks.run_viewer_gate import ViewerGateThresholds, evaluate_viewer_gate


def _passing_result() -> dict[str, object]:
    return {
        "fixtures": {"directory_count": 500, "navigation_steps": 100},
        "metrics": {
            "first_preview": {"p95": 1700.0},
            "prefetched_switch": {"p95": 250.0},
            "directory_navigation": {"p95": 240.0},
        },
        "memory": {
            "final_growth_mib": 48.0,
            "monotonic_non_decreasing": False,
        },
    }


class ViewerPerformanceGateTests(unittest.TestCase):
    def test_metric_reports_interpolated_p95(self) -> None:
        metric = _metric([10.0, 20.0, 30.0, 40.0, 50.0])

        self.assertEqual(metric["p95"], 48.0)

    def test_passing_result_has_no_gate_violations(self) -> None:
        self.assertEqual(evaluate_viewer_gate(_passing_result()), [])

    def test_gate_reports_every_failed_acceptance_condition(self) -> None:
        result = _passing_result()
        result["fixtures"] = {"directory_count": 499, "navigation_steps": 99}
        result["metrics"] = {
            "first_preview": {"p95": 1801.0},
            "prefetched_switch": {"p95": 301.0},
            "directory_navigation": {"p95": 302.0},
        }
        result["memory"] = {
            "final_growth_mib": 300.0,
            "monotonic_non_decreasing": True,
        }

        violations = evaluate_viewer_gate(result, ViewerGateThresholds())

        self.assertEqual(len(violations), 7)
        self.assertTrue(any("首次预览" in violation for violation in violations))
        self.assertTrue(any("持续单调增长" in violation for violation in violations))


if __name__ == "__main__":
    unittest.main()
