"""On-demand local image enhancement helpers used by the lightweight viewer.

The heavy inference runtime and model files live below the user's local app-data
directory.  Importing this module never imports ONNX Runtime and therefore does
not increase the viewer's normal startup cost or resident memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

UPSCAYL_RUNTIME_VERSION = "20251207-174704"
UPSCAYL_RUNTIME_URL = (
    "https://github.com/upscayl/upscayl-ncnn/releases/download/"
    f"{UPSCAYL_RUNTIME_VERSION}/upscayl-bin-{UPSCAYL_RUNTIME_VERSION}-windows.zip"
)
UPSCAYL_RUNTIME_SHA256 = "1f0f65c5d2ade866555e2ac467d35952c35a47080a4060fe56a1ab028f67258a"
UPSCAYL_MODEL_VERSION = "v2.15.1"
UPSCAYL_MODEL = "upscayl-standard-4x"
UPSCAYL_MODEL_BASE_URL = (
    "https://raw.githubusercontent.com/upscayl/upscayl/refs/tags/"
    f"{UPSCAYL_MODEL_VERSION}/resources/models"
)
UPSCAYL_MODEL_FILES = {
    f"{UPSCAYL_MODEL}.bin": 33_424_520,
    f"{UPSCAYL_MODEL}.param": 116_029,
}
UPSCAYL_DOWNLOAD_NOTE = "约 45 MB"

BIREFNET_MODEL_NAME = "onnx-community/BiRefNet_lite-ONNX"
BIREFNET_MODEL_URL = (
    "https://huggingface.co/onnx-community/BiRefNet_lite-ONNX/resolve/main/onnx/model.onnx"
)
BIREFNET_MODEL_SIZE = 224_005_088
BIREFNET_MODEL_SHA256 = "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333"
BIREFNET_DOWNLOAD_NOTE = "约 214 MB"

MAX_OUTPUT_EDGE = 8192
MAX_OUTPUT_PIXELS = MAX_OUTPUT_EDGE * MAX_OUTPUT_EDGE
TARGET_LONG_EDGES = {"2K": 2048, "4K": 4096, "8K": 8192}

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class UpscaleTarget:
    resolution: str
    width: int
    height: int
    ratio: float


@dataclass(frozen=True)
class LocalToolStatus:
    available: bool
    engine: str
    model: str
    detail: str = ""


def local_tools_root() -> Path:
    configured = os.environ.get("CSIMAGE_LOCAL_TOOLS")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return (base / "CSImage" / "local-tools").resolve()


def calculate_upscale_target(width: int, height: int, resolution: str) -> UpscaleTarget:
    normalized = str(resolution).strip().upper()
    if width < 1 or height < 1:
        raise ValueError("原图宽高无效，无法计算高清尺寸。")
    if normalized not in TARGET_LONG_EDGES:
        raise ValueError("高清优化仅支持 2K、4K 或 8K。")
    target_long_edge = TARGET_LONG_EDGES[normalized]
    ratio = target_long_edge / max(width, height)
    target_width = max(1, round(width * ratio))
    target_height = max(1, round(height * ratio))
    if (
        target_width > MAX_OUTPUT_EDGE
        or target_height > MAX_OUTPUT_EDGE
        or target_width * target_height > MAX_OUTPUT_PIXELS
    ):
        raise ValueError("目标尺寸超过本地高清工具的 8K 安全上限。")
    return UpscaleTarget(normalized, target_width, target_height, ratio)


def _expected_file(path: Path, *, size: int | None = None, minimum: int = 1) -> bool:
    try:
        actual = path.stat().st_size
    except OSError:
        return False
    return actual == size if size is not None else actual >= minimum


def birefnet_model_path() -> Path:
    return local_tools_root() / "birefnet-lite" / "model.onnx"


def birefnet_status() -> LocalToolStatus:
    model = birefnet_model_path()
    ready = _expected_file(model, size=BIREFNET_MODEL_SIZE)
    return LocalToolStatus(
        available=ready,
        engine="ONNX Runtime（本地 CPU）",
        model="BiRefNet Lite · fp32",
        detail="模型已就绪" if ready else f"首次使用下载 {BIREFNET_DOWNLOAD_NOTE}",
    )


def _upscayl_paths() -> tuple[Path, Path]:
    root = local_tools_root() / "upscayl"
    return root / "bin" / "upscayl-bin.exe", root / "models"


def _has_upscayl_models(directory: Path) -> bool:
    return all(
        _expected_file(directory / filename, size=size)
        for filename, size in UPSCAYL_MODEL_FILES.items()
    )


def _find_upscayl_runtime() -> tuple[Path | None, Path | None]:
    local_binary, local_models = _upscayl_paths()
    configured_binary = os.environ.get("UPSCAYL_BIN_PATH") or os.environ.get("UPSCAYL_BIN")
    configured_models = os.environ.get("UPSCAYL_MODELS_PATH")
    program_files = os.environ.get("ProgramFiles") or os.environ.get("PROGRAMFILES")
    local_app_data = os.environ.get("LOCALAPPDATA")
    binary_candidates = [
        Path(configured_binary) if configured_binary else None,
        local_binary,
        Path(program_files) / "Upscayl" / "resources" / "bin" / "upscayl-bin.exe"
        if program_files
        else None,
        Path(local_app_data) / "Programs" / "Upscayl" / "resources" / "bin" / "upscayl-bin.exe"
        if local_app_data
        else None,
    ]
    binary = next(
        (
            candidate.resolve()
            for candidate in binary_candidates
            if candidate and _expected_file(candidate, minimum=262_144)
        ),
        None,
    )
    model_candidates = [
        Path(configured_models) if configured_models else None,
        local_models,
        binary.parent / "models" if binary else None,
        binary.parent.parent / "models" if binary else None,
    ]
    models = next(
        (
            candidate.resolve()
            for candidate in model_candidates
            if candidate and _has_upscayl_models(candidate)
        ),
        None,
    )
    return binary, models


def upscayl_status() -> LocalToolStatus:
    binary, models = _find_upscayl_runtime()
    ready = binary is not None and models is not None
    return LocalToolStatus(
        available=ready,
        engine="Upscayl NCNN · Vulkan",
        model=f"{UPSCAYL_MODEL} · 4×",
        detail="引擎已就绪" if ready else f"首次使用下载 {UPSCAYL_DOWNLOAD_NOTE}，需要 Vulkan 显卡",
    )


def _download_verified(
    url: str,
    target: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.part")
    last_error: Exception | None = None
    for _attempt in range(5):
        try:
            existing = partial.stat().st_size if partial.exists() else 0
            headers = {"User-Agent": "CSImage/2.0"}
            if existing:
                headers["Range"] = f"bytes={existing}-"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=90) as response:
                resumed = existing > 0 and getattr(response, "status", 200) == 206
                mode = "ab" if resumed else "wb"
                with partial.open(mode) as stream:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
            actual_size = partial.stat().st_size
            if expected_size is not None and actual_size != expected_size:
                raise RuntimeError(
                    f"下载文件大小异常：应为 {expected_size} 字节，实际为 {actual_size} 字节。"
                )
            if expected_sha256 is not None:
                digest = hashlib.sha256()
                with partial.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest().casefold() != expected_sha256.casefold():
                    partial.unlink(missing_ok=True)
                    raise RuntimeError("下载文件的 SHA-256 校验失败。")
            os.replace(partial, target)
            return target
        except Exception as error:
            last_error = error
    raise RuntimeError(f"下载失败，已自动重试 5 次：{last_error}")


def ensure_birefnet_model(progress: ProgressCallback | None = None) -> Path:
    model = birefnet_model_path()
    if _expected_file(model, size=BIREFNET_MODEL_SIZE):
        return model
    if progress:
        progress(f"正在下载 BiRefNet Lite 模型（{BIREFNET_DOWNLOAD_NOTE}）…")
    return _download_verified(
        BIREFNET_MODEL_URL,
        model,
        expected_size=BIREFNET_MODEL_SIZE,
        expected_sha256=BIREFNET_MODEL_SHA256,
    )


def ensure_upscayl_runtime(progress: ProgressCallback | None = None) -> tuple[Path, Path]:
    binary, models = _find_upscayl_runtime()
    if binary is not None and models is not None:
        return binary, models
    local_binary, local_models = _upscayl_paths()
    root = local_binary.parent.parent
    with tempfile.TemporaryDirectory(prefix="csimage-upscayl-") as temp_name:
        temporary = Path(temp_name)
        if not _expected_file(local_binary, minimum=262_144):
            if progress:
                progress("正在下载 Upscayl 本地引擎…")
            archive = _download_verified(
                UPSCAYL_RUNTIME_URL,
                temporary / "upscayl.zip",
                expected_sha256=UPSCAYL_RUNTIME_SHA256,
            )
            extract_root = temporary / "runtime"
            with zipfile.ZipFile(archive) as package:
                package.extractall(extract_root)
            extracted = next(extract_root.rglob("upscayl-bin.exe"), None)
            if extracted is None or not _expected_file(extracted, minimum=262_144):
                raise RuntimeError("官方 Upscayl 压缩包中没有可用的 Windows 引擎。")
            local_binary.parent.mkdir(parents=True, exist_ok=True)
            for source in extracted.parent.iterdir():
                if source.is_file():
                    shutil.copy2(source, local_binary.parent / source.name)
        local_models.mkdir(parents=True, exist_ok=True)
        for filename, size in UPSCAYL_MODEL_FILES.items():
            target = local_models / filename
            if _expected_file(target, size=size):
                continue
            if progress:
                progress(f"正在下载 Upscayl 标准模型 · {filename}…")
            _download_verified(
                f"{UPSCAYL_MODEL_BASE_URL}/{filename}",
                target,
                expected_size=size,
            )
    binary, models = _find_upscayl_runtime()
    if binary is None or models is None:
        raise RuntimeError(f"Upscayl 本地引擎安装校验失败：{root}")
    return binary, models


def unique_output_path(directory: Path, stem: str, suffix: str = ".png") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{index}{suffix}"
        index += 1
    return candidate


def upscale_image(
    source: Path,
    output: Path,
    resolution: str,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[int, int]:
    with Image.open(source) as decoded:
        normalized = ImageOps.exif_transpose(decoded).convert("RGBA")
    target = calculate_upscale_target(normalized.width, normalized.height, resolution)
    output.parent.mkdir(parents=True, exist_ok=True)
    if target.ratio <= 1:
        normalized.resize((target.width, target.height), Image.Resampling.LANCZOS).save(
            output, "PNG"
        )
        normalized.close()
        return target.width, target.height
    binary, models = ensure_upscayl_runtime(progress)
    with tempfile.TemporaryDirectory(prefix="csimage-upscale-") as temp_name:
        temporary = Path(temp_name)
        input_path = temporary / "input.png"
        raw_output = temporary / "output.png"
        normalized.save(input_path, "PNG")
        normalized.close()
        if progress:
            progress(f"Upscayl 正在生成 {target.resolution} 高清图片…")
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        completed = subprocess.run(
            [
                str(binary),
                "-i",
                str(input_path),
                "-o",
                str(raw_output),
                "-r",
                f"{target.width}x{target.height}",
                "-m",
                str(models),
                "-n",
                UPSCAYL_MODEL,
                "-t",
                "0",
                "-c",
                "0",
                "-f",
                "png",
            ],
            capture_output=True,
            text=True,
            timeout=600,
            startupinfo=startupinfo,
            check=False,
        )
        if completed.returncode != 0 or not raw_output.is_file():
            detail = (completed.stderr or completed.stdout).replace("\n", " ").strip()[-600:]
            raise RuntimeError(
                "Upscayl 运行失败。请确认显卡支持 Vulkan 且驱动正常。"
                + (f"\n{detail}" if detail else "")
            )
        with Image.open(raw_output) as generated:
            result = generated.convert("RGBA")
            if result.size != (target.width, target.height):
                result = result.resize((target.width, target.height), Image.Resampling.LANCZOS)
            result.save(output, "PNG")
            result.close()
    return target.width, target.height


def compose_original_rgb_with_alpha(source: Image.Image, mask: Image.Image) -> Image.Image:
    source_rgba = source.convert("RGBA")
    resized_mask = mask.convert("L")
    if resized_mask.size != source_rgba.size:
        resized_mask = resized_mask.resize(source_rgba.size, Image.Resampling.LANCZOS)
    source_alpha = source_rgba.getchannel("A")
    combined_alpha = Image.eval(
        Image.fromarray(
            (
                __import__("numpy").asarray(source_alpha, dtype="uint16")
                * __import__("numpy").asarray(resized_mask, dtype="uint16")
                // 255
            ).astype("uint8")
        ),
        lambda value: value,
    )
    source_rgba.putalpha(combined_alpha)
    return source_rgba


def remove_background(
    source: Path,
    output: Path,
    *,
    progress: ProgressCallback | None = None,
) -> None:
    model_path = ensure_birefnet_model(progress)
    if progress:
        progress("BiRefNet Lite 正在识别前景边缘…")
    import numpy as np
    import onnxruntime as ort

    with Image.open(source) as decoded:
        original = ImageOps.exif_transpose(decoded).convert("RGBA")
    rgb = original.convert("RGB")
    resized = rgb.resize((1024, 1024), Image.Resampling.BILINEAR)
    tensor: Any = np.asarray(resized, dtype=np.float32) / 255.0
    tensor = (tensor - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
        [0.229, 0.224, 0.225], dtype=np.float32
    )
    tensor = np.transpose(tensor, (2, 0, 1))[None, ...]
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
    session = ort.InferenceSession(
        str(model_path),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    input_name = session.get_inputs()[0].name
    logits = np.asarray(session.run(None, {input_name: tensor})[0]).squeeze()
    logits = np.clip(logits, -60.0, 60.0)
    alpha = 1.0 / (1.0 + np.exp(-logits))
    mask = Image.fromarray(np.clip(alpha * 255.0, 0, 255).astype(np.uint8), mode="L")
    result = compose_original_rgb_with_alpha(original, mask)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.save(output, "PNG", optimize=True)
    result.close()
    mask.close()
    resized.close()
    rgb.close()
    original.close()


def worker_command(
    operation: str,
    source: Path,
    output: Path,
    resolution: str = "",
    result_file: Path | None = None,
) -> list[str]:
    arguments = ["--local-ai-worker", operation, "--source", str(source), "--output", str(output)]
    if resolution:
        arguments.extend(["--resolution", resolution])
    if result_file is not None:
        arguments.extend(["--result-file", str(result_file)])
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    entrypoint = Path(__file__).resolve().parents[1] / "csimage_viewer.py"
    return [sys.executable, str(entrypoint), *arguments]


def run_worker_process(
    operation: str,
    source: Path,
    output: Path,
    *,
    resolution: str = "",
    timeout: int = 900,
) -> dict[str, object]:
    result_handle = tempfile.NamedTemporaryFile(
        prefix="csimage-worker-result-",
        suffix=".json",
        delete=False,
    )
    result_file = Path(result_handle.name)
    result_handle.close()
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        completed = subprocess.run(
            worker_command(operation, source, output, resolution, result_file),
            capture_output=True,
            text=True,
            timeout=timeout,
            startupinfo=startupinfo,
            check=False,
        )
        worker_payload: dict[str, object] = {}
        if result_file.is_file():
            try:
                worker_payload = json.loads(result_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                worker_payload = {}
        if completed.returncode != 0:
            message = str(worker_payload.get("error") or "").strip()
            if not message:
                detail = (completed.stderr or completed.stdout).strip().splitlines()
                message = detail[-1] if detail else "本地图片处理进程运行失败。"
            raise RuntimeError(message)
        if worker_payload:
            return worker_payload
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        return json.loads(lines[-1]) if lines else {"output": str(output)}
    finally:
        result_file.unlink(missing_ok=True)


def run_worker_cli(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("operation", choices=("upscale", "remove-background"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", default="2K")
    parser.add_argument("--result-file", type=Path)
    parsed = parser.parse_args(arguments)
    try:
        if parsed.operation == "upscale":
            width, height = upscale_image(parsed.source, parsed.output, parsed.resolution)
            engine = upscayl_status()
        else:
            remove_background(parsed.source, parsed.output)
            with Image.open(parsed.output) as result:
                width, height = result.size
            engine = birefnet_status()
        payload = {
            "output": str(parsed.output.resolve()),
            "width": width,
            "height": height,
            "engine": engine.engine,
            "model": engine.model,
        }
        serialized = json.dumps(payload, ensure_ascii=False)
        if parsed.result_file is not None:
            parsed.result_file.write_text(serialized, encoding="utf-8")
        elif sys.stdout is not None:
            print(serialized)
        return 0
    except Exception as error:
        if parsed.result_file is not None:
            parsed.result_file.write_text(
                json.dumps({"error": str(error)}, ensure_ascii=False),
                encoding="utf-8",
            )
        elif sys.stderr is not None:
            print(str(error), file=sys.stderr)
        return 1
