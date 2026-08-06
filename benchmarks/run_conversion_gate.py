"""Run the M3 conversion performance and reliability acceptance gate."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import gc
import json
import platform
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image, ImageDraw
from PIL import __version__ as pillow_version

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmarks.run_benchmarks import _process_rss_bytes
from image_ops import open_image
from light_image_editor.domain.conversion_jobs import ConversionQueue
from light_image_editor.domain.conversion_runner import (
    ConversionRunner,
    ConversionRunResult,
    ConversionTaskStatus,
)
from light_image_editor.domain.recipe_executor import execute_transform_recipe
from light_image_editor.domain.recipes import ExportProfile, TransformRecipe, TransformStep
from light_image_editor.version import __version__

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {
        "valid_count": 32,
        "corrupt_count": 2,
        "cancel_count": 24,
        "image_size": (64, 48),
        "max_workers": 2,
        "max_final_rss_growth_mib": 96.0,
        "max_peak_rss_growth_mib": 160.0,
    },
    "standard": {
        "valid_count": 1_000,
        "corrupt_count": 4,
        "cancel_count": 128,
        "image_size": (96, 64),
        "max_workers": 4,
        "max_final_rss_growth_mib": 192.0,
        "max_peak_rss_growth_mib": 384.0,
    },
}


@dataclass(frozen=True, slots=True)
class ConversionGateThresholds:
    minimum_valid_inputs: int = 1_000
    max_cancel_latency_ms: float = 1_000.0
    max_final_rss_growth_mib: float = 192.0
    max_peak_rss_growth_mib: float = 384.0


def _status_counts(result: ConversionRunResult) -> dict[str, int]:
    return {status.value: result.counts[status] for status in ConversionTaskStatus}


def _create_fixture(path: Path, size: tuple[int, int], seed: int) -> None:
    image = Image.new("RGB", size, (seed * 17 % 256, 40, 90))
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.rectangle(
        (seed % max(1, width // 2), seed % max(1, height // 2), width - 1, height - 1),
        fill=(seed * 31 % 256, seed * 47 % 256, seed * 59 % 256),
    )
    draw.line((0, 0, width - 1, height - 1), fill="white", width=1)
    image.save(path, "PNG")
    image.close()


def _recipe() -> TransformRecipe:
    return TransformRecipe(
        name="M3 转换可靠性门禁",
        steps=(TransformStep.rotate(90), TransformStep.resize(max_width=96, max_height=96)),
        export_profile=ExportProfile(
            output_format="PNG",
            preserve_metadata=False,
            rename_template="{stem}_{index:04d}",
            conflict_policy="error",
        ),
    )


def _run_monitored(runner: ConversionRunner) -> tuple[ConversionRunResult, dict[str, float]]:
    results: list[ConversionRunResult] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            results.append(runner.run())
        except BaseException as error:
            errors.append(error)

    gc.collect()
    baseline = _process_rss_bytes()
    peak = baseline
    started = time.perf_counter()
    thread = threading.Thread(target=target, name="conversion-gate-normal")
    thread.start()
    while thread.is_alive():
        thread.join(0.01)
        peak = max(peak, _process_rss_bytes())
    elapsed_seconds = time.perf_counter() - started
    if errors:
        raise RuntimeError(f"转换门禁工作线程失败：{errors[0]}") from errors[0]
    if not results:
        raise RuntimeError("转换门禁工作线程没有返回结果")
    gc.collect()
    final = _process_rss_bytes()
    peak = max(peak, final)
    return results[0], {
        "elapsed_seconds": round(elapsed_seconds, 3),
        "baseline_rss_mib": round(baseline / 1024 / 1024, 3),
        "peak_rss_mib": round(peak / 1024 / 1024, 3),
        "peak_growth_mib": round(max(0, peak - baseline) / 1024 / 1024, 3),
        "final_growth_mib": round(max(0, final - baseline) / 1024 / 1024, 3),
    }


def _verify_outputs(outputs: tuple[Path, ...]) -> tuple[int, list[str]]:
    verified = 0
    failures: list[str] = []
    for path in outputs:
        try:
            image = open_image(path)
            try:
                if image.width < 1 or image.height < 1:
                    raise ValueError("输出尺寸无效")
            finally:
                image.close()
        except Exception as error:
            failures.append(f"{path.name}: {type(error).__name__}: {error}")
        else:
            verified += 1
    return verified, failures


def _run_cancellation_probe(
    paths: list[Path],
    *,
    output_directory: Path,
    max_workers: int,
) -> dict[str, Any]:
    queue = ConversionQueue()
    queue.add_files(paths)
    runner = ConversionRunner(queue, _recipe(), output_directory, max_workers=max_workers)
    workers_ready = threading.Event()
    release_workers = threading.Event()
    cancel_issued = threading.Event()
    counter_lock = threading.Lock()
    started_total = 0
    started_after_cancel = 0
    expected_initial_workers = min(max_workers, len(paths))
    real_execute = execute_transform_recipe

    def blocked_execute(image: Image.Image, recipe: TransformRecipe) -> Image.Image:
        nonlocal started_total, started_after_cancel
        with counter_lock:
            started_total += 1
            if cancel_issued.is_set():
                started_after_cancel += 1
            if started_total >= expected_initial_workers:
                workers_ready.set()
        if not release_workers.wait(10):
            raise TimeoutError("取消门禁工作线程未被释放")
        return real_execute(image, recipe)

    results: list[ConversionRunResult] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(runner.run())
        except BaseException as error:
            errors.append(error)

    with patch(
        "light_image_editor.domain.conversion_runner.execute_transform_recipe",
        side_effect=blocked_execute,
    ):
        thread = threading.Thread(target=run, name="conversion-gate-cancel")
        thread.start()
        became_ready = workers_ready.wait(10)
        cancel_started = time.perf_counter()
        runner.cancel()
        cancel_issued.set()
        release_workers.set()
        thread.join(10)
        cancel_latency_ms = (time.perf_counter() - cancel_started) * 1000.0

    if thread.is_alive():
        raise RuntimeError("取消门禁超过 10 秒仍未结束")
    if errors:
        raise RuntimeError(f"取消门禁工作线程失败：{errors[0]}") from errors[0]
    if not results:
        raise RuntimeError("取消门禁工作线程没有返回结果")
    result = results[0]
    return {
        "requested_items": len(paths),
        "workers_became_ready": became_ready,
        "started_total": started_total,
        "started_after_cancel": started_after_cancel,
        "cancel_latency_ms": round(cancel_latency_ms, 3),
        "peak_in_flight": result.peak_in_flight,
        "counts": _status_counts(result),
        "outputs_written": len(result.successful_outputs),
    }


def evaluate_conversion_gate(
    result: dict[str, Any],
    thresholds: ConversionGateThresholds | None = None,
) -> list[str]:
    """Return every unmet M3 acceptance condition."""

    limits = thresholds or ConversionGateThresholds()
    fixtures = result["fixtures"]
    normal = result["normal_run"]
    cancellation = result["cancellation"]
    memory = result["memory"]
    valid_count = int(fixtures["valid_count"])
    corrupt_count = int(fixtures["corrupt_count"])
    max_workers = int(fixtures["max_workers"])
    counts = normal["counts"]
    violations: list[str] = []

    if valid_count < limits.minimum_valid_inputs:
        violations.append(
            f"有效输入只有 {valid_count} 项，要求至少 {limits.minimum_valid_inputs} 项"
        )
    if int(normal["peak_in_flight"]) > max_workers:
        violations.append(
            f"正常转换峰值在途 {normal['peak_in_flight']} > max_workers {max_workers}"
        )
    if int(counts[ConversionTaskStatus.SUCCEEDED.value]) != valid_count:
        violations.append(
            f"有效输入成功 {counts[ConversionTaskStatus.SUCCEEDED.value]} / {valid_count}"
        )
    if int(counts[ConversionTaskStatus.FAILED.value]) != corrupt_count:
        violations.append(
            f"注入失败隔离 {counts[ConversionTaskStatus.FAILED.value]} / {corrupt_count}"
        )
    if int(counts[ConversionTaskStatus.CANCELLED.value]) != 0:
        violations.append("正常转换出现了意外取消项")
    if int(normal["verified_outputs"]) != int(counts[ConversionTaskStatus.SUCCEEDED.value]):
        violations.append(
            "成功输出未全部通过独立重解码："
            f"{normal['verified_outputs']} / {counts[ConversionTaskStatus.SUCCEEDED.value]}"
        )
    if normal["output_decode_failures"]:
        violations.append("存在无法重新解码的成功输出")
    if not bool(cancellation["workers_became_ready"]):
        violations.append("取消探针未能启动首批工作线程")
    if int(cancellation["started_after_cancel"]) != 0:
        violations.append(f"取消后仍有 {cancellation['started_after_cancel']} 项开始像素处理")
    if int(cancellation["peak_in_flight"]) > max_workers:
        violations.append(
            f"取消场景峰值在途 {cancellation['peak_in_flight']} > max_workers {max_workers}"
        )
    if float(cancellation["cancel_latency_ms"]) > limits.max_cancel_latency_ms:
        violations.append(
            f"取消收敛 {float(cancellation['cancel_latency_ms']):.1f} ms > "
            f"{limits.max_cancel_latency_ms:.1f} ms"
        )
    if int(cancellation["outputs_written"]) != 0:
        violations.append("取消信号后仍写出了文件")
    if int(cancellation["counts"][ConversionTaskStatus.CANCELLED.value]) != int(
        cancellation["requested_items"]
    ):
        violations.append("取消场景并非所有任务都收敛为 cancelled")
    if float(memory["final_growth_mib"]) > limits.max_final_rss_growth_mib:
        violations.append(
            f"最终 RSS 增长 {float(memory['final_growth_mib']):.1f} MiB > "
            f"{limits.max_final_rss_growth_mib:.1f} MiB"
        )
    if float(memory["peak_growth_mib"]) > limits.max_peak_rss_growth_mib:
        violations.append(
            f"峰值 RSS 增长 {float(memory['peak_growth_mib']):.1f} MiB > "
            f"{limits.max_peak_rss_growth_mib:.1f} MiB"
        )
    return violations


def run(
    profile_name: str = "smoke",
    *,
    valid_count: int | None = None,
    corrupt_count: int | None = None,
    cancel_count: int | None = None,
    max_workers: int | None = None,
    image_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    if profile_name not in PROFILES:
        raise ValueError(f"未知转换门禁 profile：{profile_name}")
    profile = dict(PROFILES[profile_name])
    overrides = {
        "valid_count": valid_count,
        "corrupt_count": corrupt_count,
        "cancel_count": cancel_count,
        "max_workers": max_workers,
        "image_size": image_size,
    }
    for key, value in overrides.items():
        if value is not None:
            profile[key] = value
    for key in ("valid_count", "corrupt_count", "cancel_count", "max_workers"):
        value = profile[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} 必须是正整数")
    size = tuple(profile["image_size"])
    if len(size) != 2 or any(isinstance(value, bool) or int(value) < 2 for value in size):
        raise ValueError("image_size 必须包含两个不小于 2 的整数")
    normalized_size = (int(size[0]), int(size[1]))
    profile["image_size"] = normalized_size

    with tempfile.TemporaryDirectory(prefix="light-image-editor-conversion-gate-") as raw:
        root = Path(raw)
        inputs = root / "inputs"
        cancel_inputs = root / "cancel-inputs"
        outputs = root / "outputs"
        cancel_outputs = root / "cancel-outputs"
        inputs.mkdir()
        cancel_inputs.mkdir()
        valid_paths: list[Path] = []
        for index in range(int(profile["valid_count"])):
            path = inputs / f"valid-{index:04d}.png"
            _create_fixture(path, normalized_size, index)
            valid_paths.append(path.resolve())
        corrupt_paths: list[Path] = []
        for index in range(int(profile["corrupt_count"])):
            path = inputs / f"corrupt-{index:04d}.png"
            path.write_bytes(b"not a valid image")
            corrupt_paths.append(path.resolve())
        cancel_paths: list[Path] = []
        for index in range(int(profile["cancel_count"])):
            path = cancel_inputs / f"cancel-{index:04d}.png"
            _create_fixture(path, normalized_size, index + 10_000)
            cancel_paths.append(path.resolve())

        queue = ConversionQueue()
        queue.add_files((*valid_paths, *corrupt_paths))
        runner = ConversionRunner(
            queue,
            _recipe(),
            outputs,
            max_workers=int(profile["max_workers"]),
        )
        result, memory = _run_monitored(runner)
        verified_outputs, decode_failures = _verify_outputs(result.successful_outputs)
        elapsed_seconds = float(memory["elapsed_seconds"])
        normal_run = {
            "requested_items": len(queue),
            "counts": _status_counts(result),
            "peak_in_flight": result.peak_in_flight,
            "elapsed_seconds": elapsed_seconds,
            "throughput_items_per_second": round(len(queue) / max(elapsed_seconds, 0.001), 3),
            "verified_outputs": verified_outputs,
            "output_decode_failures": decode_failures,
        }
        cancellation = _run_cancellation_probe(
            cancel_paths,
            output_directory=cancel_outputs,
            max_workers=int(profile["max_workers"]),
        )

    thresholds = ConversionGateThresholds(
        minimum_valid_inputs=int(profile["valid_count"]),
        max_final_rss_growth_mib=float(profile["max_final_rss_growth_mib"]),
        max_peak_rss_growth_mib=float(profile["max_peak_rss_growth_mib"]),
    )
    gate_result: dict[str, Any] = {
        "schema_version": 1,
        "application_version": __version__,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "profile": profile_name,
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "pillow": pillow_version,
        },
        "fixtures": {
            "valid_count": int(profile["valid_count"]),
            "corrupt_count": int(profile["corrupt_count"]),
            "cancel_count": int(profile["cancel_count"]),
            "image_size": list(normalized_size),
            "max_workers": int(profile["max_workers"]),
        },
        "normal_run": normal_run,
        "cancellation": cancellation,
        "memory": memory,
        "thresholds": asdict(thresholds),
    }
    gate_result["violations"] = evaluate_conversion_gate(gate_result, thresholds)
    gate_result["passed"] = not gate_result["violations"]
    return gate_result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="smoke")
    parser.add_argument("--count", type=int, help="覆盖有效静态图片数量")
    parser.add_argument("--corrupt-count", type=int, help="覆盖损坏图片数量")
    parser.add_argument("--cancel-count", type=int, help="覆盖取消场景图片数量")
    parser.add_argument("--max-workers", type=int, help="覆盖最大工作线程数")
    parser.add_argument("--width", type=int, help="覆盖测试图片宽度")
    parser.add_argument("--height", type=int, help="覆盖测试图片高度")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-fail", action="store_true", help="记录失败但返回成功退出码")
    arguments = parser.parse_args()
    if (arguments.width is None) != (arguments.height is None):
        parser.error("--width 与 --height 必须同时提供")
    return arguments


def main() -> int:
    arguments = _parse_args()
    size = None if arguments.width is None else (int(arguments.width), int(arguments.height))
    result = run(
        arguments.profile,
        valid_count=arguments.count,
        corrupt_count=arguments.corrupt_count,
        cancel_count=arguments.cancel_count,
        max_workers=arguments.max_workers,
        image_size=size,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if arguments.output is not None:
        destination = arguments.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized + "\n", encoding="utf-8")
    return 0 if result["passed"] or arguments.no_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
