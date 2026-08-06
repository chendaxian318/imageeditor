from __future__ import annotations

import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from light_image_editor.providers.mock_provider import _draw_replacement_text
from light_image_editor.services.text_ocr import (
    FallbackTextOcrBackend,
    OcrCommandResult,
    TesseractTextOcrBackend,
    TextOcrError,
    TextOcrResult,
    TextOcrUnavailableError,
    UnavailableTextOcrBackend,
    WindowsTextOcrBackend,
    _enhance_ocr_image,
    _rectify_curved_baseline,
    create_default_text_ocr_backend,
)


def _tsv(*rows: tuple[str, str, str, str, str]) -> str:
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext"
    )
    rendered = [header]
    for index, (block, paragraph, line, confidence, text) in enumerate(rows, 1):
        rendered.append(
            f"5\t1\t{block}\t{paragraph}\t{line}\t{index}\t0\t0\t10\t10\t{confidence}\t{text}"
        )
    return "\n".join(rendered)


def _baseline_delta(image: Image.Image) -> float:
    rgb = image.convert("RGB")
    try:
        pixels = rgb.load()
        assert pixels is not None
        ink = [
            (x, y) for y in range(rgb.height) for x in range(rgb.width) if max(pixels[x, y]) < 100
        ]
    finally:
        rgb.close()
    if not ink:
        return 0.0
    left = min(x for x, _y in ink)
    right = max(x for x, _y in ink)
    span = right - left + 1
    bands: list[float] = []
    for start, end in ((0.05, 0.25), (0.40, 0.60), (0.75, 0.95)):
        values = [y for x, y in ink if left + span * start <= x <= left + span * end]
        bands.append(sum(values) / max(1, len(values)))
    return (bands[0] + bands[2]) / 2 - bands[1]


