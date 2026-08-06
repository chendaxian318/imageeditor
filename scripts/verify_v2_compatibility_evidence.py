"""Validate imported Windows compatibility evidence without trusting status labels."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

CONTRACT_FIELDS = {"schema_version", "release", "scenarios"}
SCENARIO_FIELDS = {
    "id",
    "os_name",
    "minimum_build",
    "account_type",
    "network_mode",
    "display_mode",
    "required_dpi",
    "required_packages",
    "minimum_install_cycles",
}
EVIDENCE_FIELDS = {"schema_version", "release", "runs"}
RUN_FIELDS = {
    "scenario_id",
    "captured_at_utc",
    "os_name",
    "os_build",
    "architecture",
    "account_type",
    "network_mode",
    "display_mode",
    "dpi_scales",
    "package_version",
    "package_kinds",
    "install_cycles",
    "machine_label",
    "passed",
    "files",
}
REQUIRED_FILES = {"install_log", "diagnostic_report", "test_result"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_path(root: Path, relative: str) -> Path | None:
    candidate = Path(relative)
    if candidate.is_absolute():
        return None
    resolved_root = root.resolve(strict=False)
    resolved = (resolved_root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved


def verify(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    evidence_root: Path,
) -> list[str]:
    blockers: list[str] = []
    if set(contract) != CONTRACT_FIELDS or contract.get("schema_version") != 1:
        blockers.append("V2-COMPAT-CONTRACT: 契约顶层字段或版本无效")
    if set(evidence) != EVIDENCE_FIELDS or evidence.get("schema_version") != 1:
        blockers.append("V2-COMPAT-EVIDENCE: 证据顶层字段或版本无效")
    if contract.get("release") != "2.0" or evidence.get("release") != "2.0":
        blockers.append("V2-COMPAT-VERSION: 契约和证据必须指向 2.0")
    scenarios = contract.get("scenarios")
    runs = evidence.get("runs")
    if not isinstance(scenarios, list) or not scenarios:
        blockers.append("V2-COMPAT-CONTRACT: scenarios 必须是非空数组")
        return blockers
    if not isinstance(runs, list):
        blockers.append("V2-COMPAT-EVIDENCE: runs 必须是数组")
        return blockers

    required: dict[str, dict[str, Any]] = {}
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict) or set(scenario) != SCENARIO_FIELDS:
            blockers.append(f"V2-COMPAT-CONTRACT[{index}]: 场景字段无效")
            continue
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            blockers.append(f"V2-COMPAT-CONTRACT[{index}]: 场景编号无效")
            continue
        if scenario_id in required:
            blockers.append(f"V2-COMPAT-CONTRACT: 场景编号重复：{scenario_id}")
        required[scenario_id] = scenario

    observed: dict[str, dict[str, Any]] = {}
    for index, run in enumerate(runs):
        if not isinstance(run, dict) or set(run) != RUN_FIELDS:
            blockers.append(f"V2-COMPAT-EVIDENCE[{index}]: 运行字段无效")
            continue
        scenario_id = run.get("scenario_id")
        if not isinstance(scenario_id, str) or scenario_id not in required:
            blockers.append(f"V2-COMPAT-EVIDENCE[{index}]: 未知场景 {scenario_id}")
            continue
        if scenario_id in observed:
            blockers.append(f"V2-COMPAT-EVIDENCE: 场景证据重复：{scenario_id}")
            continue
        observed[scenario_id] = run

    for scenario_id, scenario in required.items():
        run = observed.get(scenario_id)
        if run is None:
            blockers.append(f"V2-COMPAT-MISSING: 缺少场景 {scenario_id}")
            continue
        prefix = f"V2-COMPAT-{scenario_id}"
        try:
            datetime.fromisoformat(str(run["captured_at_utc"]).replace("Z", "+00:00"))
        except ValueError:
            blockers.append(f"{prefix}: captured_at_utc 无效")
        comparisons = (
            ("os_name", "操作系统"),
            ("account_type", "账户类型"),
            ("network_mode", "网络模式"),
            ("display_mode", "显示器模式"),
        )
        for field, label in comparisons:
            if run.get(field) != scenario.get(field):
                blockers.append(f"{prefix}: {label}不符合契约")
        if run.get("architecture") != "x64":
            blockers.append(f"{prefix}: 必须使用 x64")
        if not isinstance(run.get("os_build"), int) or int(run["os_build"]) < int(
            scenario["minimum_build"]
        ):
            blockers.append(f"{prefix}: 系统构建号不足")
        if run.get("package_version") != "2.0.0":
            blockers.append(f"{prefix}: 包版本必须为 2.0.0")
        for field, label in (("dpi_scales", "DPI"), ("package_kinds", "包类型")):
            actual = run.get(field)
            required_values = scenario[
                "required_dpi" if field == "dpi_scales" else "required_packages"
            ]
            if not isinstance(actual, list) or not set(required_values) <= set(actual):
                blockers.append(f"{prefix}: {label}覆盖不足")
        if not isinstance(run.get("install_cycles"), int) or int(run["install_cycles"]) < int(
            scenario["minimum_install_cycles"]
        ):
            blockers.append(f"{prefix}: 安装生命周期次数不足")
        if not isinstance(run.get("machine_label"), str) or not run["machine_label"].strip():
            blockers.append(f"{prefix}: 实机标签不能为空")
        if run.get("passed") is not True:
            blockers.append(f"{prefix}: 场景未通过")
        files = run.get("files")
        if not isinstance(files, dict) or set(files) != REQUIRED_FILES:
            blockers.append(f"{prefix}: 证据文件字段不完整")
            continue
        for name, descriptor in files.items():
            if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
                blockers.append(f"{prefix}: {name} 描述无效")
                continue
            raw_path = descriptor.get("path")
            digest = descriptor.get("sha256")
            if not isinstance(raw_path, str):
                blockers.append(f"{prefix}: {name} 路径无效")
                continue
            path = _safe_path(evidence_root, raw_path)
            if path is None:
                blockers.append(f"{prefix}: {name} 路径越出证据目录")
            elif not path.is_file():
                blockers.append(f"{prefix}: 缺少 {name} 文件")
            elif not isinstance(digest, str) or _sha256(path) != digest.casefold():
                blockers.append(f"{prefix}: {name} SHA-256 不匹配")
    return blockers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-blocked", action="store_true")
    arguments = parser.parse_args()
    contract = json.loads(arguments.contract.read_text(encoding="utf-8"))
    evidence = json.loads(arguments.evidence.read_text(encoding="utf-8"))
    blockers = verify(contract, evidence, arguments.evidence_root)
    result = {
        "schema_version": 1,
        "release": "2.0",
        "passed": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    print(serialized, end="")
    if arguments.output is not None:
        arguments.output.write_text(serialized, encoding="utf-8")
    return 0 if result["passed"] or arguments.allow_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
