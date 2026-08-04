"""Repeatable performance smoke and baseline benchmarks for Light Image Editor.

The public command creates deterministic temporary fixtures, exercises the real
Tk application for UI-sensitive scenarios, exercises the production image
pipeline for batch processing, prints one JSON result, and removes all fixtures.
No repository file is modified unless ``--output`` is supplied explicitly.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {
        "large_size": (1600, 1200),
        "switch_size": (960, 640),
        "switch_count": 4,
        "batch_size": (800, 600),
        "batch_count": 3,
        "repetitions": 1,
    },
    "standard": {
        "large_size": (8000, 6000),
        "switch_size": (2560, 1440),
        "switch_count": 20,
        "batch_size": (2400, 1600),
        "batch_count": 20,
        "repetitions": 3,
    },
}


def _metric(samples: list[float], unit: str = "ms") -> dict[str, Any]:
    if not samples:
        raise ValueError("a benchmark metric requires at least one sample")
    rounded = [round(value, 3) for value in samples]
    return {
        "unit": unit,
        "count": len(rounded),
        "median": round(statistics.median(samples), 3),
        "mean": round(statistics.fmean(samples), 3),
        "minimum": round(min(samples), 3),
        "maximum": round(max(samples), 3),
        "samples": rounded,
    }


def _create_pattern_image(path: Path, size: tuple[int, int], seed: int) -> None:
    """Create a deterministic, non-trivial JPEG fixture without numpy."""

    from PIL import Image, ImageDraw

    width, height = size
    image = Image.new("RGB", size, (20, 30, 40))
    draw = ImageDraw.Draw(image)
    tile = max(32, min(width, height) // 16)
    for top in range(0, height, tile):
        for left in range(0, width, tile):
            column = left // tile
            row = top // tile
            color = (
                (column * 37 + row * 13 + seed * 17) % 256,
                (column * 11 + row * 43 + seed * 29) % 256,
                (column * 23 + row * 19 + seed * 31) % 256,
            )
            draw.rectangle(
                (left, top, min(width - 1, left + tile), min(height - 1, top + tile)),
                fill=color,
            )
    draw.line((0, 0, width - 1, height - 1), fill="white", width=max(1, tile // 16))
    draw.line((0, height - 1, width - 1, 0), fill="black", width=max(1, tile // 16))
    image.save(path, "JPEG", quality=90, subsampling=0)
    image.close()


def _worker_command(mode: str, *arguments: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), "--_worker", mode, *arguments]


def _read_worker(
    command: list[str], *, measure_ready: bool = False
) -> tuple[dict[str, Any], float]:
    """Read the worker's first JSON line; optionally time launch-to-ready."""

    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert process.stdout is not None
    line = process.stdout.readline()
    ready_ms = (time.perf_counter() - started) * 1000.0
    remaining_stdout, stderr = process.communicate(timeout=120)
    if process.returncode != 0:
        raise RuntimeError(
            f"benchmark worker exited with {process.returncode}:\n"
            f"{stderr.strip()}\n{line}{remaining_stdout}"
        )
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"benchmark worker did not emit JSON: {line!r}\n{remaining_stdout}\n{stderr}"
        ) from exc
    return payload, ready_ms if measure_ready else 0.0


def _benchmark_startup(repetitions: int) -> dict[str, Any]:
    samples: list[float] = []
    internal_samples: list[float] = []
    for _ in range(repetitions):
        payload, ready_ms = _read_worker(_worker_command("startup"), measure_ready=True)
        samples.append(ready_ms)
        internal_samples.append(float(payload["application_create_ms"]))
    return {
        "definition": "new Python process to Tk window ready (warm-cache after first run)",
        "launch_to_ready": _metric(samples),
        "worker_application_create": _metric(internal_samples),
    }


def _benchmark_large_open(image_path: Path, repetitions: int) -> dict[str, Any]:
    samples: list[float] = []
    for _ in range(repetitions):
        payload, _ = _read_worker(_worker_command("open", str(image_path)))
        samples.append(float(payload["open_and_render_ms"]))
    return {
        "definition": (
            "ImageEditorApp.open_path until the asynchronously rendered preview is visible"
        ),
        "open_and_render": _metric(samples),
        "fixture_bytes": image_path.stat().st_size,
    }


def _benchmark_switching(image_paths: list[Path], repetitions: int) -> dict[str, Any]:
    samples: list[float] = []
    round_totals: list[float] = []
    peak_rss: list[float] = []
    final_growth: list[float] = []
    for _ in range(repetitions):
        payload, _ = _read_worker(_worker_command("switch", *(str(path) for path in image_paths)))
        current = [float(value) for value in payload["switch_samples_ms"]]
        samples.extend(current)
        round_totals.append(sum(current))
        peak_rss.append(float(payload["peak_rss_bytes"]) / (1024 * 1024))
        final_growth.append(float(payload["final_growth_bytes"]) / (1024 * 1024))
    return {
        "definition": "open first folder image, then switch through remaining files with open_path",
        "per_image": _metric(samples),
        "round_total": _metric(round_totals),
        "images_per_round": len(image_paths) - 1,
        "peak_rss": _metric(peak_rss, "MiB"),
        "final_rss_growth": _metric(final_growth, "MiB"),
    }