class TextOcrTests(unittest.TestCase):
    def test_adaptive_contrast_image_is_bounded_for_small_and_extremely_wide_inputs(
        self,
    ) -> None:
        small = Image.new("RGB", (100, 20), (110, 120, 130))
        wide = Image.new("RGB", (5_000, 100), (110, 120, 130))
        try:
            enlarged = _enhance_ocr_image(small)
            reduced = _enhance_ocr_image(wide)
            try:
                self.assertEqual(enlarged.size, (300, 60))
                self.assertEqual(reduced.size, (2_400, 48))
                self.assertEqual(enlarged.mode, "RGB")
                self.assertEqual(reduced.mode, "RGB")
                self.assertEqual(small.size, (100, 20))
                self.assertEqual(wide.size, (5_000, 100))
            finally:
                enlarged.close()
                reduced.close()
        finally:
            small.close()
            wide.close()

    def test_windows_backend_retries_empty_result_with_bounded_contrast_enhancement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "powershell.exe"
            executable.write_bytes(b"fixture")
            source = root / "低对比小字.png"
            Image.new("RGB", (700, 130), (125, 132, 140)).save(source)
            observed_sizes: list[tuple[int, int]] = []
            observed_extrema: list[tuple[tuple[int, int], ...]] = []
            observed_timeouts: list[float] = []

            def runner(
                input_path: Path,
                output_path: Path,
                timeout: float,
            ) -> OcrCommandResult:
                with Image.open(input_path) as region:
                    observed_sizes.append(region.size)
                    observed_extrema.append(region.getextrema())
                observed_timeouts.append(timeout)
                if len(observed_sizes) == 1:
                    output_path.write_text(
                        '{"schema_version":1,"text":"","language":"zh-Hans-CN","line_count":null}',
                        encoding="utf-8",
                    )
                else:
                    output_path.write_text(
                        '{"schema_version":1,"text":"轻量图片编辑器 2026",'
                        '"language":"zh-Hans-CN","line_count":1}',
                        encoding="utf-8",
                    )
                return OcrCommandResult(0, "", "")

            backend = WindowsTextOcrBackend(executable, runner=runner)
            result = backend.recognize(source, region=(0.0, 0.0, 1.0, 1.0))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.text, "轻量图片编辑器 2026")
        self.assertEqual(result.preprocessing, "adaptive-contrast")
        self.assertEqual(observed_sizes[0], (700, 130))
        self.assertEqual(observed_sizes[1], (2_100, 390))
        self.assertEqual(len(observed_extrema[1]), 3)
        self.assertTrue(all(extrema == observed_extrema[1][0] for extrema in observed_extrema[1]))
        self.assertLessEqual(observed_timeouts[1], observed_timeouts[0])

    def test_windows_backend_uses_shared_perspective_preprocessing_and_json_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "powershell.exe"
            executable.write_bytes(b"fixture")
            source = root / "Windows透视文字.png"
            Image.new("RGB", (300, 200), "white").save(source)
            observed_sizes: list[tuple[int, int]] = []

            def runner(
                input_path: Path,
                output_path: Path,
                _timeout: float,
            ) -> OcrCommandResult:
                with Image.open(input_path) as region:
                    observed_sizes.append(region.size)
                output_path.write_text(
                    '{"schema_version":1,"text":"  轻 量 图 片  \\r\\n编辑器 2026  ",'
                    '"language":"zh-Hans-CN","line_count":2}',
                    encoding="utf-8",
                )
                return OcrCommandResult(0, "", "")

            backend = WindowsTextOcrBackend(executable, runner=runner)
            result = backend.recognize(
                source,
                region=(0.1, 0.1, 0.9, 0.85),
                rotation=22,
                quadrilateral=(0.2, 0.25, 0.8, 0.15, 0.85, 0.75, 0.15, 0.65),
            )

        self.assertEqual(observed_sizes, [(196, 101)])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.text, "轻量图片\n编辑器 2026")
        self.assertIsNone(result.confidence)
        self.assertEqual(result.language_label, "简体中文（Windows）")
        self.assertEqual(result.backend_id, "windows-system-ocr")

    def test_windows_backend_rejects_invalid_output_and_fallback_uses_tesseract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            powershell = root / "powershell.exe"
            tesseract = root / "tesseract.exe"
            powershell.write_bytes(b"fixture")
            tesseract.write_bytes(b"fixture")
            source = root / "图片.png"
            Image.new("RGB", (80, 40), "white").save(source)

            def invalid_windows(
                _input: Path,
                output: Path,
                _timeout: float,
            ) -> OcrCommandResult:
                output.write_text('{"text":"缺少契约字段"}', encoding="utf-8")
                return OcrCommandResult(0, "", "")

            windows = WindowsTextOcrBackend(powershell, runner=invalid_windows)
            with self.assertRaisesRegex(TextOcrError, "无效结果"):
                windows.recognize(source, region=(0.0, 0.0, 1.0, 1.0))

            calls = 0

            def tesseract_runner(command: Sequence[str], _timeout: float) -> OcrCommandResult:
                nonlocal calls
                calls += 1
                if "--list-langs" in command:
                    return OcrCommandResult(0, "chi_sim\n", "")
                return OcrCommandResult(0, _tsv(("1", "1", "1", "94", "后备识别")), "")

            fallback = FallbackTextOcrBackend(
                (windows, TesseractTextOcrBackend(tesseract, runner=tesseract_runner))
            )
            result = fallback.recognize(source, region=(0.0, 0.0, 1.0, 1.0))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.text, "后备识别")
        self.assertEqual(result.backend_id, "tesseract-local")
        self.assertEqual(calls, 2)

    def test_local_backend_recognizes_chinese_lines_and_ascii_spacing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tesseract.exe"
            executable.write_bytes(b"fixture")
            source = root / "海报.png"
            Image.new("RGB", (120, 80), "white").save(source)
            calls = []

            def runner(command: Sequence[str], _timeout: float) -> OcrCommandResult:
                calls.append(tuple(command))
                if "--list-langs" in command:
                    return OcrCommandResult(0, "List of available languages\nchi_sim\neng\n", "")
                return OcrCommandResult(
                    0,
                    _tsv(
                        ("1", "1", "1", "95", "你好"),
                        ("1", "1", "1", "85", "世界"),
                        ("1", "1", "2", "90", "ABC"),
                        ("1", "1", "2", "80", "2026"),
                    ),
                    "",
                )

            backend = TesseractTextOcrBackend(executable, runner=runner)
            result = backend.recognize(source, region=(0.1, 0.1, 0.9, 0.9))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.text, "你好世界\nABC 2026")
        self.assertIsNotNone(result.confidence)
        assert result.confidence is not None
        self.assertAlmostEqual(result.confidence, 0.875)
        self.assertEqual(result.language, "chi_sim+eng")
        self.assertEqual(len(calls), 2)

    def test_empty_output_returns_none_and_language_lookup_is_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tesseract.exe"
            executable.write_bytes(b"fixture")
            source = root / "空白.png"
            Image.new("RGB", (20, 20), "white").save(source)
            language_calls = 0

            def runner(command: Sequence[str], _timeout: float) -> OcrCommandResult:
                nonlocal language_calls
                if "--list-langs" in command:
                    language_calls += 1
                    return OcrCommandResult(0, "eng\n", "")
                return OcrCommandResult(0, _tsv(), "")

            backend = TesseractTextOcrBackend(executable, runner=runner)
            self.assertIsNone(backend.recognize(source, region=(0.0, 0.0, 1.0, 1.0)))
            self.assertIsNone(backend.recognize(source, region=(0.0, 0.0, 1.0, 1.0)))

        self.assertEqual(language_calls, 1)

    def test_unavailable_language_and_failed_command_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tesseract.exe"
            executable.write_bytes(b"fixture")
            source = root / "图片.png"
            Image.new("RGB", (20, 20), "white").save(source)

            unavailable = TesseractTextOcrBackend(
                executable,
                runner=lambda _command, _timeout: OcrCommandResult(0, "fra\n", ""),
            )
            with self.assertRaises(TextOcrUnavailableError):
                unavailable.recognize(source, region=(0.0, 0.0, 1.0, 1.0))

            calls = 0

            def failing(command: Sequence[str], _timeout: float) -> OcrCommandResult:
                nonlocal calls
                calls += 1
                if calls == 1:
                    return OcrCommandResult(0, "eng\n", "")
                return OcrCommandResult(1, "", "模拟失败")

            failed = TesseractTextOcrBackend(executable, runner=failing)
            with self.assertRaisesRegex(TextOcrError, "模拟失败"):
                failed.recognize(source, region=(0.0, 0.0, 1.0, 1.0))

    def test_rotated_region_is_deskewed_before_local_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tesseract.exe"
            executable.write_bytes(b"fixture")
            source = root / "旋转文字.png"
            Image.new("RGB", (200, 100), "white").save(source)
            observed_sizes: list[tuple[int, int]] = []

            def runner(command: Sequence[str], _timeout: float) -> OcrCommandResult:
                if "--list-langs" in command:
                    return OcrCommandResult(0, "chi_sim\n", "")
                with Image.open(command[1]) as region:
                    observed_sizes.append(region.size)
                return OcrCommandResult(
                    0,
                    _tsv(("1", "1", "1", "91", "旋转标题")),
                    "",
                )

            backend = TesseractTextOcrBackend(executable, runner=runner)
            result = backend.recognize(
                source,
                region=(0.0, 0.0, 1.0, 1.0),
                rotation=30,
            )

        self.assertIsNotNone(result)
        self.assertEqual(len(observed_sizes), 1)
        self.assertGreater(observed_sizes[0][0], 200)
        self.assertGreater(observed_sizes[0][1], 100)

    def test_vertical_region_is_rotated_to_horizontal_before_local_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tesseract.exe"
            executable.write_bytes(b"fixture")
            source = root / "竖排文字.png"
            Image.new("RGB", (90, 260), "white").save(source)
            observed_sizes: list[tuple[int, int]] = []

            def runner(command: Sequence[str], _timeout: float) -> OcrCommandResult:
                if "--list-langs" in command:
                    return OcrCommandResult(0, "chi_sim\n", "")
                with Image.open(command[1]) as region:
                    observed_sizes.append(region.size)
                return OcrCommandResult(
                    0,
                    _tsv(("1", "1", "1", "92", "竖排标题")),
                    "",
                )

            backend = TesseractTextOcrBackend(executable, runner=runner)
            result = backend.recognize(
                source,
                region=(0.0, 0.0, 1.0, 1.0),
                rotation=90,
            )

        self.assertIsNotNone(result)
        self.assertEqual(observed_sizes, [(260, 90)])
        assert result is not None
        self.assertEqual(result.text, "竖排标题")

    def test_perspective_quad_is_rectified_before_local_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tesseract.exe"
            executable.write_bytes(b"fixture")
            source = root / "透视文字.png"
            Image.new("RGB", (300, 200), "white").save(source)
            observed_sizes: list[tuple[int, int]] = []

            def runner(command: Sequence[str], _timeout: float) -> OcrCommandResult:
                if "--list-langs" in command:
                    return OcrCommandResult(0, "chi_sim\n", "")
                with Image.open(command[1]) as region:
                    observed_sizes.append(region.size)
                return OcrCommandResult(
                    0,
                    _tsv(("1", "1", "1", "93", "透视标题")),
                    "",
                )

            backend = TesseractTextOcrBackend(executable, runner=runner)
            result = backend.recognize(
                source,
                region=(0.1, 0.1, 0.9, 0.85),
                rotation=22,
                quadrilateral=(0.2, 0.25, 0.8, 0.15, 0.85, 0.75, 0.15, 0.65),
            )

        self.assertIsNotNone(result)
        self.assertEqual(observed_sizes, [(196, 101)])
        assert result is not None
        self.assertEqual(result.text, "透视标题")
        self.assertEqual(result.geometry_correction, "perspective")

    def test_curved_baseline_is_rectified_before_local_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tesseract.exe"
            executable.write_bytes(b"fixture")
            observed: list[tuple[tuple[int, int], float]] = []

            def runner(command: Sequence[str], _timeout: float) -> OcrCommandResult:
                if "--list-langs" in command:
                    return OcrCommandResult(0, "chi_sim\n", "")
                with Image.open(command[1]) as region:
                    region.load()
                    observed.append((region.size, _baseline_delta(region)))
                return OcrCommandResult(
                    0,
                    _tsv(("1", "1", "1", "94", "弧形标题二零二六")),
                    "",
                )

            backend = TesseractTextOcrBackend(executable, runner=runner)
            for index, curve in enumerate((0.75, -0.75)):
                source = root / f"弧形文字-{index}.png"
                image = Image.new("RGBA", (680, 170), "white")
                try:
                    _draw_replacement_text(
                        image,
                        text="弧形文字测试二零二六",
                        font_family="微软雅黑",
                        font_size=70,
                        color="#202020",
                        align="center",
                        curve=curve,
                    )
                    before = abs(_baseline_delta(image))
                    image.save(source, "PNG")
                finally:
                    image.close()

                result = backend.recognize(
                    source,
                    region=(0.0, 0.0, 1.0, 1.0),
                    curve=curve,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.text, "弧形标题二零二六")
                self.assertEqual(result.geometry_correction, "curve")
                observed_size, after = observed[-1]
                self.assertEqual(observed_size, (680, 170))
                self.assertGreater(before, 8.0)
                self.assertLessEqual(abs(after), max(3.5, before * 0.35))

    def test_curved_ocr_contract_fails_closed_and_neutral_copy_is_stable(self) -> None:
        image = Image.new("RGB", (120, 40), "white")
        try:
            neutral = _rectify_curved_baseline(image, 0.0)
            try:
                self.assertEqual(neutral.tobytes(), image.tobytes())
                self.assertIsNot(neutral, image)
            finally:
                neutral.close()
            for invalid in (True, float("nan"), -1.01, 1.01):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):
                        _rectify_curved_baseline(image, invalid)  # type: ignore[arg-type]
        finally:
            image.close()

        with self.assertRaises(ValueError):
            TextOcrResult(
                "文字",
                0.5,
                "chi_sim",
                "简体中文",
                "fixture",
                geometry_correction="unknown",  # type: ignore[arg-type]
            )

    def test_default_unavailable_backend_and_result_validation(self) -> None:
        with patch("light_image_editor.services.text_ocr.shutil.which", return_value=None):
            backend = create_default_text_ocr_backend()
        self.assertIsInstance(backend, UnavailableTextOcrBackend)
        self.assertFalse(backend.available)
        with self.assertRaises(TextOcrUnavailableError):
            backend.recognize("missing.png", region=(0.0, 0.0, 1.0, 1.0))
        with self.assertRaises(ValueError):
            TextOcrResult("", 0.5, "chi_sim", "简体中文", "fixture")
        with self.assertRaises(ValueError):
            TextOcrResult(
                "文字",
                0.5,
                "chi_sim",
                "简体中文",
                "fixture",
                "unknown",  # type: ignore[arg-type]
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tesseract.exe"
            executable.write_bytes(b"fixture")
            source = root / "图片.png"
            Image.new("RGB", (20, 20), "white").save(source)
            local = TesseractTextOcrBackend(
                executable,
                runner=lambda _command, _timeout: OcrCommandResult(0, "eng\n", ""),
            )
            with self.assertRaises(ValueError):
                local.recognize(
                    source,
                    region=(0.0, 0.0, 1.0, 1.0),
                    rotation=float("nan"),
                )
            with self.assertRaisesRegex(ValueError, "-1 到 1"):
                local.recognize(
                    source,
                    region=(0.0, 0.0, 1.0, 1.0),
                    curve=1.01,
                )
            with self.assertRaisesRegex(ValueError, "不能与透视四角同时使用"):
                local.recognize(
                    source,
                    region=(0.0, 0.0, 1.0, 1.0),
                    quadrilateral=(0.1, 0.1, 0.9, 0.1, 0.9, 0.9, 0.1, 0.9),
                    curve=0.5,
                )


if __name__ == "__main__":
    unittest.main()
