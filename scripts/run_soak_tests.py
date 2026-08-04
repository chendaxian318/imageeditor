"""Long-running browse, batch, memory and failure-isolation validation."""

from __future__ import annotations

import argparse
import gc
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from benchmarks.run_benchmarks import _process_rss_bytes
from light_image_editor.batch import BatchManifest, BatchRecipe, BatchRunner
from light_image_editor.image_loader import ImageLoader, ImageLoadFailure, decode_image
from light_image_editor.version import __version__

PROFILES = {
    "quick": {"browse_iterations": 200, "batch_count": 100, "max_growth_mib": 96},
    "release": {"browse_iterations": 20_000, "batch_count": 1_000, "max_growth_mib": 192},
}


def _fixtures(root: Path, count: int = 24) -> list[Path]:
    paths: list[Path] = []
    for index in range(count):
        path = root / f"browse-{index:03d}.jpg"
        Image.new("RGB", (640, 480), (index * 7 % 255, 40, 90)).save(path, quality=88)
        paths.append(path)
    return paths


def browse_soak(paths: list[Path], iterations: int, max_growth_mib: int) -> dict[str, object]:
    loader = ImageLoader(max_workers=2, cache_size=3)
    started = time.perf_counter()
    baseline = _process_rss_bytes()
    peak = baseline
    try:
        for index in range(iterations):
            image = loader.load(paths[index % len(paths)]).result(timeout=20)
            image.close()
            if index % len(paths) == 0:
                gc.collect()
                peak = max(peak, _process_rss_bytes())
        gc.collect()
        final = _process_rss_bytes()
        cache_items = len(loader._cache)
    finally:
        loader.shutdown()
    growth = max(0, final - baseline)
    passed = cache_items <= 3 and growth <= max_growth_mib * 1024 * 1024
    return {
        "passed": passed,
        "iterations": iterations,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "cache_items": cache_items,
        "baseline_rss_mib": round(baseline / 1024 / 1024, 3),
        "peak_rss_mib": round(peak / 1024 / 1024, 3),
        "final_growth_mib": round(growth / 1024 / 1024, 3),
        "maximum_growth_mib": max_growth_mib,
    }


def batch_soak(root: Path, count: int) -> dict[str, object]:
    source = root / "batch-input"
    output = root / "batch-output"
    source.mkdir()
    paths: list[Path] = []
    for index in range(count):
        path = source / f"input-{index:04d}.png"
        Image.new("RGB", (16, 12), (index % 255, 80, 120)).save(path)
        paths.append(path)
    damaged = source / "damaged.png"
    damaged.write_bytes(b"not an image")
    missing = source / "missing.png"
    paths.extend((damaged, missing))
    manifest = BatchManifest.create(
        paths,
        output,
        BatchRecipe(output_format="JPEG", rename_template="{stem}_{index:04d}"),
        concurrency=4,
        memory_limit_mb=64,
    )
    started = time.perf_counter()
    result = BatchRunner(manifest, root / "batch-manifest.json").run()
    counts = result.counts()
    passed = (
        counts["succeeded"] == count
        and counts["failed"] == 2
        and result.peak_reserved_bytes <= 64 * 1024 * 1024
    )
    return {
        "passed": passed,
        "requested_valid": count,
        "injected_failures": 2,
        "counts": counts,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "peak_reserved_bytes": result.peak_reserved_bytes,
    }


def failure_injection(root: Path) -> dict[str, object]:
    corrupt = root / "corrupt.jpg"
    corrupt.write_bytes(b"invalid image bytes")
    missing = root / "does-not-exist.png"
    observed: dict[str, str] = {}
    for name, path in (("corrupt", corrupt), ("missing", missing)):
        try:
            decode_image(path)
        except ImageLoadFailure as error:
            observed[name] = error.kind
    return {
        "passed": observed.get("corrupt") == "damaged" and observed.get("missing") == "missing",
        "observed": observed,
    }


def run(profile_name: str) -> dict[str, object]:
    profile = PROFILES[profile_name]
    with tempfile.TemporaryDirectory(prefix="light-image-editor-soak-") as directory:
        root = Path(directory)
        paths = _fixtures(root)
        results = {
            "browse": browse_soak(
                paths,
                int(profile["browse_iterations"]),
                int(profile["max_growth_mib"]),
            ),
            "batch": batch_soak(root, int(profile["batch_count"])),
            "failure_injection": failure_injection(root),
        }
    return {
        "schema_version": 1,
        "application_version": __version__,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "profile": profile_name,
        "passed": all(bool(item["passed"]) for item in results.values()),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=tuple(PROFILES), default="quick")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = run(arguments.profile)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if arguments.output:
        arguments.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