def _benchmark_zoom(image_path: Path, repetitions: int) -> dict[str, Any]:
    samples: list[float] = []
    for _ in range(repetitions):
        payload, _ = _read_worker(_worker_command("zoom", str(image_path)))
        samples.extend(float(value) for value in payload["zoom_samples_ms"])
    return {
        "definition": "render the same decoded image at 50%, 100%, 150%, 200% and fit",
        "per_change": _metric(samples),
    }


def _benchmark_export(image_path: Path, output_root: Path, repetitions: int) -> dict[str, Any]:
    from image_ops import ExportOptions, open_image, save_image

    samples: list[float] = []
    sizes: list[int] = []
    options = ExportOptions(output_format="JPEG", quality=90)
    source = open_image(image_path)
    try:
        for repetition in range(repetitions):
            target = output_root / f"export-{repetition + 1}.jpg"
            started = time.perf_counter()
            save_image(source, target, options)
            samples.append((time.perf_counter() - started) * 1000.0)
            sizes.append(target.stat().st_size)
    finally:
        source.close()
    return {
        "definition": "full-resolution JPEG export at quality 90",
        "save": _metric(samples),
        "output_bytes": sizes,
    }


def _benchmark_batch(
    image_paths: list[Path], output_root: Path, repetitions: int
) -> dict[str, Any]:
    from image_ops import ExportOptions, open_image, save_image

    elapsed_samples: list[float] = []
    throughput_samples: list[float] = []
    output_bytes: list[int] = []
    options = ExportOptions(output_format="JPEG", quality=85, max_width=1920)
    for repetition in range(repetitions):
        output_dir = output_root / f"round-{repetition + 1}"
        output_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        written = 0
        for index, source in enumerate(image_paths):
            image = open_image(source)
            try:
                target = output_dir / f"image-{index:03d}.jpg"
                save_image(image, target, options)
                written += target.stat().st_size
            finally:
                image.close()
        elapsed = time.perf_counter() - started
        elapsed_samples.append(elapsed * 1000.0)
        throughput_samples.append(len(image_paths) / elapsed)
        output_bytes.append(written)
    return {
        "definition": "production open_image/save_image pipeline, JPEG quality 85, max side 1920",
        "round_total": _metric(elapsed_samples),
        "throughput": _metric(throughput_samples, "images/s"),
        "images_per_round": len(image_paths),
        "output_bytes_per_round": output_bytes,
    }


def _git_metadata() -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return completed.stdout.strip()

    try:
        return {
            "commit": run("rev-parse", "HEAD"),
            "branch": run("branch", "--show-current"),
            "dirty": bool(run("status", "--porcelain")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "branch": None, "dirty": None}


def run(profile_name: str) -> dict[str, Any]:
    from PIL import __version__ as pillow_version

    try:
        from light_image_editor.version import __version__ as app_version
    except ImportError:
        app_version = "unversioned"

    profile = PROFILES[profile_name]
    repetitions = int(profile["repetitions"])
    with tempfile.TemporaryDirectory(prefix="light-image-editor-benchmark-") as raw_temp:
        temporary_root = Path(raw_temp)
        large_dir = temporary_root / "large"
        switch_dir = temporary_root / "switch"
        batch_dir = temporary_root / "batch"
        output_dir = temporary_root / "output"
        for directory in (large_dir, switch_dir, batch_dir, output_dir):
            directory.mkdir()

        large_image = large_dir / "large.jpg"
        _create_pattern_image(large_image, tuple(profile["large_size"]), seed=1)

        switch_images: list[Path] = []
        for index in range(int(profile["switch_count"])):
            path = switch_dir / f"image-{index:03d}.jpg"
            _create_pattern_image(path, tuple(profile["switch_size"]), seed=index + 10)
            switch_images.append(path)

        batch_images: list[Path] = []
        for index in range(int(profile["batch_count"])):
            path = batch_dir / f"image-{index:03d}.jpg"
            _create_pattern_image(path, tuple(profile["batch_size"]), seed=index + 100)
            batch_images.append(path)

        benchmarks = {
            "startup": _benchmark_startup(repetitions),
            "open_large_image": _benchmark_large_open(large_image, repetitions),
            "switch_images": _benchmark_switching(switch_images, repetitions),
            "zoom": _benchmark_zoom(switch_images[0], repetitions),
            "export": _benchmark_export(large_image, output_dir, repetitions),
            "batch_process": _benchmark_batch(batch_images, output_dir, repetitions),
        }

    return {
        "schema_version": 1,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "profile": profile_name,
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "python_executable": sys.executable,
            "pillow": pillow_version,
            "application": app_version,
            "git": _git_metadata(),
        },
        "fixtures": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in profile.items()
        },
        "benchmarks": benchmarks,
        "interpretation": {
            "lower_is_better": [
                "startup.launch_to_ready",
                "open_large_image.open_and_render",
                "switch_images.per_image",
                "zoom.per_change",
                "export.save",
                "batch_process.round_total",
            ],
            "higher_is_better": ["batch_process.throughput"],
            "note": (
                "Compare results only on equivalent hardware, power mode, profile and build type."
            ),
        },
    }


