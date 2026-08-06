"""Optional, local-only OCR backend for the smart text replacement workflow."""

from __future__ import annotations

import base64
import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from PIL import Image, ImageFilter, ImageOps, ImageStat

from ..domain.perspective_quad import NormalizedQuad, validate_normalized_quad

MAX_OCR_PIXELS = 80_000_000
OCR_TIMEOUT_SECONDS = 30.0
MAX_ENHANCED_OCR_DIMENSION = 2_400
MAX_OCR_ENHANCEMENT_SCALE = 3.0

OcrPreprocessing = Literal["standard", "adaptive-contrast"]
OcrGeometryCorrection = Literal["none", "perspective", "curve"]


class TextOcrError(RuntimeError):
    """Base error for local OCR execution or output parsing."""


class TextOcrUnavailableError(TextOcrError):
    """Raised when no supported local OCR runtime or language is available."""


@dataclass(frozen=True, slots=True)
class OcrCommandResult:
    returncode: int
    stdout: str
    stderr: str


OcrCommandRunner = Callable[[Sequence[str], float], OcrCommandResult]
WindowsOcrRunner = Callable[[Path, Path, float], OcrCommandResult]


WINDOWS_OCR_SCRIPT = r"""
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsTask" -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
})[0]
function Await-WinRt($operation, $resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.Wait()
    return $task.Result
}
$inputPath = $env:LIGHT_IMAGE_EDITOR_OCR_INPUT
$outputPath = $env:LIGHT_IMAGE_EDITOR_OCR_OUTPUT
if ([string]::IsNullOrWhiteSpace($inputPath) -or [string]::IsNullOrWhiteSpace($outputPath)) {
    throw "OCR input or output path is missing"
}
$fileOperation = [Windows.Storage.StorageFile]::GetFileFromPathAsync($inputPath)
$file = Await-WinRt $fileOperation ([Windows.Storage.StorageFile])
$streamOperation = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
$streamType = [Windows.Storage.Streams.IRandomAccessStream]
$stream = Await-WinRt $streamOperation $streamType
try {
    $decoderOperation = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
    $decoder = Await-WinRt $decoderOperation ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmapOperation = $decoder.GetSoftwareBitmapAsync()
    $bitmap = Await-WinRt $bitmapOperation ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        if ($null -eq $engine) { throw "No compatible Windows OCR language is installed" }
        $result = Await-WinRt ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $payload = [ordered]@{
            schema_version = 1
            text = $result.Text
            language = $engine.RecognizerLanguage.LanguageTag
            line_count = [int](@($result.Lines).Count)
        }
        $json = $payload | ConvertTo-Json -Compress
        [System.IO.File]::WriteAllText($outputPath, $json, [System.Text.UTF8Encoding]::new($false))
    } finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
    }
} finally {
    $stream.Dispose()
}
"""


@dataclass(frozen=True, slots=True)
class TextOcrResult:
    text: str
    confidence: float | None
    language: str
    language_label: str
    backend_id: str
    preprocessing: OcrPreprocessing = "standard"
    geometry_correction: OcrGeometryCorrection = "none"

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("文字识别结果不能为空")
        if len(self.text) > 2_000:
            raise ValueError("文字识别结果超过两千字符安全上限")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("文字识别置信度必须在 0 到 1 之间")
        for value in (self.language, self.language_label, self.backend_id):
            if not value.strip():
                raise ValueError("文字识别来源字段不能为空")
        if self.preprocessing not in {"standard", "adaptive-contrast"}:
            raise ValueError("文字识别预处理来源无效")
        if self.geometry_correction not in {"none", "perspective", "curve"}:
            raise ValueError("文字识别几何校正来源无效")


