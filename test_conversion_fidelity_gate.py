from __future__ import annotations

import unittest

from benchmarks.run_conversion_fidelity_gate import FidelityThresholds, evaluate_fidelity_gate


class ConversionFidelityGateTests(unittest.TestCase):
    @staticmethod
    def _passing_result() -> dict[str, object]:
        return {
            "fixtures": {"queue_rows": 1000},
            "large_images": {
                "source_sizes": [[6000, 4000], [10000, 5000]],
                "source_pixels": [24_000_000, 50_000_000],
                "counts": {"succeeded": 2},
                "outputs": [
                    {"name": "24mp.png", "size": [2048, 1365], "format": "PNG"},
                    {"name": "50mp.png", "size": [2048, 1024], "format": "PNG"},
                ],
            },
            "mixed_formats": {
                "input_suffixes": [".jpg", ".png", ".webp"],
                "counts": {"succeeded": 3},
                "outputs": [{"format": "PNG"}, {"format": "PNG"}, {"format": "PNG"}],
            },
            "transparency": {
                "counts": {"succeeded": 1},
                "mode": "RGB",
                "center_pixel": [136, 16, 24],
                "expected_pixel": [136, 16, 24],
            },
            "animation": {
                "counts": {"succeeded": 1},
                "frame_count": 3,
                "durations_ms": [80, 120, 160],
                "expected_durations_ms": [80, 120, 160],
                "loop": 2,
                "expected_loop": 2,
            },
            "dpi_queue": [
                {
                    "dpi_percent": dpi,
                    "queue_rows": 1000,
                    "selected_last": True,
                    "toggled_last": True,
                    "interaction_ms": 25,
                }
                for dpi in (100, 125, 150, 200)
            ],
            "memory": {"final_growth_mib": 12.0},
        }

    def test_passing_result_has_no_violations(self) -> None:
        self.assertEqual(evaluate_fidelity_gate(self._passing_result()), [])

    def test_reports_independent_fidelity_failures(self) -> None:
        result = self._passing_result()
        result["large_images"]["source_pixels"][-1] = 49_000_000  # type: ignore[index]
        result["transparency"]["center_pixel"] = [255, 255, 255]  # type: ignore[index]
        result["animation"]["frame_count"] = 1  # type: ignore[index]
        result["dpi_queue"][-1]["interaction_ms"] = 1500  # type: ignore[index]
        result["memory"]["final_growth_mib"] = 900  # type: ignore[index]

        violations = evaluate_fidelity_gate(result, FidelityThresholds())

        self.assertGreaterEqual(len(violations), 5)
        self.assertTrue(any("50 MP" in message for message in violations))
        self.assertTrue(any("透明背景" in message for message in violations))
        self.assertTrue(any("动画帧数" in message for message in violations))
        self.assertTrue(any("队列交互" in message for message in violations))
        self.assertTrue(any("内存增长" in message for message in violations))


if __name__ == "__main__":
    unittest.main()
