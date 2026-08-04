"""Fail closed unless every time-based, external and automated RC gate has evidence."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def verify(gates: dict[str, Any], stability: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    blockers = [
        f"{gate['id']}: {gate['closure']}" for gate in gates["gates"] if gate["status"] != "passed"
    ]
    entries = stability.get("entries", [])
    required = int(stability.get("required_consecutive_days", 7))
    if len(entries) < required:
        blockers.append(f"RC-STABILITY: only {len(entries)} of {required} daily entries exist")
    else:
        latest = entries[-required:]
        days = [date.fromisoformat(str(item["date"])) for item in latest]
        if any(
            days[index] + timedelta(days=1) != days[index + 1] for index in range(len(days) - 1)
        ):
            blockers.append("RC-STABILITY: entries are not consecutive calendar days")
        if any(int(item["new_p0"]) or int(item["new_p1"]) for item in latest):
            blockers.append("RC-STABILITY: a daily entry contains a new P0/P1")
        if int(latest[-1]["open_p0"]) or int(latest[-1]["open_p1"]):
            blockers.append("RC-STABILITY: P0/P1 remain open")
    source = evidence["source"]
    if source["dirty"]:
        blockers.append("RC-TRACE: build source was dirty")
    if not evidence["build"]["tests_executed_in_this_build"]:
        blockers.append("RC-TRACE: final build did not execute tests")
    if not any(str(tag).startswith("v0.9.0-rc.") for tag in source["tags"]):
        blockers.append("RC-TRACE: commit has no annotated v0.9.0-rc.N tag")
    return blockers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gates", type=Path, required=True)
    parser.add_argument("--stability", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-blocked", action="store_true")
    arguments = parser.parse_args()
    blockers = verify(
        json.loads(arguments.gates.read_text(encoding="utf-8")),
        json.loads(arguments.stability.read_text(encoding="utf-8")),
        json.loads(arguments.evidence.read_text(encoding="utf-8")),
    )
    result = {"schema_version": 1, "passed": not blockers, "blockers": blockers}
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if arguments.output:
        arguments.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if not blockers or arguments.allow_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