def _create_ui_application() -> Any:
    from light_image_editor.app import create_application

    app: Any = create_application([])
    app.update_idletasks()
    return app


def _wait_for_preview(
    app: Any,
    previous_generation: int,
    *,
    started: float | None = None,
    timeout: float = 30.0,
) -> float:
    """Pump Tk until async decode and preview rendering reach the canvas."""

    started = started or time.perf_counter()
    deadline = started + timeout
    while time.perf_counter() < deadline:
        app.update()
        if app.original is not None and app.preview_ready_generation > previous_generation:
            return (time.perf_counter() - started) * 1000.0
        time.sleep(0.002)
    raise TimeoutError("preview did not become ready before the benchmark timeout")


def _destroy_ui_application(app: Any) -> None:
    app.image_loader.shutdown()
    app.preview_renderer.shutdown()
    app._drop_target.disable()
    app.destroy()


def _worker_startup() -> tuple[dict[str, Any], Any]:
    started = time.perf_counter()
    app = _create_ui_application()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    app.withdraw()
    return {"application_create_ms": elapsed_ms}, app


def _worker_open(path: Path) -> dict[str, Any]:
    app = _create_ui_application()
    app.withdraw()
    try:
        previous_generation = app.preview_ready_generation
        started = time.perf_counter()
        app.open_path(path, confirm=False)
        elapsed_ms = _wait_for_preview(app, previous_generation, started=started)
        if app.original is None:
            raise RuntimeError(f"application did not load fixture: {path}")
        return {"open_and_render_ms": elapsed_ms}
    finally:
        _destroy_ui_application(app)


def _worker_switch(paths: list[Path]) -> dict[str, Any]:
    if len(paths) < 2:
        raise ValueError("switch benchmark needs at least two images")
    app = _create_ui_application()
    app.withdraw()
    samples: list[float] = []
    initial_rss = _process_rss_bytes()
    peak_rss = initial_rss
    try:
        previous_generation = app.preview_ready_generation
        app.open_path(paths[0], confirm=False)
        _wait_for_preview(app, previous_generation)
        for path in paths[1:]:
            previous_generation = app.preview_ready_generation
            started = time.perf_counter()
            app.open_path(path, confirm=False)
            samples.append(_wait_for_preview(app, previous_generation, started=started))
            peak_rss = max(peak_rss, _process_rss_bytes())
        return {
            "switch_samples_ms": samples,
            "peak_rss_bytes": peak_rss,
            "final_growth_bytes": max(0, _process_rss_bytes() - initial_rss),
        }
    finally:
        _destroy_ui_application(app)


def _worker_zoom(path: Path) -> dict[str, Any]:
    app = _create_ui_application()
    app.withdraw()
    samples: list[float] = []
    try:
        generation = app.preview_ready_generation
        app.open_path(path, confirm=False)
        _wait_for_preview(app, generation)
        for mode in ("50%", "100%", "150%", "200%", "适应"):
            generation = app.preview_ready_generation
            app.zoom_mode.set(mode)
            started = time.perf_counter()
            app._refresh_preview()
            samples.append(_wait_for_preview(app, generation, started=started))
        return {"zoom_samples_ms": samples}
    finally:
        _destroy_ui_application(app)


def _process_rss_bytes() -> int:
    if os.name != "nt":
        return 0

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        return 0
    return int(counters.WorkingSetSize)


def _run_worker(arguments: list[str]) -> int:
    mode = arguments[0]
    cleanup_app: Any | None = None
    if mode == "startup":
        payload, cleanup_app = _worker_startup()
    elif mode == "open":
        payload = _worker_open(Path(arguments[1]))
    elif mode == "switch":
        payload = _worker_switch([Path(value) for value in arguments[1:]])
    elif mode == "zoom":
        payload = _worker_zoom(Path(arguments[1]))
    else:
        raise ValueError(f"unknown worker mode: {mode}")
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    if cleanup_app is not None:
        cleanup_app.destroy()
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="standard",
        help="smoke is CI-friendly; standard creates a 48 MP large-image fixture",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON destination; stdout is always emitted",
    )
    parser.add_argument("--_worker", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(argv)
    if arguments._worker:
        return _run_worker(arguments._worker)
    result = run(arguments.profile)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if arguments.output is not None:
        destination = arguments.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
