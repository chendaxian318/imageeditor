from __future__ import annotations

import unittest

from light_image_editor.windows_effects import rounded_region_metrics


class RoundedWindowMetricsTests(unittest.TestCase):
    def test_region_uses_scaled_corner_diameter(self) -> None:
        self.assertEqual(rounded_region_metrics(1240, 780, 28), (1240, 780, 56))
        self.assertEqual(rounded_region_metrics(1240, 780, 28, 1.5), (1240, 780, 84))

    def test_region_clamps_invalid_or_tiny_dimensions(self) -> None:
        self.assertEqual(rounded_region_metrics(0, -4, 28), (1, 1, 0))
        self.assertEqual(rounded_region_metrics(20, 12, 28), (20, 12, 12))


if __name__ == "__main__":
    unittest.main()
