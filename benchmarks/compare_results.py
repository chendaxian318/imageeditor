"""Compare a benchmark result with an accepted same-machine baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

LOWER_PATHS = {
    "startup.launch_to_ready": ("startup", "launch_to_ready"),
    "open_large_image.open_and_render": ("open_large_image", "open_and_render"),
    "switch_images.per_image": ("switch_images", "per_image"),
    "batch_process.round_total": ("batch_process", "round_total"),
}
HIGHER_PATHS = {"batch_process.throughput": ("batch_process", "throughput")}


def compare(
    baseline: dict[str, Any],
    current: dict[str, Any],
    maximum_regression_percent: float,
) -> dict[str, Any]:
    if baseline["profile"] != current["profile"]:
        raise ValueError("benchmark profiles do not match")
    for key in ("machine", "python", "pillow"):
        if baseline["environment"][key] != current["environment"][key]:
            raise ValueError(f"benchmark environment mismatch: {key}")

    comparisons: dict[str, Any] = {}
    failures: list[str] = []
    for name, path in {**LOWER_PATHS, **HIGHER_PATHS}.items():
        before = float(baseline["metrics"][name])
        after = float(current["benchmarks"][path[0]][path[1]]["median"])
        if name in LOWER_PATHS:
            regression = ((after / before) - 1.0) * 100.0
        else:
            regression = (1.0 - (after / before)) * 100.0
        passed = regression <= maximum_regression_percent
        comparisons[name] = {
            "baseline": before,
            "current": after,
            "regression_percent": round(regression, 3),
            "passed": passed,
        }
        if not passed:
            failures.append(name)
    return {
        "schema_version": 1,
        "maximum_regression_percent": maximum_regression_percent,
        "passed": not failures,
        "failures": failures,
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--maximum-regression-percent", type=float, default=15.0)
    arguments = parser.parse_args()
    result = compare(
        json.loads(arguments.baseline.read_text(encoding="utf-8")),
        json.loads(arguments.current.read_text(encoding="utf-8")),
        arguments.maximum_regression_percent,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if arguments.output:
        arguments.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
