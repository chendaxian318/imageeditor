"""Run M3 large-image, alpha, animation, mixed-format, and DPI gates."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import gc
import json
import platform
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from PIL import __version__ as pillow_version

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmarks.run_benchmarks import _process_rss_bytes
from light_image_editor.domain.conversion_jobs import ConversionInput, ConversionQueue
from light_image_editor.domain.conversion_runner import ConversionRunner, ConversionTaskStatus
from light_image_editor.domain.recipes import ExportProfile, TransformRecipe, TransformStep

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {
        "large_sizes": ((2400, 1600), (3200, 2000)),
        "queue_rows": 100,
        "dpi_percentages": (100, 200),
        "max_interaction_ms": 1_000.0,
        "max_final_rss_growth_mib": 384.0,
    },
    "standard": {
        "large_sizes": ((6000, 4000), (10000, 5000)),
        "queue_rows": 1_000,
        "dpi_percentages": (100, 125, 150, 200),
        "max_interaction_ms": 1_000.0,
        "max_final_rss_growth_mib": 768.0,
    },
}


@dataclass(frozen=True, slots=True)
class FidelityThresholds:
    minimum_large_pixels: int = 50_000_000
    minimum_queue_rows: int = 1_000
    maximum_dpi_percent: int = 200
    max_interaction_ms: float = 1_000.0
    max_final_rss_growth_mib: float = 768.0


def _create_large_jpeg(path: Path, size: tuple[int, int], seed: int) -> None:
    image = Image.new("RGB", size, (28 + seed * 11, 52, 82))
    draw = ImageDraw.Draw(image)
    width, height = size
    for fraction in range(1, 8):
        left = width * fraction // 9
        top = height * fraction // 10
        draw.rectangle(
            (left, top, min(width - 1, left + width // 7), min(height - 1, top + height // 8)),
            fill=((seed * 37 + fraction * 23) % 256, 80 + fraction * 12, 170),
        )
    draw.line((0, 0, width - 1, height - 1), fill="white", width=max(1, width // 1000))
    image.save(path, "JPEG", quality=88, subsampling=0)
    image.close()


def _queue(paths: list[Path]) -> ConversionQueue:
    queue = ConversionQueue()
    queue.add_files(paths)
    return queue


def _run_recipe(
    paths: list[Path],
    recipe: TransformRecipe,
    output_directory: Path,
    *,
    max_workers: int = 1,
) -> tuple[dict[str, int], tuple[Path, ...], float]:
    started = time.perf_counter()
    result = ConversionRunner(
        _queue(paths),
        recipe,
        output_directory,
        max_workers=max_workers,
    ).run()
    elapsed = time.perf_counter() - started
    counts = {status.value: result.counts[status] for status in ConversionTaskStatus}
    return counts, result.successful_outputs, elapsed


def _decode_summary(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        image.seek(0)
        image.load()
        return {
            "name": path.name,
            "format": image.format,
            "mode": image.mode,
            "size": list(image.size),
            "frame_count": int(getattr(image, "n_frames", 1)),
        }


def _webp_durations(path: Path) -> list[int]:
    """Read ANMF timing because Pillow currently reports zero after WebP seek."""

    payload = path.read_bytes()
    durations: list[int] = []
    offset = 12
    while offset + 8 <= len(payload):
        chunk_type = payload[offset : offset + 4]
        chunk_size = int.from_bytes(payload[offset + 4 : offset + 8], "little")
        start = offset + 8
        if chunk_type == b"ANMF":
            durations.append(int.from_bytes(payload[start + 12 : start + 15], "little"))
        offset = start + chunk_size + (chunk_size % 2)
    return durations


def _run_large_probe(root: Path, sizes: tuple[tuple[int, int], ...]) -> dict[str, Any]:
    inputs = root / "large-inputs"
    outputs = root / "large-outputs"
    inputs.mkdir()
    outputs.mkdir()
    paths: list[Path] = []
    for index, size in enumerate(sizes, start=1):
        path = inputs / f"large-{index}.jpg"
        _create_large_jpeg(path, size, index)
        paths.append(path)
    recipe = TransformRecipe(
        name="大图混合门禁",
        steps=(TransformStep.resize(max_width=2048, max_height=2048),),
        export_profile=ExportProfile(
            output_format="PNG",
            preserve_metadata=False,
            preserve_animation=False,
            rename_template="{stem}-validated",
            conflict_policy="error",
        ),
    )
    counts, converted, elapsed = _run_recipe(paths, recipe, outputs)
    decoded = [_decode_summary(path) for path in converted]
    return {
        "source_sizes": [list(size) for size in sizes],
        "source_pixels": [width * height for width, height in sizes],
        "counts": counts,
        "elapsed_seconds": round(elapsed, 3),
        "outputs": decoded,
    }


def _create_mixed_inputs(directory: Path) -> list[Path]:
    directory.mkdir()
    formats = (
        ("JPEG", "jpg", "RGB"),
        ("PNG", "png", "RGBA"),
        ("WEBP", "webp", "RGB"),
        ("BMP", "bmp", "RGB"),
        ("TIFF", "tiff", "RGB"),
        ("GIF", "gif", "P"),
    )
    paths: list[Path] = []
    for index, (image_format, suffix, mode) in enumerate(formats):
        path = directory / f"mixed-{index}.{suffix}"
        if mode == "RGBA":
            image = Image.new(mode, (320, 224), (40, 100, 190, 128))
        elif mode == "P":
            image = Image.new("RGB", (320, 224), (40, 100, 190)).convert("P")
        else:
            image = Image.new(mode, (320, 224), (40 + index * 15, 100, 190))
        image.save(path, image_format)
        image.close()
        paths.append(path)
    return paths


def _run_mixed_probe(root: Path) -> dict[str, Any]:
    paths = _create_mixed_inputs(root / "mixed-inputs")
    outputs = root / "mixed-outputs"
    outputs.mkdir()
    recipe = TransformRecipe(
        name="混合格式门禁",
        steps=(TransformStep.rotate(90),),
        export_profile=ExportProfile(
            output_format="PNG",
            preserve_metadata=False,
            preserve_animation=False,
            rename_template="{stem}-png",
            conflict_policy="error",
        ),
    )
    counts, converted, elapsed = _run_recipe(paths, recipe, outputs, max_workers=2)
    return {
        "input_suffixes": [path.suffix.casefold() for path in paths],
        "counts": counts,
        "elapsed_seconds": round(elapsed, 3),
        "outputs": [_decode_summary(path) for path in converted],
    }


def _run_transparency_probe(root: Path) -> dict[str, Any]:
    source = root / "alpha.png"
    output = root / "alpha-output"
    output.mkdir()
    image = Image.new("RGBA", (64, 64), (255, 0, 0, 128))
    image.save(source, "PNG")
    image.close()
    recipe = TransformRecipe(
        name="透明底色门禁",
        export_profile=ExportProfile(
            output_format="JPEG",
            quality=95,
            preserve_metadata=False,
            preserve_animation=False,
            transparency_background="#102030",
            rename_template="alpha-flat",
            conflict_policy="error",
        ),
    )
    counts, converted, elapsed = _run_recipe([source], recipe, output)
    pixel: tuple[int, ...] | None = None
    mode: str | None = None
    if converted:
        with Image.open(converted[0]) as result:
            result.load()
            mode = result.mode
            value = result.convert("RGB").getpixel((32, 32))
            pixel = tuple(int(channel) for channel in value)
    return {
        "counts": counts,
        "elapsed_seconds": round(elapsed, 3),
        "mode": mode,
        "center_pixel": list(pixel) if pixel is not None else None,
        "expected_pixel": [136, 16, 24],
    }


def _run_animation_probe(root: Path) -> dict[str, Any]:
    source = root / "animated.gif"
    output = root / "animation-output"
    output.mkdir()
    frames = [
        Image.new("RGBA", (96, 64), color)
        for color in ((220, 40, 50, 255), (40, 180, 80, 255), (50, 90, 220, 255))
    ]
    durations = [80, 120, 160]
    frames[0].save(
        source,
        "GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=2,
        disposal=2,
    )
    for frame in frames:
        frame.close()
    recipe = TransformRecipe(
        name="动画保留门禁",
        steps=(TransformStep.flip("horizontal"),),
        export_profile=ExportProfile(
            output_format="WEBP",
            quality=90,
            preserve_metadata=False,
            preserve_animation=True,
            rename_template="animated-preserved",
            conflict_policy="error",
        ),
    )
    counts, converted, elapsed = _run_recipe([source], recipe, output)
    frame_count = 0
    actual_durations: list[int] = []
    loop: int | None = None
    if converted:
        with Image.open(converted[0]) as result:
            frame_count = int(getattr(result, "n_frames", 1))
            loop_value = result.info.get("loop")
            loop = int(loop_value) if isinstance(loop_value, int) else None
            for index in range(frame_count):
                result.seek(index)
                actual_durations.append(int(result.info.get("duration", 0)))
        if actual_durations and not any(actual_durations):
            actual_durations = _webp_durations(converted[0])
    return {
        "counts": counts,
        "elapsed_seconds": round(elapsed, 3),
        "frame_count": frame_count,
        "durations_ms": actual_durations,
        "expected_durations_ms": durations,
        "loop": loop,
        "expected_loop": 2,
    }


def _run_dpi_probe(
    root: Path,
    *,
    queue_rows: int,
    dpi_percentages: tuple[int, ...],
) -> list[dict[str, Any]]:
    import tkinter as tk

    from light_image_editor.ui.conversion_workspace import ConversionWorkspace

    paths = [root / "virtual-queue" / f"row-{index:04d}.png" for index in range(queue_rows)]
    samples: list[dict[str, Any]] = []
    for dpi_percent in dpi_percentages:
        queue = ConversionQueue.from_ordered(ConversionInput(path) for path in paths)
        root_window = tk.Tk()
        root_window.withdraw()
        try:
            root_window.tk.call("tk", "scaling", (96.0 * dpi_percent / 100.0) / 72.0)
            workspace = ConversionWorkspace(root_window, queue=queue, width=286)
            workspace.pack(fill="both", expand=True)
            root_window.update_idletasks()
            started = time.perf_counter()
            selected = workspace.select(paths[-1], reveal=True)
            toggled = workspace.toggle_selected()
            workspace.select(paths[0], reveal=True)
            root_window.update_idletasks()
            interaction_ms = (time.perf_counter() - started) * 1000.0
            samples.append(
                {
                    "dpi_percent": dpi_percent,
                    "queue_rows": len(workspace.tree.get_children()),
                    "selected_last": selected,
                    "toggled_last": toggled is not None and not toggled.enabled,
                    "interaction_ms": round(interaction_ms, 3),
                    "requested_width": workspace.winfo_reqwidth(),
                    "requested_height": workspace.winfo_reqheight(),
                }
            )
            workspace.destroy()
        finally:
            root_window.destroy()
    return samples


def evaluate_fidelity_gate(
    result: dict[str, Any],
    thresholds: FidelityThresholds | None = None,
) -> list[str]:
    limits = thresholds or FidelityThresholds()
    violations: list[str] = []
    large = result["large_images"]
    if max(int(value) for value in large["source_pixels"]) < limits.minimum_large_pixels:
        violations.append("大图样本未覆盖 50 MP")
    expected_large = len(large["source_sizes"])
    if int(large["counts"]["succeeded"]) != expected_large:
        violations.append(f"大图成功 {large['counts']['succeeded']} / {expected_large}")
    if len(large["outputs"]) != expected_large:
        violations.append("大图输出未全部重新解码")
    for output in large["outputs"]:
        if max(int(value) for value in output["size"]) > 2048:
            violations.append(f"大图尺寸配方未生效：{output['name']}")

    mixed = result["mixed_formats"]
    expected_mixed = len(mixed["input_suffixes"])
    if int(mixed["counts"]["succeeded"]) != expected_mixed:
        violations.append(f"混合格式成功 {mixed['counts']['succeeded']} / {expected_mixed}")
    if any(output["format"] != "PNG" for output in mixed["outputs"]):
        violations.append("混合格式输出存在非 PNG 文件")

    transparency = result["transparency"]
    if int(transparency["counts"]["succeeded"]) != 1 or transparency["mode"] != "RGB":
        violations.append("透明图转 JPEG 未成功生成 RGB 输出")
    pixel = transparency["center_pixel"]
    expected = transparency["expected_pixel"]
    if pixel is None or any(abs(int(a) - int(b)) > 12 for a, b in zip(pixel, expected)):
        violations.append(f"透明背景合成像素偏差过大：{pixel} / {expected}")

    animation = result["animation"]
    if int(animation["counts"]["succeeded"]) != 1:
        violations.append("动画保留转换失败")
    if int(animation["frame_count"]) != len(animation["expected_durations_ms"]):
        violations.append("动画帧数未保留")
    if animation["durations_ms"] != animation["expected_durations_ms"]:
        violations.append("动画帧时长未保留")
    if animation["loop"] != animation["expected_loop"]:
        violations.append("动画循环次数未保留")

    dpi_samples = result["dpi_queue"]
    if max(int(sample["dpi_percent"]) for sample in dpi_samples) < limits.maximum_dpi_percent:
        violations.append("DPI 样本未覆盖 200%")
    if max(int(sample["queue_rows"]) for sample in dpi_samples) < limits.minimum_queue_rows:
        violations.append("DPI 队列样本未覆盖 1,000 行")
    for sample in dpi_samples:
        if int(sample["queue_rows"]) != int(result["fixtures"]["queue_rows"]):
            violations.append(f"{sample['dpi_percent']}% DPI 队列行数丢失")
        if not bool(sample["selected_last"]) or not bool(sample["toggled_last"]):
            violations.append(f"{sample['dpi_percent']}% DPI 队列交互失败")
        if float(sample["interaction_ms"]) > limits.max_interaction_ms:
            violations.append(
                f"{sample['dpi_percent']}% DPI 队列交互 {sample['interaction_ms']} ms 超限"
            )

    growth = float(result["memory"]["final_growth_mib"])
    if growth > limits.max_final_rss_growth_mib:
        violations.append(
            f"门禁最终内存增长 {growth:.1f} MiB > {limits.max_final_rss_growth_mib:.1f} MiB"
        )
    return violations


def run(profile_name: str) -> dict[str, Any]:
    profile = PROFILES[profile_name]
    gc.collect()
    baseline = _process_rss_bytes()
    with tempfile.TemporaryDirectory(prefix="light-image-editor-m3-fidelity-") as raw_temp:
        root = Path(raw_temp)
        result: dict[str, Any] = {
            "schema_version": 1,
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "profile": profile_name,
            "environment": {
                "os": platform.platform(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "pillow": pillow_version,
            },
            "fixtures": {
                "large_sizes": [list(value) for value in profile["large_sizes"]],
                "queue_rows": profile["queue_rows"],
                "dpi_percentages": list(profile["dpi_percentages"]),
            },
            "large_images": _run_large_probe(root, tuple(profile["large_sizes"])),
            "mixed_formats": _run_mixed_probe(root),
            "transparency": _run_transparency_probe(root),
            "animation": _run_animation_probe(root),
            "dpi_queue": _run_dpi_probe(
                root,
                queue_rows=int(profile["queue_rows"]),
                dpi_percentages=tuple(profile["dpi_percentages"]),
            ),
        }
    gc.collect()
    final = _process_rss_bytes()
    result["memory"] = {
        "baseline_rss_mib": round(baseline / 1024 / 1024, 3),
        "final_rss_mib": round(final / 1024 / 1024, 3),
        "final_growth_mib": round(max(0, final - baseline) / 1024 / 1024, 3),
    }
    thresholds = FidelityThresholds(
        minimum_large_pixels=(
            50_000_000
            if profile_name == "standard"
            else max(w * h for w, h in profile["large_sizes"])
        ),
        minimum_queue_rows=int(profile["queue_rows"]),
        maximum_dpi_percent=max(profile["dpi_percentages"]),
        max_interaction_ms=float(profile["max_interaction_ms"]),
        max_final_rss_growth_mib=float(profile["max_final_rss_growth_mib"]),
    )
    result["thresholds"] = asdict(thresholds)
    result["violations"] = evaluate_fidelity_gate(result, thresholds)
    result["passed"] = not result["violations"]
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="standard")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-fail", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    result = run(arguments.profile)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if arguments.output is not None:
        destination = arguments.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized + "\n", encoding="utf-8")
    return 0 if result["passed"] or arguments.no_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
