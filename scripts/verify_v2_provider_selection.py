"""Validate production AI Provider candidates and explicit human selection evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import urlparse

CONTRACT_FIELDS = {
    "schema_version",
    "release",
    "selection_policy",
    "max_retention_days",
    "required_common_capabilities",
    "required_evidence_files",
    "required_roles",
}
ROLE_FIELDS = {"role", "required_capabilities"}
EVIDENCE_FIELDS = {"schema_version", "release", "candidates", "decisions"}
CANDIDATE_FIELDS = {
    "id",
    "role",
    "provider_name",
    "provider_id",
    "model_id",
    "adapter_id",
    "protocol",
    "documentation_url",
    "pricing_url",
    "privacy_url",
    "terms_url",
    "regions",
    "capabilities",
    "image_upload",
    "retention_days",
    "trains_on_customer_data",
    "training_opt_out_documented",
    "supports_zero_retention",
    "currency",
    "estimated_cost_minor_per_output",
    "max_input_bytes",
    "max_outputs_per_request",
    "rate_limit_per_minute",
    "sandbox_passed",
    "files",
}
DECISION_FIELDS = {
    "role",
    "candidate_id",
    "selected_provider_id",
    "selected_model_id",
    "human_approved",
    "approved_by",
    "approved_at_utc",
    "rationale",
    "max_approved_cost_minor_per_output",
}
DESCRIPTOR_FIELDS = {"path", "sha256"}
EXPECTED_COMMON_CAPABILITIES = {"content_policy", "cancellation", "idempotency"}
EXPECTED_ROLE_CAPABILITIES = {
    "smart_edit": {"upscale", "remove_background", "ocr_text", "replace_text"},
    "generation": {
        "text_to_image",
        "reference_image",
        "variation",
        "negative_prompt",
        "fixed_seed",
    },
}
EXPECTED_EVIDENCE_FILES = {
    "request",
    "response",
    "quality_result",
    "cost_result",
    "privacy_review",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


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


def _is_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _text_set(value: object) -> set[str] | None:
    if not isinstance(value, list) or any(not _is_nonempty_text(item) for item in value):
        return None
    return {str(item) for item in value}


def _is_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _validate_contract(contract: dict[str, Any], blockers: list[str]) -> dict[str, set[str]]:
    if set(contract) != CONTRACT_FIELDS:
        blockers.append("V2-PROVIDER-CONTRACT: 顶层字段不完整或包含未知字段")
    if contract.get("schema_version") != 1:
        blockers.append("V2-PROVIDER-CONTRACT: schema_version 必须为 1")
    if contract.get("release") != "2.0":
        blockers.append("V2-PROVIDER-CONTRACT: release 必须为 2.0")
    if not _is_nonempty_text(contract.get("selection_policy")):
        blockers.append("V2-PROVIDER-CONTRACT: selection_policy 不能为空")
    if contract.get("max_retention_days") != 30:
        blockers.append("V2-PROVIDER-CONTRACT: max_retention_days 必须固定为 30")

    common = _text_set(contract.get("required_common_capabilities"))
    if common != EXPECTED_COMMON_CAPABILITIES:
        blockers.append("V2-PROVIDER-CONTRACT: 通用能力集合与冻结契约不一致")
    files = _text_set(contract.get("required_evidence_files"))
    if files != EXPECTED_EVIDENCE_FILES:
        blockers.append("V2-PROVIDER-CONTRACT: 沙盒证据文件集合与冻结契约不一致")

    required_roles: dict[str, set[str]] = {}
    raw_roles = contract.get("required_roles")
    if not isinstance(raw_roles, list):
        blockers.append("V2-PROVIDER-CONTRACT: required_roles 必须是数组")
        return required_roles
    for index, raw_role in enumerate(raw_roles):
        prefix = f"V2-PROVIDER-CONTRACT[{index}]"
        if not isinstance(raw_role, dict) or set(raw_role) != ROLE_FIELDS:
            blockers.append(f"{prefix}: 角色字段无效")
            continue
        role = raw_role.get("role")
        capabilities = _text_set(raw_role.get("required_capabilities"))
        if not isinstance(role, str) or role not in EXPECTED_ROLE_CAPABILITIES:
            blockers.append(f"{prefix}: 未知角色 {role}")
            continue
        if role in required_roles:
            blockers.append(f"V2-PROVIDER-CONTRACT: 角色重复：{role}")
            continue
        if capabilities != EXPECTED_ROLE_CAPABILITIES[role]:
            blockers.append(f"{prefix}: {role} 能力集合与冻结契约不一致")
            continue
        required_roles[role] = capabilities | EXPECTED_COMMON_CAPABILITIES
    if set(required_roles) != set(EXPECTED_ROLE_CAPABILITIES):
        blockers.append("V2-PROVIDER-CONTRACT: 必须同时定义 smart_edit 与 generation")
    return required_roles


def _validate_candidate(
    candidate: dict[str, Any],
    index: int,
    required_roles: dict[str, set[str]],
    evidence_root: Path,
    max_retention_days: int,
    blockers: list[str],
) -> None:
    candidate_id = candidate.get("id")
    prefix = f"V2-PROVIDER-CANDIDATE[{index}:{candidate_id or '未知'}]"
    if set(candidate) != CANDIDATE_FIELDS:
        blockers.append(f"{prefix}: 字段不完整或包含未知字段")

    for field in ("id", "provider_name", "provider_id", "model_id", "adapter_id", "protocol"):
        if not _is_nonempty_text(candidate.get(field)):
            blockers.append(f"{prefix}: {field} 不能为空")
    role = candidate.get("role")
    if not isinstance(role, str) or role not in required_roles:
        blockers.append(f"{prefix}: role 未纳入冻结契约")
    for field in ("documentation_url", "pricing_url", "privacy_url", "terms_url"):
        if not _is_https_url(candidate.get(field)):
            blockers.append(f"{prefix}: {field} 必须是官方 HTTPS 地址")

    regions = _text_set(candidate.get("regions"))
    if not regions:
        blockers.append(f"{prefix}: regions 必须明确列出处理区域")
    capabilities = _text_set(candidate.get("capabilities"))
    if capabilities is None:
        blockers.append(f"{prefix}: capabilities 必须是非空字符串数组")
    elif isinstance(role, str) and role in required_roles:
        missing = required_roles[role] - capabilities
        if missing:
            blockers.append(f"{prefix}: 缺少能力 {', '.join(sorted(missing))}")

    if candidate.get("image_upload") is not True:
        blockers.append(f"{prefix}: image_upload 必须明确为 true")
    retention = candidate.get("retention_days")
    if not _is_int(retention) or not 0 <= retention <= max_retention_days:
        blockers.append(f"{prefix}: retention_days 必须在 0 至 {max_retention_days} 之间")
    if candidate.get("trains_on_customer_data") is not False:
        blockers.append(f"{prefix}: 禁止将客户数据用于模型训练")
    if candidate.get("training_opt_out_documented") is not True:
        blockers.append(f"{prefix}: 必须提供不用于训练的官方说明")
    if not isinstance(candidate.get("supports_zero_retention"), bool):
        blockers.append(f"{prefix}: supports_zero_retention 必须是布尔值")

    currency = candidate.get("currency")
    if not isinstance(currency, str) or CURRENCY_PATTERN.fullmatch(currency) is None:
        blockers.append(f"{prefix}: currency 必须是三位大写货币代码")
    for field in (
        "estimated_cost_minor_per_output",
        "max_input_bytes",
        "max_outputs_per_request",
        "rate_limit_per_minute",
    ):
        value = candidate.get(field)
        minimum = 0 if field == "estimated_cost_minor_per_output" else 1
        if not _is_int(value) or value < minimum:
            blockers.append(f"{prefix}: {field} 必须是不小于 {minimum} 的整数")
    if candidate.get("sandbox_passed") is not True:
        blockers.append(f"{prefix}: 真实沙盒调用尚未通过")

    files = candidate.get("files")
    if not isinstance(files, dict) or set(files) != EXPECTED_EVIDENCE_FILES:
        blockers.append(f"{prefix}: 沙盒证据文件字段不完整")
        return
    for name, descriptor in files.items():
        file_prefix = f"{prefix}: {name}"
        if not isinstance(descriptor, dict) or set(descriptor) != DESCRIPTOR_FIELDS:
            blockers.append(f"{file_prefix} 描述无效")
            continue
        raw_path = descriptor.get("path")
        digest = descriptor.get("sha256")
        if not isinstance(raw_path, str):
            blockers.append(f"{file_prefix} 路径无效")
            continue
        path = _safe_path(evidence_root, raw_path)
        if path is None:
            blockers.append(f"{file_prefix} 路径越出证据目录")
        elif not path.is_file():
            blockers.append(f"{file_prefix} 文件不存在")
        elif (
            not isinstance(digest, str)
            or SHA256_PATTERN.fullmatch(digest) is None
            or _sha256(path) != digest
        ):
            blockers.append(f"{file_prefix} SHA-256 不匹配")


def verify(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    evidence_root: Path,
) -> list[str]:
    """Return every blocker; never infer or auto-select a Provider."""

    blockers: list[str] = []
    required_roles = _validate_contract(contract, blockers)
    if set(evidence) != EVIDENCE_FIELDS:
        blockers.append("V2-PROVIDER-EVIDENCE: 顶层字段不完整或包含未知字段")
    if evidence.get("schema_version") != 1:
        blockers.append("V2-PROVIDER-EVIDENCE: schema_version 必须为 1")
    if evidence.get("release") != "2.0":
        blockers.append("V2-PROVIDER-EVIDENCE: release 必须为 2.0")

    raw_candidates = evidence.get("candidates")
    raw_decisions = evidence.get("decisions")
    if not isinstance(raw_candidates, list):
        blockers.append("V2-PROVIDER-EVIDENCE: candidates 必须是数组")
        raw_candidates = []
    if not isinstance(raw_decisions, list):
        blockers.append("V2-PROVIDER-EVIDENCE: decisions 必须是数组")
        raw_decisions = []

    candidates: dict[str, dict[str, Any]] = {}
    candidate_roles: dict[str, int] = {role: 0 for role in required_roles}
    max_retention = contract.get("max_retention_days")
    max_retention_days = max_retention if _is_int(max_retention) else 0
    for index, raw_candidate in enumerate(raw_candidates):
        if not isinstance(raw_candidate, dict):
            blockers.append(f"V2-PROVIDER-CANDIDATE[{index}]: 候选必须是对象")
            continue
        candidate_id = raw_candidate.get("id")
        if isinstance(candidate_id, str) and candidate_id:
            if candidate_id in candidates:
                blockers.append(f"V2-PROVIDER-EVIDENCE: 候选编号重复：{candidate_id}")
            else:
                candidates[candidate_id] = raw_candidate
        role = raw_candidate.get("role")
        if isinstance(role, str) and role in candidate_roles:
            candidate_roles[role] += 1
        _validate_candidate(
            raw_candidate,
            index,
            required_roles,
            evidence_root,
            max_retention_days,
            blockers,
        )

    for role in required_roles:
        if candidate_roles[role] == 0:
            blockers.append(f"V2-PROVIDER-MISSING-CANDIDATE: {role} 缺少生产候选")

    decisions: dict[str, dict[str, Any]] = {}
    for index, raw_decision in enumerate(raw_decisions):
        prefix = f"V2-PROVIDER-DECISION[{index}]"
        if not isinstance(raw_decision, dict):
            blockers.append(f"{prefix}: 决策必须是对象")
            continue
        if set(raw_decision) != DECISION_FIELDS:
            blockers.append(f"{prefix}: 字段不完整或包含未知字段")
        role = raw_decision.get("role")
        if not isinstance(role, str) or role not in required_roles:
            blockers.append(f"{prefix}: role 未纳入冻结契约")
            continue
        if role in decisions:
            blockers.append(f"V2-PROVIDER-EVIDENCE: 决策角色重复：{role}")
            continue
        decisions[role] = raw_decision
        candidate_id = raw_decision.get("candidate_id")
        candidate = candidates.get(candidate_id) if isinstance(candidate_id, str) else None
        if candidate is None:
            blockers.append(f"{prefix}: candidate_id 未引用有效候选")
        else:
            if candidate.get("role") != role:
                blockers.append(f"{prefix}: 候选角色与决策角色不一致")
            if raw_decision.get("selected_provider_id") != candidate.get("provider_id"):
                blockers.append(f"{prefix}: selected_provider_id 与候选不一致")
            if raw_decision.get("selected_model_id") != candidate.get("model_id"):
                blockers.append(f"{prefix}: selected_model_id 与候选不一致")
            approved_cap = raw_decision.get("max_approved_cost_minor_per_output")
            candidate_cost = candidate.get("estimated_cost_minor_per_output")
            if not _is_int(approved_cap) or approved_cap < 0:
                blockers.append(f"{prefix}: 批准成本上限必须是非负整数")
            elif _is_int(candidate_cost) and candidate_cost > approved_cap:
                blockers.append(f"{prefix}: 候选估算费用超过人工批准上限")
        if raw_decision.get("human_approved") is not True:
            blockers.append(f"{prefix}: 必须由人工明确批准")
        for field in ("approved_by", "rationale"):
            if not _is_nonempty_text(raw_decision.get(field)):
                blockers.append(f"{prefix}: {field} 不能为空")
        if not _is_utc_timestamp(raw_decision.get("approved_at_utc")):
            blockers.append(f"{prefix}: approved_at_utc 必须是带时区的 UTC 时间")

    for role in required_roles:
        if role not in decisions:
            blockers.append(f"V2-PROVIDER-MISSING-DECISION: {role} 缺少人工选型决策")
    return blockers


def summarize(blockers: list[str]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "release": "2.0",
        "passed": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
    }


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
    if not isinstance(contract, dict) or not isinstance(evidence, dict):
        raise ValueError("Provider 选型契约与证据的根节点必须是对象")
    result = summarize(verify(contract, evidence, arguments.evidence_root))
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    print(serialized, end="")
    if arguments.output is not None:
        arguments.output.write_text(serialized, encoding="utf-8")
    return 0 if result["passed"] or arguments.allow_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
