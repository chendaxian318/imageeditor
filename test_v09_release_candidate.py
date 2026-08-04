from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import SupportsInt, cast

from PIL import Image

from benchmarks.compare_results import compare
from image_ops import EditState
from light_image_editor.preview import PreviewFrame, render_preview
from scripts.run_soak_tests import browse_soak, failure_injection
from scripts.verify_release_candidate import verify

ROOT = Path(__file__).resolve().parent


class PreviewLifetimeTests(unittest.TestCase):
    def test_borrowed_original_survives_discarded_preview(self) -> None:
        original = Image.new("RGB", (64, 48), "navy")
        frame = render_preview(
            original,
            EditState(),
            compare_original=False,
            canvas_size=(32, 24),
            zoom_percent=None,
        )
        self.assertFalse(frame.owns_working)
        frame.close()
        self.assertEqual(original.getpixel((0, 0)), (0, 0, 128))
        original.close()

    def test_owned_edit_pixels_are_closed_with_stale_frame(self) -> None:
        original = Image.new("RGB", (64, 48), "navy")
        frame = render_preview(
            original,
            EditState(brightness=10),
            compare_original=False,
            canvas_size=(32, 24),
            zoom_percent=None,
        )
        self.assertTrue(frame.owns_working)
        working = frame.working
        frame.close()
        with self.assertRaises(ValueError):
            working.getpixel((0, 0))
        original.close()

    def test_animation_preview_does_not_claim_frame_ownership(self) -> None:
        frame_image = Image.new("RGB", (20, 10), "red")
        display = frame_image.copy()
        frame = PreviewFrame(frame_image, display, 1.0)
        frame.close()
        self.assertEqual(frame_image.getpixel((0, 0)), (255, 0, 0))
        frame_image.close()


class PerformanceAndSoakTests(unittest.TestCase):
    def test_regression_gate_handles_lower_and_higher_metrics(self) -> None:
        baseline = {
            "profile": "standard",
            "environment": {"machine": "AMD64", "python": "3.12.10", "pillow": "11.3.0"},
            "metrics": {
                "startup.launch_to_ready": 100.0,
                "open_large_image.open_and_render": 100.0,
                "switch_images.per_image": 100.0,
                "batch_process.round_total": 100.0,
                "batch_process.throughput": 10.0,
            },
        }

        def current(value: float, throughput: float) -> dict[str, object]:
            return {
                "profile": "standard",
                "environment": baseline["environment"],
                "benchmarks": {
                    "startup": {"launch_to_ready": {"median": value}},
                    "open_large_image": {"open_and_render": {"median": value}},
                    "switch_images": {"per_image": {"median": value}},
                    "batch_process": {
                        "round_total": {"median": value},
                        "throughput": {"median": throughput},
                    },
                },
            }

        self.assertTrue(compare(baseline, current(114.0, 8.6), 15.0)["passed"])
        self.assertFalse(compare(baseline, current(116.0, 8.4), 15.0)["passed"])

    def test_short_browse_soak_is_memory_bounded_and_failures_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for index in range(4):
                path = root / f"image-{index}.jpg"
                Image.new("RGB", (80, 60), (index, 20, 30)).save(path)
                paths.append(path)
            result = browse_soak(paths, 40, 64)
            self.assertTrue(result["passed"])
            self.assertLessEqual(int(cast(SupportsInt, result["cache_items"])), 3)
            self.assertTrue(failure_injection(root)["passed"])


class ReleaseGateTests(unittest.TestCase):
    def test_candidate_cannot_claim_release_while_external_gates_are_open(self) -> None:
        gates = json.loads((ROOT / "docs/release/RC_GATES.json").read_text(encoding="utf-8"))
        stability = json.loads(
            (ROOT / "docs/release/RC_STABILITY_LOG.json").read_text(encoding="utf-8")
        )
        evidence = {
            "source": {"dirty": False, "tags": ["v0.9.0-rc.1"]},
            "build": {"tests_executed_in_this_build": True},
        }
        blockers = verify(gates, stability, evidence)
        self.assertTrue(any("RC-SIGN" in blocker for blocker in blockers))
        self.assertTrue(any("only 1 of 7" in blocker for blocker in blockers))

    def test_release_docs_and_security_tools_are_present(self) -> None:
        for path in (
            "docs/USER_MANUAL.md",
            "docs/release/COMPATIBILITY_MATRIX.md",
            "docs/release/RC_GATES.json",
            "docs/release/RC_STABILITY_LOG.json",
            "scripts/inspect_release.ps1",
            "scripts/generate_release_evidence.py",
            "scripts/run_soak_tests.py",
        ):
            self.assertTrue((ROOT / path).is_file(), path)
        build = (ROOT / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("BUILD_PROVENANCE.json", build)
        self.assertIn("LOCAL_SECURITY_SCAN.json", build)
        self.assertIn("pyinstaller-warnings.txt", build)
        installer = (ROOT / "packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")
        self.assertIn("docs\\USER_MANUAL.md", installer)
        self.assertIn("RELEASE_NOTES-v1.0.0.md", installer)


if __name__ == "__main__":
    unittest.main()
