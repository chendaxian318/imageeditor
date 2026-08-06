"""Strict, fail-closed readiness audit for the four-mode v2.0 release."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

EXPECTED_FIELDS = {
    "id",
    "area",
    "title",
    "status",
    "evidence",
    "closure",
}
VALID_STATUSES = {"passed", "blocked", "pending", "external"}
PROVIDER_DEPENDENT_GATES = {
    "V2-SMART-EDIT",
    "V2-GENERATION",
    "V2-AI-PRIVACY-COST",
}
PROVIDER_STATUS_RELATIVE_PATH = "docs/release/v2-provider-selection-status.json"
COMPATIBILITY_STATUS_RELATIVE_PATH = "docs/release/v2-compatibility-evidence-status.json"
INSTALL_STATUS_RELATIVE_PATH = "docs/release/v2-install-preflight.json"
SUPPLY_CHAIN_STATUS_RELATIVE_PATH = "docs/release/v2-supply-chain-status.json"
LICENSE_STATUS_RELATIVE_PATH = "docs/release/v2-license-evidence-status.json"
RELEASE_STATUS_FIELDS = {
    "schema_version",
    "release",
    "passed",
    "blocker_count",
    "blockers",
}
INSTALL_STATUS_FIELDS = {
    "schema_version",
    "target_version",
    "passed",
    "blocker_count",
    "blockers",
}


def _is_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_evidence_path(repository_root: Path, raw_path: str) -> Path | None:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return None
    root = repository_root.resolve(strict=False)
    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _release_status_problem(
    path: Path,
    expected_release: str,
    label: str,
) -> str | None:
    try:
        raw_status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return f"无法读取{label}状态：{error}"
    if not isinstance(raw_status, dict) or set(raw_status) != RELEASE_STATUS_FIELDS:
        return f"{label}状态字段无效"
    if raw_status.get("schema_version") != 1:
        return f"{label}状态 schema_version 无效"
    if raw_status.get("release") != expected_release:
        return f"{label}状态未指向 {expected_release}"
    blocker_count = raw_status.get("blocker_count")
    status_blockers = raw_status.get("blockers")
    if (
        not isinstance(blocker_count, int)
        or isinstance(blocker_count, bool)
        or not isinstance(status_blockers, list)
        or any(not isinstance(item, str) for item in status_blockers)
        or blocker_count != len(status_blockers)
    ):
        return f"{label}状态的阻塞项统计无效"
    if raw_status.get("passed") is not True or blocker_count != 0:
        return f"{label}尚未通过"
    return None


def _install_status_problem(path: Path, expected_release: str) -> str | None:
    try:
        raw_status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return f"无法读取安装候选预检状态：{error}"
    if not isinstance(raw_status, dict) or set(raw_status) != INSTALL_STATUS_FIELDS:
        return "安装候选预检状态字段无效"
    if raw_status.get("schema_version") != 1:
        return "安装候选预检状态 schema_version 无效"
    if raw_status.get("target_version") != f"{expected_release}.0":
        return f"安装候选预检状态未指向 {expected_release}.0"
    blocker_count = raw_status.get("blocker_count")
    status_blockers = raw_status.get("blockers")
    if (
        not isinstance(blocker_count, int)
        or isinstance(blocker_count, bool)
        or not isinstance(status_blockers, list)
        or any(not isinstance(item, str) for item in status_blockers)
        or blocker_count != len(status_blockers)
    ):
        return "安装候选预检状态的阻塞项统计无效"
    if raw_status.get("passed") is not True or blocker_count != 0:
        return "安装候选预检尚未通过"
    return None


def verify(
    document: dict[str, Any],
    repository_root: Path,
    *,
    expected_release: str = "2.0",
    provider_status_path: Path | None = None,
    compatibility_status_path: Path | None = None,
    install_status_path: Path | None = None,
    supply_chain_status_path: Path | None = None,
    license_status_path: Path | None = None,
) -> list[str]:
    """Return every contract or readiness blocker without trusting malformed input."""

    blockers: list[str] = []
    expected_document_fields = {
        "schema_version",
        "release",
        "generated_at",
        "policy",
        "gates",
    }
    if set(document) != expected_document_fields:
        blockers.append("V2-CONTRACT: 顶层字段不完整或包含未知字段")
    if document.get("schema_version") != 1:
        blockers.append("V2-CONTRACT: schema_version 必须为 1")
    if document.get("release") != expected_release:
        blockers.append(f"V2-VERSION: 发布门禁必须指向 {expected_release}")
    try:
        date.fromisoformat(str(document.get("generated_at", "")))
    except ValueError:
        blockers.append("V2-CONTRACT: generated_at 必须是 ISO 日期")
    if not _is_nonempty_text(document.get("policy")):
        blockers.append("V2-CONTRACT: policy 不能为空")

    raw_gates = document.get("gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        blockers.append("V2-CONTRACT: gates 必须是非空数组")
        return blockers

    seen_ids: set[str] = set()
    for index, raw_gate in enumerate(raw_gates):
        prefix = f"V2-CONTRACT[{index}]"
        if not isinstance(raw_gate, dict):
            blockers.append(f"{prefix}: 门禁必须是对象")
            continue
        if set(raw_gate) != EXPECTED_FIELDS:
            blockers.append(f"{prefix}: 字段不完整或包含未知字段")
        gate_id = raw_gate.get("id")
        if not _is_nonempty_text(gate_id) or not str(gate_id).startswith("V2-"):
            blockers.append(f"{prefix}: id 必须是 V2- 开头的非空字符串")
            gate_id = f"{prefix}-INVALID"
        else:
            gate_id = str(gate_id)
        if gate_id in seen_ids:
            blockers.append(f"{prefix}: 门禁编号重复：{gate_id}")
        seen_ids.add(gate_id)
        if not _is_nonempty_text(raw_gate.get("area")):
            blockers.append(f"{gate_id}: area 不能为空")
        if not _is_nonempty_text(raw_gate.get("title")):
            blockers.append(f"{gate_id}: title 不能为空")
        status = raw_gate.get("status")
        valid_status = isinstance(status, str) and status in VALID_STATUSES
        if not valid_status:
            blockers.append(f"{gate_id}: status 无效")
        closure = raw_gate.get("closure")
        if status == "passed":
            if closure not in ("", None):
                blockers.append(f"{gate_id}: 已通过门禁的 closure 必须为空")
        elif valid_status and not _is_nonempty_text(closure):
            blockers.append(f"{gate_id}: 未通过门禁必须说明关闭条件")

        evidence = raw_gate.get("evidence")
        if not isinstance(evidence, list) or any(not _is_nonempty_text(item) for item in evidence):
            blockers.append(f"{gate_id}: evidence 必须是字符串数组")
            continue
        if status == "passed" and not evidence:
            blockers.append(f"{gate_id}: 已通过门禁必须提供证据")
        for evidence_path in evidence:
            assert isinstance(evidence_path, str)
            resolved = _safe_evidence_path(repository_root, evidence_path)
            if resolved is None:
                blockers.append(f"{gate_id}: 证据路径越出仓库：{evidence_path}")
            elif status == "passed" and not resolved.is_file():
                blockers.append(f"{gate_id}: 已通过门禁缺少证据：{evidence_path}")

        if status == "passed" and gate_id in PROVIDER_DEPENDENT_GATES:
            if PROVIDER_STATUS_RELATIVE_PATH not in evidence:
                blockers.append(
                    f"{gate_id}: 必须引用生产 Provider 选型状态证据："
                    f"{PROVIDER_STATUS_RELATIVE_PATH}"
                )
            status_path = provider_status_path
            if status_path is None:
                status_path = repository_root / PROVIDER_STATUS_RELATIVE_PATH
            elif not status_path.is_absolute():
                status_path = repository_root / status_path
            problem = _release_status_problem(
                status_path,
                expected_release,
                "生产 Provider 选型",
            )
            if problem is not None:
                blockers.append(f"{gate_id}: {problem}")

        if status == "passed" and gate_id == "V2-COMPAT":
            if COMPATIBILITY_STATUS_RELATIVE_PATH not in evidence:
                blockers.append(
                    f"{gate_id}: 必须引用外部实机兼容性状态证据："
                    f"{COMPATIBILITY_STATUS_RELATIVE_PATH}"
                )
            status_path = compatibility_status_path
            if status_path is None:
                status_path = repository_root / COMPATIBILITY_STATUS_RELATIVE_PATH
            elif not status_path.is_absolute():
                status_path = repository_root / status_path
            problem = _release_status_problem(
                status_path,
                expected_release,
                "外部实机兼容性",
            )
            if problem is not None:
                blockers.append(f"{gate_id}: {problem}")

        if status == "passed" and gate_id == "V2-INSTALL-UPGRADE":
            if INSTALL_STATUS_RELATIVE_PATH not in evidence:
                blockers.append(
                    f"{gate_id}: 必须引用安装候选预检状态证据：{INSTALL_STATUS_RELATIVE_PATH}"
                )
            status_path = install_status_path
            if status_path is None:
                status_path = repository_root / INSTALL_STATUS_RELATIVE_PATH
            elif not status_path.is_absolute():
                status_path = repository_root / status_path
            problem = _install_status_problem(status_path, expected_release)
            if problem is not None:
                blockers.append(f"{gate_id}: {problem}")

        if status == "passed" and gate_id == "V2-SIGN-MALWARE":
            if SUPPLY_CHAIN_STATUS_RELATIVE_PATH not in evidence:
                blockers.append(
                    f"{gate_id}: 必须引用供应链安全状态证据：{SUPPLY_CHAIN_STATUS_RELATIVE_PATH}"
                )
            status_path = supply_chain_status_path
            if status_path is None:
                status_path = repository_root / SUPPLY_CHAIN_STATUS_RELATIVE_PATH
            elif not status_path.is_absolute():
                status_path = repository_root / status_path
            problem = _release_status_problem(
                status_path,
                expected_release,
                "签名与恶意软件扫描",
            )
            if problem is not None:
                blockers.append(f"{gate_id}: {problem}")

        if status == "passed" and gate_id == "V2-LICENSE":
            if LICENSE_STATUS_RELATIVE_PATH not in evidence:
                blockers.append(
                    f"{gate_id}: 必须引用许可授权状态证据：{LICENSE_STATUS_RELATIVE_PATH}"
                )
            status_path = license_status_path
            if status_path is None:
                status_path = repository_root / LICENSE_STATUS_RELATIVE_PATH
            elif not status_path.is_absolute():
                status_path = repository_root / status_path
            problem = _release_status_problem(
                status_path,
                expected_release,
                "许可授权",
            )
            if problem is not None:
                blockers.append(f"{gate_id}: {problem}")

        if status in ("blocked", "pending", "external") and _is_nonempty_text(closure):
            blockers.append(f"{gate_id}: {str(closure).strip()}")
    return blockers


def summarize(document: dict[str, Any], blockers: list[str]) -> dict[str, object]:
    raw_gates = document.get("gates", [])
    gates = (
        [gate for gate in raw_gates if isinstance(gate, dict)]
        if isinstance(raw_gates, list)
        else []
    )
    status_counts = Counter(
        status
        for gate in gates
        if isinstance((status := gate.get("status")), str) and status in VALID_STATUSES
    )
    return {
        "schema_version": 1,
        "release": document.get("release"),
        "passed": not blockers,
        "gate_count": len(gates),
        "status_counts": {
            status: status_counts.get(status, 0) for status in sorted(VALID_STATUSES)
        },
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 v2.0 四模式发布就绪状态")
    parser.add_argument("--gates", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument(
        "--provider-status",
        type=Path,
        help="构建时新生成的生产 Provider 选型状态；默认读取仓库发布状态",
    )
    parser.add_argument(
        "--compatibility-status",
        type=Path,
        help="构建时新生成的外部实机兼容性状态；默认读取仓库发布状态",
    )
    parser.add_argument(
        "--install-status",
        type=Path,
        help="构建时新生成的安装候选预检状态；默认读取仓库发布状态",
    )
    parser.add_argument(
        "--supply-chain-status",
        type=Path,
        help="构建时新生成的签名与恶意软件扫描状态；默认读取仓库发布状态",
    )
    parser.add_argument(
        "--license-status",
        type=Path,
        help="构建时新生成的许可授权状态；默认读取仓库发布状态",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-blocked", action="store_true")
    arguments = parser.parse_args()
    try:
        document = json.loads(arguments.gates.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        result: dict[str, object] = {
            "schema_version": 1,
            "release": "2.0",
            "passed": False,
            "gate_count": 0,
            "status_counts": {},
            "blockers": [f"V2-CONTRACT: 无法读取发布门禁：{error}"],
        }
    else:
        if not isinstance(document, dict):
            result = {
                "schema_version": 1,
                "release": "2.0",
                "passed": False,
                "gate_count": 0,
                "status_counts": {},
                "blockers": ["V2-CONTRACT: 发布门禁根节点必须是对象"],
            }
        else:
            blockers = verify(
                document,
                arguments.repository,
                provider_status_path=arguments.provider_status,
                compatibility_status_path=arguments.compatibility_status,
                install_status_path=arguments.install_status,
                supply_chain_status_path=arguments.supply_chain_status,
                license_status_path=arguments.license_status,
            )
            result = summarize(document, blockers)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    print(serialized, end="")
    if arguments.output is not None:
        arguments.output.write_text(serialized, encoding="utf-8")
    return 0 if result["passed"] or arguments.allow_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
