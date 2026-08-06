"""Run the M2 viewer acceptance gate against the production Tk application."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {
        "large_size": (1600, 1200),
        "switch_count": 4,
        "directory_count": 40,
        "navigation_steps": 10,
        "navigation_size": (480, 320),
        "open_repetitions": 1,
        "switch_repetitions": 1,
    },
    "standard": {
        "large_size": (6000, 4000),
        "switch_count": 6,
        "directory_count": 500,
        "navigation_steps": 100,
        "navigation_size": (640, 480),
        "open_repetitions": 5,
        "switch_repetitions": 3,
    },
}


@dataclass(frozen=True, slots=True)
class ViewerGateThresholds:
    first_preview_p95_ms: float = 1800.0
    prefetched_switch_p95_ms: float = 300.0
    navigation_p95_ms: float = 300.0
    directory_count: int = 500
    navigation_steps: int = 100
    final_rss_growth_mib: float = 256.0
    monotonic_growth_review_mib: float = 64.0


def evaluate_viewer_gate(
    result: dict[str, Any],
    thresholds: ViewerGateThresholds | None = None,
) -> list[str]:
    """Return every unmet M2 acceptance condition instead of failing at the first one."""

    limits = thresholds or ViewerGateThresholds()
    metrics = result["metrics"]
    violations: list[str] = []
    first_p95 = float(metrics["first_preview"]["p95"])
    switch_p95 = float(metrics["prefetched_switch"]["p95"])
    navigation_p95 = float(metrics["directory_navigation"]["p95"])
    if first_p95 > limits.first_preview_p95_ms:
        violations.append(
            f"24 MP 首次预览 P95 {first_p95:.1f} ms > {limits.first_preview_p95_ms:.1f} ms"
        )
    if switch_p95 > limits.prefetched_switch_p95_ms:
        violations.append(
            f"预取切图 P95 {switch_p95:.1f} ms > {limits.prefetched_switch_p95_ms:.1f} ms"
        )
    if navigation_p95 > limits.navigation_p95_ms:
        violations.append(
            f"500 张目录导航 P95 {navigation_p95:.1f} ms > {limits.navigation_p95_ms:.1f} ms"
        )
    fixtures = result["fixtures"]
    if int(fixtures["directory_count"]) < limits.directory_count:
        violations.append(
            f"目录样本只有 {fixtures['directory_count']} 张，要求至少 {limits.directory_count} 张"
        )
    if int(fixtures["navigation_steps"]) < limits.navigation_steps:
        violations.append(
            f"连续浏览只有 {fixtures['navigation_steps']} 张，要求至少 {limits.navigation_steps} 张"
        )
    memory = result["memory"]
    growth_mib = float(memory["final_growth_mib"])
    if growth_mib > limits.final_rss_growth_mib:
        violations.append(
            f"连续浏览工作集增长 {growth_mib:.1f} MiB > {limits.final_rss_growth_mib:.1f} MiB"
        )
    if bool(memory["monotonic_non_decreasing"]) and growth_mib > limits.monotonic_growth_review_mib:
        violations.append(
            "连续浏览工作集持续单调增长，且累计增长 "
            f"{growth_mib:.1f} MiB > {limits.monotonic_growth_review_mib:.1f} MiB"
        )
    return violations


def run(profile_name: str) -> dict[str, Any]:
    from benchmarks.run_benchmarks import (
        _create_pattern_image,
        _metric,
        _read_worker,
        _worker_command,
    )

    profile = PROFILES[profile_name]
    with tempfile.TemporaryDirectory(prefix="light-image-editor-viewer-gate-") as raw_temp:
        root = Path(raw_temp)
        large = root / "24mp.jpg"
        _create_pattern_image(large, tuple(profile["large_size"]), seed=1)

        switch_dir = root / "switch"
        switch_dir.mkdir()
        switch_paths: list[Path] = []
        for index in range(int(profile["switch_count"])):
            path = switch_dir / f"switch-{index:03d}.jpg"
            _create_pattern_image(path, tuple(profile["large_size"]), seed=index + 10)
            switch_paths.append(path)

        directory = root / "directory"
        directory.mkdir()
        for index in range(int(profile["directory_count"])):
            _create_pattern_image(
                directory / f"image-{index:04d}.jpg",
                tuple(profile["navigation_size"]),
                seed=index + 100,
            )

        first_preview_samples: list[float] = []
        for _ in range(int(profile["open_repetitions"])):
            payload, _ = _read_worker(_worker_command("open", str(large)))
            first_preview_samples.append(float(payload["open_and_render_ms"]))

        switch_samples: list[float] = []
        for _ in range(int(profile["switch_repetitions"])):
            payload, _ = _read_worker(
                _worker_command("switch", *(str(path) for path in switch_paths))
            )
            switch_samples.extend(float(value) for value in payload["switch_samples_ms"])

        navigation_payload, _ = _read_worker(
            _worker_command(
                "viewer-navigation",
                str(directory),
                str(profile["navigation_steps"]),
            )
        )
        navigation_samples = [float(value) for value in navigation_payload["navigation_samples_ms"]]
        final_growth_mib = float(navigation_payload["final_growth_bytes"]) / (1024 * 1024)
        peak_rss_mib = float(navigation_payload["peak_rss_bytes"]) / (1024 * 1024)

    result: dict[str, Any] = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "profile": profile_name,
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "fixtures": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in profile.items()
        },
        "metrics": {
            "first_preview": _metric(first_preview_samples),
            "prefetched_switch": _metric(switch_samples),
            "directory_navigation": _metric(navigation_samples),
        },
        "memory": {
            "final_growth_mib": round(final_growth_mib, 3),
            "peak_rss_mib": round(peak_rss_mib, 3),
            "monotonic_non_decreasing": bool(navigation_payload["monotonic_non_decreasing"]),
        },
        "thresholds": asdict(ViewerGateThresholds()),
    }
    result["violations"] = [] if profile_name == "smoke" else evaluate_viewer_gate(result)
    result["passed"] = not result["violations"]
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="standard")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="记录门禁失败但仍返回成功退出码",
    )
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
