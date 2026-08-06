from __future__ import annotations

import unittest

from benchmarks.run_windows_compatibility_probe import (
    CompatibilityThresholds,
    evaluate_compatibility_probe,
)


def _result() -> dict[str, object]:
    return {
        "host": {
            "is_windows": True,
            "major": 10,
            "build": 19045,
            "pointer_bits": 64,
            "per_monitor_v2_api": True,
        },
        "scales": [
            {
                "dpi": dpi,
                "all_mode_layouts_fit": True,
                "font_rendering_available": True,
                "antialiased_rounding": True,
                "rounded_region_scales": True,
            }
            for dpi in (96, 120, 144, 192)
        ],
        "virtual_monitors": {"passed": True, "transitions": 1_000},
    }


class WindowsCompatibilityProbeTests(unittest.TestCase):
    def test_supported_windows_10_shape_passes(self) -> None:
        self.assertEqual(evaluate_compatibility_probe(_result()), [])

    def test_probe_reports_host_scale_and_layout_failures(self) -> None:
        result = _result()
        result["host"]["pointer_bits"] = 32
        result["host"]["per_monitor_v2_api"] = False
        result["scales"] = result["scales"][:-1]
        result["scales"][0]["all_mode_layouts_fit"] = False
        result["virtual_monitors"]["passed"] = False

        violations = evaluate_compatibility_probe(result)

        self.assertTrue(any("64 位" in item for item in violations))
        self.assertTrue(any("Per-Monitor V2" in item for item in violations))
        self.assertTrue(any("DPI 样本" in item for item in violations))
        self.assertTrue(any("布局越界" in item for item in violations))
        self.assertTrue(any("虚拟多屏" in item for item in violations))

    def test_custom_minimum_build_is_enforced(self) -> None:
        limits = CompatibilityThresholds(minimum_windows_10_build=20000)

        violations = evaluate_compatibility_probe(_result(), limits)

        self.assertTrue(any("19045 < 20000" in item for item in violations))


if __name__ == "__main__":
    unittest.main()
