"""Run the Windows 10/11 built-in Chinese OCR acceptance gate."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageDraw, ImageFont

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmarks.run_benchmarks import _metric
from light_image_editor.domain import NormalizedQuad
from light_image_editor.providers.mock_provider import _warp_surface_to_quad
from light_image_editor.services.text_ocr import TextOcrBackend, WindowsTextOcrBackend
from light_image_editor.version import __version__

EXPECTED_TEXT = "轻量图片编辑器 2026"
MAXIMUM_P95_MS = 5_000.0
PROFILES = {"smoke": 1, "standard": 3}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def _text_layer() -> Image.Image:
    layer = Image.new("RGBA", (900, 220), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((30, 55), EXPECTED_TEXT, font=_font(76), fill="black")
    return layer


def _rotation_fixture(
    root: Path,
    *,
    angle: float,
) -> tuple[Path, tuple[float, float, float, float]]:
    layer = _text_layer()
    rotated = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    canvas = Image.new(
        "RGB",
        (max(1_100, rotated.width + 100), max(700, rotated.height + 100)),
        "white",
    )
    left = (canvas.width - rotated.width) // 2
    top = (canvas.height - rotated.height) // 2
    canvas.paste(rotated, (left, top), rotated)
    path = root / f"windows-ocr-{angle:+.0f}.png"
    canvas.save(path, "PNG")
    region = (
        left / canvas.width,
        top / canvas.height,
        (left + rotated.width) / canvas.width,
        (top + rotated.height) / canvas.height,
    )
    layer.close()
    rotated.close()
    canvas.close()
    return (path, region)


def _perspective_fixture(
    root: Path,
) -> tuple[Path, tuple[float, float, float, float], NormalizedQuad]:
    size = (1_100, 600)
    layer = _text_layer()
    pixel_quad = (120.0, 160.0, 930.0, 80.0, 1_010.0, 500.0, 80.0, 410.0)
    canvas = Image.new("RGBA", size, "white")
    warped = _warp_surface_to_quad(
        layer,
        output_size=size,
        destination=pixel_quad,
    )
    canvas.alpha_composite(warped)
    path = root / "windows-ocr-perspective.png"
    canvas.convert("RGB").save(path, "PNG")
    quad = cast(
        NormalizedQuad,
        tuple(
            value / (size[0] if index % 2 == 0 else size[1])
            for index, value in enumerate(pixel_quad)
        ),
    )
    region = (min(quad[0::2]), min(quad[1::2]), max(quad[0::2]), max(quad[1::2]))
    layer.close()
    warped.close()
    canvas.close()
    return (path, region, quad)


def _complex_background_fixtures(
    root: Path,
) -> list[tuple[str, Path, tuple[float, float, float, float], bool]]:
    fixtures: list[tuple[str, Path, tuple[float, float, float, float], bool]] = []
    full_region = (0.0, 0.0, 1.0, 1.0)

    gradient = Image.new("RGB", (1_000, 240))
    gradient_draw = ImageDraw.Draw(gradient)
    for x in range(gradient.width):
        ratio = x / max(1, gradient.width - 1)
        gradient_draw.line(
            (x, 0, x, gradient.height),
            fill=(round(245 - 35 * ratio), round(235 + 12 * ratio), round(210 + 35 * ratio)),
        )
    gradient_draw.text((35, 70), EXPECTED_TEXT, font=_font(64), fill=(25, 40, 80))
    gradient_path = root / "windows-ocr-gradient.png"
    gradient.save(gradient_path, "PNG")
    gradient.close()
    fixtures.append(("gradient_background", gradient_path, full_region, False))

    patterned = Image.new("RGB", (1_000, 240), (238, 235, 226))
    pattern_draw = ImageDraw.Draw(patterned)
    for x in range(-260, 1_200, 42):
        pattern_draw.line((x, 0, x + 260, 260), fill=(205, 220, 218), width=7)
    for x in range(20, patterned.width, 90):
        pattern_draw.ellipse((x, 18, x + 22, 40), fill=(224, 190, 180))
    pattern_draw.text(
        (35, 70),
        EXPECTED_TEXT,
        font=_font(64),
        fill=(35, 45, 55),
        stroke_width=1,
        stroke_fill="white",
    )
    pattern_path = root / "windows-ocr-pattern.png"
    patterned.save(pattern_path, "PNG")
    patterned.close()
    fixtures.append(("pattern_background", pattern_path, full_region, False))

    low_contrast = Image.new("RGB", (700, 130), (125, 132, 140))
    ImageDraw.Draw(low_contrast).text(
        (20, 40),
        EXPECTED_TEXT,
        font=_font(22),
        fill=(107, 114, 122),
    )
    low_contrast_path = root / "windows-ocr-low-contrast-small.png"
    low_contrast.save(low_contrast_path, "PNG")
    low_contrast.close()
    fixtures.append(("low_contrast_small", low_contrast_path, full_region, True))
    return fixtures


def evaluate_windows_ocr_gate(result: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if int(result.get("case_count", 0)) < 7:
        violations.append("Windows OCR 样本数量不足")
    for case in result.get("cases", []):
        name = str(case.get("name", "未知样本"))
        if not bool(case.get("recognized")):
            violations.append(f"Windows OCR {name} 未识别到文字")
        elif not bool(case.get("exact_match")):
            violations.append(f"Windows OCR {name} 中文结果不准确")
        if case.get("backend_id") != "windows-system-ocr":
            violations.append(f"Windows OCR {name} 未使用系统后端")
        if float(case.get("elapsed_ms", {}).get("p95", MAXIMUM_P95_MS + 1)) > MAXIMUM_P95_MS:
            violations.append(f"Windows OCR {name} P95 超过性能门槛")
        if not bool(case.get("deterministic")):
            violations.append(f"Windows OCR {name} 结果不可复现")
        if bool(case.get("adaptive_retry_required")) and case.get("preprocessing") != (
            "adaptive-contrast"
        ):
            violations.append(f"Windows OCR {name} 未走低对比增强重试")
        if not bool(case.get("preprocessing_deterministic")):
            violations.append(f"Windows OCR {name} 预处理路径不可复现")
    return violations


def run(
    profile: str = "standard",
    *,
    backend: TextOcrBackend | None = None,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"未知 Windows OCR 门禁档位：{profile}")
    repetitions = PROFILES[profile]
    selected = backend
    if selected is None:
        executable = shutil.which("powershell.exe") or shutil.which("powershell")
        if executable is None:
            raise RuntimeError("未找到 Windows PowerShell，无法执行系统 OCR 门禁")
        selected = WindowsTextOcrBackend(executable)
    with tempfile.TemporaryDirectory(prefix="light-image-editor-windows-ocr-gate-") as directory:
        root = Path(directory)
        fixtures: list[
            tuple[
                str,
                Path,
                tuple[float, float, float, float],
                float,
                NormalizedQuad | None,
                bool,
            ]
        ] = []
        horizontal_path, horizontal_region = _rotation_fixture(root, angle=0)
        rotated_path, rotated_region = _rotation_fixture(root, angle=30)
        vertical_path, vertical_region = _rotation_fixture(root, angle=90)
        perspective_path, perspective_region, perspective_quad = _perspective_fixture(root)
        fixtures.extend(
            (
                ("horizontal", horizontal_path, horizontal_region, 0.0, None, False),
                ("rotation_30", rotated_path, rotated_region, 30.0, None, False),
                ("vertical_90", vertical_path, vertical_region, 90.0, None, False),
                (
                    "perspective",
                    perspective_path,
                    perspective_region,
                    0.0,
                    perspective_quad,
                    False,
                ),
            )
        )
        fixtures.extend(
            (name, path, region, 0.0, None, adaptive_required)
            for name, path, region, adaptive_required in _complex_background_fixtures(root)
        )
        cases: list[dict[str, Any]] = []
        for name, path, region, rotation, quadrilateral, adaptive_required in fixtures:
            texts: list[str | None] = []
            backend_ids: list[str | None] = []
            languages: list[str | None] = []
            preprocessing: list[str | None] = []
            elapsed: list[float] = []
            for _repetition in range(repetitions):
                started = time.perf_counter()
                result = selected.recognize(
                    path,
                    region=region,
                    rotation=rotation,
                    quadrilateral=quadrilateral,
                )
                elapsed.append((time.perf_counter() - started) * 1_000)
                texts.append(None if result is None else result.text)
                backend_ids.append(None if result is None else result.backend_id)
                languages.append(None if result is None else result.language)
                preprocessing.append(None if result is None else result.preprocessing)
            cases.append(
                {
                    "name": name,
                    "recognized": texts[0] is not None,
                    "text": texts[0],
                    "expected_text": EXPECTED_TEXT,
                    "exact_match": texts[0] == EXPECTED_TEXT,
                    "backend_id": backend_ids[0],
                    "language": languages[0],
                    "preprocessing": preprocessing[0],
                    "adaptive_retry_required": adaptive_required,
                    "confidence_available": False,
                    "deterministic": len(set(texts)) == 1,
                    "preprocessing_deterministic": len(set(preprocessing)) == 1,
                    "elapsed_ms": _metric(elapsed),
                }
            )
    document: dict[str, Any] = {
        "schema_version": 1,
        "application_version": __version__,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "profile": profile,
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
        },
        "case_count": len(cases),
        "cases": cases,
        "thresholds": {"maximum_p95_ms": MAXIMUM_P95_MS},
        "scope_limit": (
            "Windows 10/11 内置 OCR 的本机普通印刷体及受控渐变、图案、低对比小字基线；"
            "系统 API 不提供词级可信度，不代表真实照片复杂背景、艺术字、手写体或任意场景生产质量"
        ),
    }
    document["violations"] = evaluate_windows_ocr_gate(document)
    document["passed"] = not document["violations"]
    return document


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="standard")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-fail", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    result = run(arguments.profile)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if result["passed"] or arguments.no_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
