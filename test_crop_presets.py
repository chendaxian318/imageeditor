from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from light_image_editor.crop_presets import (
    CropPresetCatalog,
    CropRatioPreset,
    load_crop_preset_catalog,
    save_crop_preset_catalog,
)


class CropPresetCatalogTests(unittest.TestCase):
    def test_catalog_round_trips_through_portable_json_file(self) -> None:
        catalog = CropPresetCatalog(
            (
                CropRatioPreset(7, 3, ((1400, 600), (700, 300))),
                CropRatioPreset(4, 5, ((800, 1000),)),
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "team.csimage-presets.json"
            save_crop_preset_catalog(target, catalog)

            self.assertEqual(load_crop_preset_catalog(target), catalog)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)

    def test_ratio_is_normalized_and_size_must_match_exactly(self) -> None:
        preset = CropRatioPreset(14, 10, ((1400, 1000),))
        self.assertEqual((preset.width, preset.height, preset.label), (7, 5, "7:5"))

        with self.assertRaisesRegex(ValueError, "不一致"):
            CropRatioPreset(7, 5, ((700, 499),))

    def test_catalog_rejects_duplicate_ratio_size_and_unknown_json_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "比例 1:1 重复"):
            CropPresetCatalog((CropRatioPreset(1, 1), CropRatioPreset(2, 2)))
        with self.assertRaisesRegex(ValueError, "尺寸 512 × 512 重复"):
            CropRatioPreset(1, 1, ((512, 512), (512, 512)))
        with self.assertRaisesRegex(ValueError, "未知字段"):
            CropPresetCatalog.from_json({"schema_version": 1, "presets": [], "unexpected": True})

    def test_defaults_group_requested_dimensions_under_matching_ratios(self) -> None:
        catalog = CropPresetCatalog.defaults()
        self.assertIn((650, 250), catalog.preset("13:5").sizes)
        self.assertIn((1280, 720), catalog.preset("16:9").sizes)
        self.assertIn((720, 1280), catalog.preset("9:16").sizes)
        self.assertIn((500, 400), catalog.preset("5:4").sizes)


if __name__ == "__main__":
    unittest.main()
