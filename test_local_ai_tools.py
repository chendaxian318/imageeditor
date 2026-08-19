from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from light_image_editor.local_ai_tools import (
    BIREFNET_MODEL_SIZE,
    birefnet_status,
    calculate_upscale_target,
    compose_original_rgb_with_alpha,
    unique_output_path,
    worker_command,
)


class LocalAiToolsTests(unittest.TestCase):
    def test_upscale_targets_preserve_ratio_and_honor_long_edge(self) -> None:
        landscape = calculate_upscale_target(1600, 900, "4k")
        portrait = calculate_upscale_target(900, 1600, "2K")
        self.assertEqual((landscape.width, landscape.height), (4096, 2304))
        self.assertEqual((portrait.width, portrait.height), (1152, 2048))
        self.assertAlmostEqual(landscape.ratio, 2.56)
        with self.assertRaisesRegex(ValueError, "2K、4K 或 8K"):
            calculate_upscale_target(100, 100, "16K")

    def test_background_composition_preserves_rgb_and_multiplies_existing_alpha(self) -> None:
        source = Image.new("RGBA", (2, 1))
        source.putdata(((12, 34, 56, 128), (78, 90, 123, 255)))
        mask = Image.new("L", (2, 1))
        mask.putdata((128, 64))
        result = compose_original_rgb_with_alpha(source, mask)
        try:
            self.assertEqual(result.getpixel((0, 0)), (12, 34, 56, 64))
            self.assertEqual(result.getpixel((1, 0)), (78, 90, 123, 64))
        finally:
            result.close()
            source.close()
            mask.close()

    def test_model_status_and_output_paths_use_per_user_cache_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict("os.environ", {"CSIMAGE_LOCAL_TOOLS": str(root)}, clear=False):
                self.assertFalse(birefnet_status().available)
                model = root / "birefnet-lite" / "model.onnx"
                model.parent.mkdir(parents=True)
                with model.open("wb") as stream:
                    stream.truncate(BIREFNET_MODEL_SIZE)
                self.assertTrue(birefnet_status().available)

                first = unique_output_path(root, "sample_去背景")
                first.touch()
                second = unique_output_path(root, "sample_去背景")
                self.assertEqual(second.name, "sample_去背景_2.png")

    def test_development_worker_command_routes_through_viewer_entrypoint(self) -> None:
        command = worker_command(
            "remove-background",
            Path("source.png"),
            Path("output.png"),
        )
        self.assertIn("csimage_viewer.py", command[1])
        self.assertEqual(command[2:4], ["--local-ai-worker", "remove-background"])


if __name__ == "__main__":
    unittest.main()
