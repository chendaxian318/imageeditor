from __future__ import annotations

import unittest
from pathlib import Path

from benchmarks.run_windows_ocr_gate import EXPECTED_TEXT, evaluate_windows_ocr_gate, run
from light_image_editor.services.text_ocr import TextOcrResult


class FixtureWindowsOcrBackend:
    backend_id = "windows-system-ocr"
    display_name = "测试 Windows OCR"
    available = True
    unavailable_reason = ""

    def recognize(
        self,
        source: str | Path,
        *,
        region: tuple[float, float, float, float],
        rotation: float = 0.0,
        quadrilateral=None,
    ) -> TextOcrResult:
        del region, rotation, quadrilateral
        return TextOcrResult(
            EXPECTED_TEXT,
            None,
            "zh-Hans-CN",
            "简体中文（Windows）",
            self.backend_id,
            (
                "adaptive-contrast"
                if Path(source).stem == "windows-ocr-low-contrast-small"
                else "standard"
            ),
        )


class WindowsOcrGateTests(unittest.TestCase):
    def test_injected_gate_covers_horizontal_rotation_vertical_and_perspective(self) -> None:
        result = run("smoke", backend=FixtureWindowsOcrBackend())

        self.assertTrue(result["passed"], result["violations"])
        self.assertEqual(result["case_count"], 7)
        self.assertEqual(
            [case["name"] for case in result["cases"]],
            [
                "horizontal",
                "rotation_30",
                "vertical_90",
                "perspective",
                "gradient_background",
                "pattern_background",
                "low_contrast_small",
            ],
        )
        self.assertEqual(result["cases"][-1]["preprocessing"], "adaptive-contrast")

    def test_evaluator_rejects_inaccurate_or_non_system_results(self) -> None:
        result = run("smoke", backend=FixtureWindowsOcrBackend())
        result["cases"][0]["exact_match"] = False
        result["cases"][1]["backend_id"] = "fallback"
        result["cases"][2]["deterministic"] = False
        result["cases"][3]["elapsed_ms"]["p95"] = 6_000.0
        result["cases"][-1]["preprocessing"] = "standard"

        violations = evaluate_windows_ocr_gate(result)

        self.assertEqual(len(violations), 5)


if __name__ == "__main__":
    unittest.main()
