"""Fail closed unless a v1.0 bundle is safe to publish as the stable release."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def verify(
    gates: dict[str, Any],
    priorities: dict[str, Any],
    stability: dict[str, Any],
    evidence: dict[str, Any],
    security: dict[str, Any],
    release: Path,
    expected_version: str = "1.0.0",
    candidate: bool = False,
) -> list[str]:
    blockers = [
        f"{gate['id']}: {gate['closure']}"
        for gate in gates.get("gates", [])
        if gate.get("status") != "passed"
        and not (
            gate.get("id") == "V1-MALWARE"
            and (candidate or security.get("MultiEngineReview") == "approved")
        )
    ]
    if gates.get("release") != expected_version:
        blockers.append("V1-VERSION: release gate file does not target 1.0.0")
    if priorities.get("release") != expected_version:
        blockers.append("V1-PRIORITY: priority matrix does not target 1.0.0")
    for item in priorities.get("items", []):
        item_id = str(item.get("id", "unknown"))
        item_status = item.get("status")
        signing_evidence_passed = (
            item_id == "P0-SIGN" and security.get("AuthenticodePassed") is True
        )
        if item.get("priority") == "P0" and item_status != "passed" and not signing_evidence_passed:
            blockers.append(f"V1-PRIORITY: {item_id} is not complete")
        if item.get("introduced_after_freeze") is True:
            exchange = item.get("scope_exchange")
            if not isinstance(exchange, dict):
                blockers.append(f"V1-SCOPE: {item_id} was added without an exchanged scope item")
                continue
            removed = int(exchange.get("removed_effort_points", 0))
            added = int(exchange.get("added_effort_points", 0))
            if not exchange.get("removed_id") or added <= 0 or removed < added:
                blockers.append(f"V1-SCOPE: {item_id} does not have an equal scope exchange")

    entries = stability.get("entries", [])
    required = int(stability.get("required_consecutive_days", 7))
    if len(entries) < required:
        blockers.append(f"V1-STABILITY: only {len(entries)} of {required} daily entries exist")
    else:
        latest = entries[-required:]
        days = [date.fromisoformat(str(item["date"])) for item in latest]
        if any(
            days[index] + timedelta(days=1) != days[index + 1] for index in range(len(days) - 1)
        ):
            blockers.append("V1-STABILITY: entries are not consecutive calendar days")
        if any(int(item["new_p0"]) or int(item["new_p1"]) for item in latest):
            blockers.append("V1-STABILITY: a daily entry contains a new P0/P1")
        if int(latest[-1]["open_p0"]) or int(latest[-1]["open_p1"]):
            blockers.append("V1-STABILITY: P0/P1 remain open")

    if evidence.get("application_version") != expected_version:
        blockers.append("V1-TRACE: provenance contains the wrong application version")
    source = evidence.get("source", {})
    if source.get("dirty"):
        blockers.append("V1-TRACE: build source was dirty")
    if not evidence.get("build", {}).get("tests_executed_in_this_build"):
        blockers.append("V1-TRACE: final build did not execute tests")
    if f"v{expected_version}" not in source.get("tags", []):
        blockers.append(f"V1-TRACE: commit has no v{expected_version} tag")
    if f"v{expected_version}" not in source.get("annotated_tags", []):
        blockers.append(f"V1-TRACE: v{expected_version} is not an annotated tag")

    expected_artifacts = {
        "LightImageEditor/ImageEditor.exe",
        f"CSImage-Portable-x64-{expected_version}.exe",
        f"CSImage-Setup-x64-{expected_version}.exe",
        "SBOM.cdx.json",
    }
    artifacts = {
        str(item.get("path")): item
        for item in evidence.get("artifacts", [])
        if isinstance(item, dict)
    }
    for relative in sorted(expected_artifacts):
        item = artifacts.get(relative)
        path = release / Path(relative)
        if item is None or not path.is_file():
            blockers.append(f"V1-TRACE: missing provenance artifact {relative}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item.get("sha256") or path.stat().st_size != item.get("size"):
            blockers.append(f"V1-TRACE: provenance mismatch for {relative}")

    if not security.get("AuthenticodePassed"):
        blockers.append("V1-SIGN: not every release executable has a valid signature")
    if security.get("DefenderStatus") != "passed":
        blockers.append("V1-MALWARE: Microsoft Defender scan did not pass")
    if not candidate:
        if security.get("MultiEngineReview") != "approved":
            blockers.append("V1-MALWARE: approved multi-engine evidence is missing")
        if not security.get("PublicReleaseReady"):
            blockers.append("V1-MALWARE: security report does not allow public release")

    rollback = list(release.glob("CSImage-Rollback-x64-*.exe"))
    if len(rollback) != 1:
        blockers.append("V1-ROLLBACK: exactly one signed previous-version installer is required")
    for required_name in (
        "update-stable.json",
        "SHA256SUMS.txt",
        "SBOM.cdx.json",
        "THIRD_PARTY_LICENSES.md",
        "V1_PRIORITY_MATRIX.json",
        "RELEASE_NOTES-v1.0.0.md",
        "POST_RELEASE_MONITORING.md",
        "HOTFIX_RUNBOOK.md",
    ):
        if not (release / required_name).is_file():
            blockers.append(f"V1-BUNDLE: missing {required_name}")
    return blockers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gates", type=Path, required=True)
    parser.add_argument("--priorities", type=Path, required=True)
    parser.add_argument("--stability", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--security", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-blocked", action="store_true")
    parser.add_argument("--candidate", action="store_true")
    arguments = parser.parse_args()
    blockers = verify(
        json.loads(arguments.gates.read_text(encoding="utf-8")),
        json.loads(arguments.priorities.read_text(encoding="utf-8")),
        json.loads(arguments.stability.read_text(encoding="utf-8")),
        json.loads(arguments.evidence.read_text(encoding="utf-8")),
        json.loads(arguments.security.read_text(encoding="utf-8-sig")),
        arguments.release,
        candidate=arguments.candidate,
    )
    result = {"schema_version": 1, "passed": not blockers, "blockers": blockers}
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    arguments.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if not blockers or arguments.allow_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