class TextOcrBackend(Protocol):
    @property
    def backend_id(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def available(self) -> bool: ...

    @property
    def unavailable_reason(self) -> str: ...

    def recognize(
        self,
        source: str | Path,
        *,
        region: tuple[float, float, float, float],
        rotation: float = 0.0,
        quadrilateral: NormalizedQuad | None = None,
        curve: float = 0.0,
    ) -> TextOcrResult | None: ...


@dataclass(frozen=True, slots=True)
class UnavailableTextOcrBackend:
    unavailable_reason: str = "本机未安装可用的离线文字识别组件"
    backend_id: str = "unavailable"
    display_name: str = "离线文字识别"
    available: bool = False

    def recognize(
        self,
        source: str | Path,
        *,
        region: tuple[float, float, float, float],
        rotation: float = 0.0,
        quadrilateral: NormalizedQuad | None = None,
        curve: float = 0.0,
    ) -> TextOcrResult | None:
        normalized_curve = _validated_curve(curve)
        if quadrilateral is not None and abs(normalized_curve) > 1e-9:
            raise ValueError("弧形文字校正不能与透视四角同时使用")
        del source, region, rotation, quadrilateral, normalized_curve
        raise TextOcrUnavailableError(self.unavailable_reason)


class WindowsTextOcrBackend:
    backend_id = "windows-system-ocr"
    display_name = "Windows 系统文字识别"
    available = True
    unavailable_reason = ""

    def __init__(
        self,
        executable: str | Path,
        *,
        runner: WindowsOcrRunner | None = None,
    ) -> None:
        self._executable = Path(executable).expanduser().resolve(strict=True)
        if not self._executable.is_file():
            raise ValueError("Windows OCR 执行程序路径必须指向文件")
        self._runner = runner or self._run_windows_ocr

    def recognize(
        self,
        source: str | Path,
        *,
        region: tuple[float, float, float, float],
        rotation: float = 0.0,
        quadrilateral: NormalizedQuad | None = None,
        curve: float = 0.0,
    ) -> TextOcrResult | None:
        normalized = _validated_region(region)
        normalized_rotation = _validated_rotation(rotation)
        normalized_curve = _validated_curve(curve)
        if quadrilateral is not None and abs(normalized_curve) > 1e-9:
            raise ValueError("弧形文字校正不能与透视四角同时使用")
        normalized_quad = (
            None
            if quadrilateral is None
            else validate_normalized_quad(quadrilateral, region=normalized)
        )
        path = Path(source).expanduser().resolve(strict=True)
        cropped = _prepare_ocr_image(
            path,
            region=normalized,
            rotation=normalized_rotation,
            quadrilateral=normalized_quad,
            curve=normalized_curve,
        )
        geometry_correction: OcrGeometryCorrection = (
            "perspective"
            if normalized_quad is not None
            else "curve"
            if abs(normalized_curve) >= 0.05
            else "none"
        )
        deadline = time.monotonic() + OCR_TIMEOUT_SECONDS
        try:
            result = self._recognize_prepared(
                cropped,
                preprocessing="standard",
                geometry_correction=geometry_correction,
                timeout_seconds=_remaining_ocr_timeout(deadline),
            )
            if result is not None:
                return result
            enhanced = _enhance_ocr_image(cropped)
            try:
                return self._recognize_prepared(
                    enhanced,
                    preprocessing="adaptive-contrast",
                    geometry_correction=geometry_correction,
                    timeout_seconds=_remaining_ocr_timeout(deadline),
                )
            finally:
                enhanced.close()
        finally:
            cropped.close()

    def _recognize_prepared(
        self,
        image: Image.Image,
        *,
        preprocessing: OcrPreprocessing,
        geometry_correction: OcrGeometryCorrection,
        timeout_seconds: float,
    ) -> TextOcrResult | None:
        with tempfile.TemporaryDirectory(prefix="light-image-editor-windows-ocr-") as directory:
            input_path = Path(directory) / "region.png"
            output_path = Path(directory) / "result.json"
            image.save(input_path, "PNG")
            try:
                completed = self._runner(input_path, output_path, timeout_seconds)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise TextOcrError("Windows 系统文字识别执行失败或超时") from error
            if completed.returncode != 0:
                message = completed.stderr.strip()[:200]
                if "No compatible Windows OCR language" in message:
                    raise TextOcrUnavailableError("Windows 未安装可用的中文或英文 OCR 语言")
                raise TextOcrError(f"Windows 系统文字识别失败：{message or '未知错误'}")
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise TextOcrError("Windows 系统文字识别返回了无效结果") from error
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "text",
            "language",
            "line_count",
        }:
            raise TextOcrError("Windows 系统文字识别返回了无效结果")
        if payload["schema_version"] != 1 or not isinstance(payload["text"], str):
            raise TextOcrError("Windows 系统文字识别返回了无效结果")
        if not isinstance(payload["language"], str) or not payload["language"].strip():
            raise TextOcrError("Windows 系统文字识别缺少语言信息")
        line_count = payload["line_count"]
        if line_count is None and not payload["text"].strip():
            line_count = 0
        if isinstance(line_count, bool) or not isinstance(line_count, int) or line_count < 0:
            raise TextOcrError("Windows 系统文字识别返回了无效行数")
        text = _normalize_windows_ocr_text(payload["text"])
        if not text:
            return None
        if len(text) > 2_000:
            raise TextOcrError("框内识别文字超过两千字符，请缩小选择区域")
        language = payload["language"].strip()
        return TextOcrResult(
            text=text,
            confidence=None,
            language=language,
            language_label=_windows_language_label(language),
            backend_id=self.backend_id,
            preprocessing=preprocessing,
            geometry_correction=geometry_correction,
        )

    def _run_windows_ocr(
        self,
        input_path: Path,
        output_path: Path,
        timeout_seconds: float,
    ) -> OcrCommandResult:
        encoded = base64.b64encode(WINDOWS_OCR_SCRIPT.encode("utf-16-le")).decode("ascii")
        environment = os.environ.copy()
        environment["LIGHT_IMAGE_EDITOR_OCR_INPUT"] = str(input_path)
        environment["LIGHT_IMAGE_EDITOR_OCR_OUTPUT"] = str(output_path)
        completed = subprocess.run(
            (
                str(self._executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            env=environment,
        )
        return OcrCommandResult(completed.returncode, completed.stdout, completed.stderr)


class FallbackTextOcrBackend:
    backend_id = "automatic-local-ocr"
    display_name = "自动本地文字识别"

    def __init__(self, backends: Sequence[TextOcrBackend]) -> None:
        self._backends = tuple(backend for backend in backends if backend.available)
        if not self._backends:
            raise ValueError("自动本地文字识别至少需要一个可用后端")

    @property
    def available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str:
        return ""

    def recognize(
        self,
        source: str | Path,
        *,
        region: tuple[float, float, float, float],
        rotation: float = 0.0,
        quadrilateral: NormalizedQuad | None = None,
        curve: float = 0.0,
    ) -> TextOcrResult | None:
        normalized_curve = _validated_curve(curve)
        if quadrilateral is not None and abs(normalized_curve) > 1e-9:
            raise ValueError("弧形文字校正不能与透视四角同时使用")
        errors: list[TextOcrError] = []
        for backend in self._backends:
            try:
                result = backend.recognize(
                    source,
                    region=region,
                    rotation=rotation,
                    quadrilateral=quadrilateral,
                    curve=normalized_curve,
                )
            except TextOcrError as error:
                errors.append(error)
                continue
            if result is not None:
                return result
        if errors and len(errors) == len(self._backends):
            raise errors[-1]
        return None


class TesseractTextOcrBackend:
    backend_id = "tesseract-local"
    display_name = "本地文字识别"
    available = True
    unavailable_reason = ""

    def __init__(
        self,
        executable: str | Path,
        *,
        runner: OcrCommandRunner | None = None,
    ) -> None:
        self._executable = Path(executable).expanduser().resolve(strict=True)
        if not self._executable.is_file():
            raise ValueError("文字识别程序路径必须指向文件")
        self._runner = runner or _run_command
        self._languages: tuple[str, str] | None = None

    def recognize(
        self,
        source: str | Path,
        *,
        region: tuple[float, float, float, float],
        rotation: float = 0.0,
        quadrilateral: NormalizedQuad | None = None,
        curve: float = 0.0,
    ) -> TextOcrResult | None:
        normalized = _validated_region(region)
        normalized_rotation = _validated_rotation(rotation)
        normalized_curve = _validated_curve(curve)
        if quadrilateral is not None and abs(normalized_curve) > 1e-9:
            raise ValueError("弧形文字校正不能与透视四角同时使用")
        normalized_quad = (
            None
            if quadrilateral is None
            else validate_normalized_quad(quadrilateral, region=normalized)
        )
        path = Path(source).expanduser().resolve(strict=True)
        language, language_label = self._selected_language()
        cropped = _prepare_ocr_image(
            path,
            region=normalized,
            rotation=normalized_rotation,
            quadrilateral=normalized_quad,
            curve=normalized_curve,
        )
        geometry_correction: OcrGeometryCorrection = (
            "perspective"
            if normalized_quad is not None
            else "curve"
            if abs(normalized_curve) >= 0.05
            else "none"
        )
        try:
            with tempfile.TemporaryDirectory(prefix="light-image-editor-ocr-") as directory:
                input_path = Path(directory) / "region.png"
                cropped.save(input_path, "PNG")
                command = (
                    str(self._executable),
                    str(input_path),
                    "stdout",
                    "-l",
                    language,
                    "--psm",
                    "6",
                    "tsv",
                )
                try:
                    completed = self._runner(command, OCR_TIMEOUT_SECONDS)
                except (OSError, subprocess.TimeoutExpired) as error:
                    raise TextOcrError("离线文字识别执行失败或超时") from error
        finally:
            cropped.close()
        if completed.returncode != 0:
            message = completed.stderr.strip()[:200]
            raise TextOcrError(f"离线文字识别失败：{message or '未知错误'}")
        parsed = _parse_tesseract_tsv(completed.stdout)
        if parsed is None:
            return None
        text, confidence = parsed
        return TextOcrResult(
            text=text,
            confidence=confidence,
            language=language,
            language_label=language_label,
            backend_id=self.backend_id,
            geometry_correction=geometry_correction,
        )

    def _selected_language(self) -> tuple[str, str]:
        if self._languages is not None:
            return self._languages
        try:
            completed = self._runner(
                (str(self._executable), "--list-langs"),
                5.0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TextOcrUnavailableError("无法读取本地文字识别语言包") from error
        if completed.returncode != 0:
            raise TextOcrUnavailableError("无法读取本地文字识别语言包")
        available = {
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip() and not line.casefold().startswith("list of available")
        }
        if "chi_sim" in available and "eng" in available:
            self._languages = ("chi_sim+eng", "简体中文和英文")
        elif "chi_sim" in available:
            self._languages = ("chi_sim", "简体中文")
        elif "chi_tra" in available and "eng" in available:
            self._languages = ("chi_tra+eng", "繁体中文和英文")
        elif "chi_tra" in available:
            self._languages = ("chi_tra", "繁体中文")
        elif "eng" in available:
            self._languages = ("eng", "英文")
        else:
            raise TextOcrUnavailableError("本地文字识别缺少中文或英文语言包")
        return self._languages


def create_default_text_ocr_backend() -> TextOcrBackend:
    backends: list[TextOcrBackend] = []
    if os.name == "nt":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is not None:
            try:
                backends.append(WindowsTextOcrBackend(powershell))
            except (OSError, ValueError):
                pass
    executable = shutil.which("tesseract")
    if executable is not None:
        try:
            backends.append(TesseractTextOcrBackend(executable))
        except (OSError, ValueError):
            pass
    if not backends:
        return UnavailableTextOcrBackend()
    if len(backends) == 1:
        return backends[0]
    return FallbackTextOcrBackend(backends)


def _run_command(command: Sequence[str], timeout_seconds: float) -> OcrCommandResult:
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NO_WINDOW
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
        creationflags=creation_flags,
    )
    return OcrCommandResult(completed.returncode, completed.stdout, completed.stderr)


def _validated_region(
    region: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    if not isinstance(region, tuple) or len(region) != 4:
        raise ValueError("文字识别区域必须包含四个坐标")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in region):
        raise ValueError("文字识别区域坐标必须是数字")
    left, top, right, bottom = (float(value) for value in region)
    if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
        raise ValueError("文字识别区域必须位于归一化画布范围内")
    return (left, top, right, bottom)


def _pixel_region(
    region: tuple[float, float, float, float],
    image_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    width, height = image_size
    left = max(0, min(width - 1, round(region[0] * width)))
    top = max(0, min(height - 1, round(region[1] * height)))
    right = max(left + 1, min(width, round(region[2] * width)))
    bottom = max(top + 1, min(height, round(region[3] * height)))
    return (left, top, right, bottom)


def _validated_rotation(rotation: float) -> float:
    if (
        isinstance(rotation, bool)
        or not isinstance(rotation, (int, float))
        or not math.isfinite(float(rotation))
        or not -180 <= float(rotation) <= 180
    ):
        raise ValueError("文字识别旋转角度必须位于 -180 到 180 度")
    return float(rotation)


def _validated_curve(curve: float) -> float:
    if (
        isinstance(curve, bool)
        or not isinstance(curve, (int, float))
        or not math.isfinite(float(curve))
        or not -1 <= float(curve) <= 1
    ):
        raise ValueError("文字识别弧度必须位于 -1 到 1 之间")
    return float(curve)


def _remaining_ocr_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TextOcrError("离线文字识别执行超时")
    return remaining


def _enhance_ocr_image(image: Image.Image) -> Image.Image:
    """Create a bounded high-contrast retry image without mutating the source crop."""

    width, height = image.size
    pixel_count = width * height
    dimension_scale = MAX_ENHANCED_OCR_DIMENSION / max(width, height)
    pixel_scale = math.sqrt(MAX_OCR_PIXELS / max(1, pixel_count))
    scale = min(MAX_OCR_ENHANCEMENT_SCALE, dimension_scale, pixel_scale)
    target_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    grayscale = ImageOps.grayscale(image)
    try:
        contrasted = ImageOps.autocontrast(grayscale)
        try:
            if contrasted.size == target_size:
                return contrasted.convert("RGB")
            resized = contrasted.resize(target_size, Image.Resampling.LANCZOS)
            try:
                return resized.convert("RGB")
            finally:
                resized.close()
        finally:
            contrasted.close()
    finally:
        grayscale.close()


def _prepare_ocr_image(
    path: Path,
    *,
    region: tuple[float, float, float, float],
    rotation: float,
    quadrilateral: NormalizedQuad | None,
    curve: float,
) -> Image.Image:
    with Image.open(path) as opened:
        width, height = opened.size
        if width < 1 or height < 1 or width * height > MAX_OCR_PIXELS:
            raise TextOcrError("图片尺寸超过离线文字识别安全上限")
        opened.load()
        oriented = ImageOps.exif_transpose(opened)
        try:
            if quadrilateral is None:
                left, top, right, bottom = _pixel_region(region, oriented.size)
                cropped = oriented.crop((left, top, right, bottom)).convert("RGB")
            else:
                cropped = _rectify_quadrilateral(oriented, quadrilateral)
        finally:
            if oriented is not opened:
                oriented.close()
    if quadrilateral is None and abs(rotation) >= 0.05:
        background = _average_background(cropped)
        deskewed = cropped.rotate(
            -rotation,
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor=background,
        )
        cropped.close()
        cropped = deskewed
    if abs(curve) >= 0.05:
        rectified = _rectify_curved_baseline(cropped, curve)
        cropped.close()
        return rectified
    return cropped


def _average_background(image: Image.Image) -> tuple[int, int, int]:
    sample = image.resize((1, 1), Image.Resampling.BOX)
    try:
        return cast(
            tuple[int, int, int],
            tuple(round(value) for value in ImageStat.Stat(sample).mean[:3]),
        )
    finally:
        sample.close()


def _curve_content_box(image: Image.Image) -> tuple[int, int, int, int]:
    """Find a conservative edge envelope for a controlled printed-text crop."""

    width, height = image.size
    fallback = (0, 0, width, height)
    if width < 12 or height < 8:
        return fallback
    grayscale = ImageOps.grayscale(image)
    try:
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        try:
            inner = edges.crop((1, 1, width - 1, height - 1))
            try:
                contrasted = ImageOps.autocontrast(inner, cutoff=1)
                try:
                    mask = contrasted.point(lambda value: 255 if value >= 32 else 0)
                    try:
                        box = mask.getbbox()
                    finally:
                        mask.close()
                finally:
                    contrasted.close()
            finally:
                inner.close()
        finally:
            edges.close()
    finally:
        grayscale.close()
    if box is None:
        return fallback
    left, top, right, bottom = (box[0] + 1, box[1] + 1, box[2] + 1, box[3] + 1)
    if right - left < max(12, round(width * 0.25)) or bottom - top < 4:
        return fallback
    return (left, top, right, bottom)


def _rectify_curved_baseline(image: Image.Image, curve: float) -> Image.Image:
    """Flatten a controlled single-line quadratic baseline for local OCR.

    ``curve`` uses the same signed -1..1 contract as automatic text-region
    suggestions and replacement drafts. The detector encodes baseline displacement
    quadratically, so the inverse displacement below mirrors that calibration.
    """

    normalized_curve = _validated_curve(curve)
    if abs(normalized_curve) < 0.05:
        return image.copy()
    width, height = image.size
    if width < 12 or height < 8 or width * height > MAX_OCR_PIXELS:
        raise TextOcrError("弧形文字校正区域尺寸无效或超过安全上限")
    left, top, right, bottom = _curve_content_box(image)
    content_width = max(2.0, float(right - left))
    content_height = max(4.0, float(bottom - top))
    displacement = math.copysign(
        min(
            content_height * 0.42,
            0.54 * (abs(normalized_curve) / 0.9) ** 2 * content_height,
        ),
        normalized_curve,
    )
    center_y = (top + bottom) / 2

    def source_point(x: float, y: float) -> tuple[float, float]:
        if x < left or x > right:
            return (x, y)
        progress = min(1.0, max(0.0, (x - left) / content_width))
        centered = progress - 0.5
        vertical_offset = displacement * (4 * centered**2 - 1)
        tangent = displacement * 8 * centered / content_width
        angle = math.atan(tangent)
        distance = y - center_y
        return (
            x - distance * math.sin(angle),
            center_y + vertical_offset + distance * math.cos(angle),
        )

    step = max(2, math.ceil(width / min(160, max(24, math.ceil(content_width / 6)))))
    boundaries = sorted({0, width, left, right, *range(0, width + 1, step)})
    mesh: list[
        tuple[
            tuple[int, int, int, int],
            tuple[float, float, float, float, float, float, float, float],
        ]
    ] = []
    for destination_left, destination_right in zip(boundaries, boundaries[1:]):
        if destination_right <= destination_left:
            continue
        top_left = source_point(float(destination_left), 0.0)
        bottom_left = source_point(float(destination_left), float(height))
        bottom_right = source_point(float(destination_right), float(height))
        top_right = source_point(float(destination_right), 0.0)
        mesh.append(
            (
                (destination_left, 0, destination_right, height),
                (
                    top_left[0],
                    top_left[1],
                    bottom_left[0],
                    bottom_left[1],
                    bottom_right[0],
                    bottom_right[1],
                    top_right[0],
                    top_right[1],
                ),
            )
        )
    return image.transform(
        image.size,
        Image.Transform.MESH,
        mesh,
        resample=Image.Resampling.BICUBIC,
        fillcolor=_average_background(image),
    ).convert("RGB")


def _rectify_quadrilateral(image: Image.Image, quad: NormalizedQuad) -> Image.Image:
    width, height = image.size
    points = [(quad[index] * width, quad[index + 1] * height) for index in range(0, 8, 2)]
    top_left, top_right, bottom_right, bottom_left = points
    top_width = math.dist(top_left, top_right)
    bottom_width = math.dist(bottom_left, bottom_right)
    left_height = math.dist(top_left, bottom_left)
    right_height = math.dist(top_right, bottom_right)
    output_width = max(2, round((top_width + bottom_width) / 2))
    output_height = max(2, round((left_height + right_height) / 2))
    if output_width * output_height > MAX_OCR_PIXELS:
        raise TextOcrError("透视校正后的文字区域超过安全上限")
    pil_quad = (
        top_left[0],
        top_left[1],
        bottom_left[0],
        bottom_left[1],
        bottom_right[0],
        bottom_right[1],
        top_right[0],
        top_right[1],
    )
    return image.transform(
        (output_width, output_height),
        Image.Transform.QUAD,
        pil_quad,
        resample=Image.Resampling.BICUBIC,
    ).convert("RGB")


def _parse_tesseract_tsv(raw: str) -> tuple[str, float] | None:
    reader = csv.DictReader(io.StringIO(raw), delimiter="\t")
    required = {"block_num", "par_num", "line_num", "conf", "text"}
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise TextOcrError("离线文字识别返回了无效结果")
    lines: dict[tuple[str, str, str], list[str]] = {}
    confidences: list[float] = []
    for row in reader:
        token = (row.get("text") or "").strip()
        if not token:
            continue
        try:
            confidence = float(row.get("conf") or "-1")
        except ValueError:
            continue
        if confidence < 0:
            continue
        key = (
            row.get("block_num") or "0",
            row.get("par_num") or "0",
            row.get("line_num") or "0",
        )
        lines.setdefault(key, []).append(token)
        confidences.append(min(100.0, confidence))
    rendered_lines = [_join_ocr_tokens(tokens) for tokens in lines.values() if tokens]
    text = "\n".join(line for line in rendered_lines if line).strip()
    if not text:
        return None
    if len(text) > 2_000:
        raise TextOcrError("框内识别文字超过两千字符，请缩小选择区域")
    confidence = sum(confidences) / max(1, len(confidences)) / 100
    return (text, confidence)


def _join_ocr_tokens(tokens: Sequence[str]) -> str:
    output = ""
    for token in tokens:
        if output and _needs_space(output[-1], token[0]):
            output += " "
        output += token
    return output


def _needs_space(previous: str, current: str) -> bool:
    return previous.isascii() and current.isascii() and previous.isalnum() and current.isalnum()


def _normalize_windows_ocr_text(raw: str) -> str:
    lines = []
    for raw_line in raw.replace("\r", "").split("\n"):
        line = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", raw_line.strip())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _windows_language_label(language: str) -> str:
    normalized = language.casefold()
    if normalized.startswith("zh-hans") or normalized.startswith("zh-cn"):
        return "简体中文（Windows）"
    if normalized.startswith("zh-hant") or normalized.startswith("zh-tw"):
        return "繁体中文（Windows）"
    if normalized.startswith("en"):
        return "英文（Windows）"
    return f"{language}（Windows）"


__all__ = [
    "FallbackTextOcrBackend",
    "OcrCommandResult",
    "TesseractTextOcrBackend",
    "TextOcrBackend",
    "TextOcrError",
    "TextOcrResult",
    "TextOcrUnavailableError",
    "UnavailableTextOcrBackend",
    "WindowsTextOcrBackend",
    "create_default_text_ocr_backend",
]
