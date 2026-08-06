from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from light_image_editor.services.font_matcher import (
    FONT_FILES,
    FontCandidate,
    FontMatchError,
    installed_font_candidates,
    match_fonts,
)


class FontMatcherTests(unittest.TestCase):
    @staticmethod
    def _candidate(family: str) -> FontCandidate:
        return FontCandidate(family, Path("C:/Windows/Fonts") / FONT_FILES[family])

    def test_installed_catalog_keeps_requested_order_and_skips_missing(self) -> None:
        candidates = installed_font_candidates(("宋体", "未知字体", "微软雅黑"))
        self.assertEqual(tuple(item.family for item in candidates), ("宋体", "微软雅黑"))
        self.assertTrue(all(item.path.is_file() for item in candidates))

    def test_exact_rendered_font_ranks_first_with_stable_scores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "font-sample.png"
            image = Image.new("RGB", (280, 110), "white")
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 56)
            ImageDraw.Draw(image).text((18, 20), "轻量图片", font=font, fill="black")
            image.save(path)
            image.close()
            candidates = (
                self._candidate("宋体"),
                self._candidate("微软雅黑"),
                self._candidate("黑体"),
            )

            first = match_fonts(
                path,
                region=(0.0, 0.0, 1.0, 1.0),
                text="轻量图片",
                candidates=candidates,
            )
            second = match_fonts(
                path,
                region=(0.0, 0.0, 1.0, 1.0),
                text="轻量图片",
                candidates=candidates,
            )

            self.assertEqual(first, second)
            self.assertEqual(first[0].font_family, "微软雅黑")
            self.assertGreater(first[0].score, first[-1].score)
            self.assertTrue(all(0 <= item.score <= 1 for item in first))

    def test_unreadable_candidates_are_skipped_without_hiding_total_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.png"
            image = Image.new("RGB", (120, 60), "white")
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 32)
            ImageDraw.Draw(image).text((8, 10), "测试", font=font, fill="black")
            image.save(path)
            image.close()
            missing = FontCandidate("不存在", Path(directory) / "missing.ttf")
            with self.assertRaisesRegex(FontMatchError, "没有可用于比较"):
                match_fonts(
                    path,
                    region=(0.0, 0.0, 1.0, 1.0),
                    text="测试",
                    candidates=(missing,),
                )

    def test_flat_region_and_invalid_requests_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat.png"
            Image.new("RGB", (80, 60), "white").save(path)
            candidate = (self._candidate("微软雅黑"),)
            with self.assertRaisesRegex(FontMatchError, "没有足够清晰"):
                match_fonts(
                    path,
                    region=(0.0, 0.0, 1.0, 1.0),
                    text="测试",
                    candidates=candidate,
                )
            for region, text, rotation in (
                ((0.5, 0.2, 0.4, 0.8), "测试", 0.0),
                ((0.0, 0.0, 1.0, 1.0), "", 0.0),
                ((0.0, 0.0, 1.0, 1.0), "测试", float("nan")),
            ):
                with self.subTest(region=region, text=text, rotation=rotation):
                    with self.assertRaises(FontMatchError):
                        match_fonts(
                            path,
                            region=region,
                            text=text,
                            rotation=rotation,
                            candidates=candidate,
                        )


if __name__ == "__main__":
    unittest.main()
