"""Validate v2 first-party, third-party, and installer licence evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONTRACT_FIELDS = {"schema_version", "release", "required_grants"}
GRANT_SPEC_FIELDS = {
    "id",
    "required_scopes",
    "commercial_use_required",
    "redistribution_required",
    "required_files",
}
EVIDENCE_FIELDS = {"schema_version", "release", "grants"}
GRANT_FIELDS = {
    "id",
    "rights_holder",
    "license_identifier",
    "granted_scopes",
    "commercial_use",
    "redistribution",
    "human_approved",
    "approved_by",
    "approved_at_utc",
    "valid_from_utc",
    "expires_at_utc",
    "files",
}
DESCRIPTOR_FIELDS = {"path", "sha256"}
EXPECTED_GRANTS = {
    "first_party_rights": {
        "scopes": {
            "modify",
            "source_redistribution",
            "binary_redistribution",
            "commercial_distribution",
        },
        "files": {"authorization_document", "chain_of_title_review"},
    },
    "third_party_dependencies": {
        "scopes": {
            "binary_redistribution",
            "notice_distribution",
            "commercial_distribution",
        },
        "files": {"dependency_inventory", "legal_review"},
    },
    "inno_setup_commercial": {
        "scopes": {"commercial_installer_build", "redistribute_compiled_installer"},
        "files": {"license_document", "legal_review"},
    },
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


def _text_set(value: object) -> set[str] | None:
    if not isinstance(value, list) or any(not _is_nonempty_text(item) for item in value):
        return None
    values = [str(item) for item in value]
    if len(values) != len(set(values)):
        return None
    return set(values)


def _utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _validate_contract(
    contract: dict[str, Any], blockers: list[str]
) -> dict[str, dict[str, set[str]]]:
    if set(contract) != CONTRACT_FIELDS:
        blockers.append("V2-LICENSE-CONTRACT: 顶层字段不完整或包含未知字段")
    if contract.get("schema_version") != 1 or contract.get("release") != "2.0":
        blockers.append("V2-LICENSE-CONTRACT: 版本必须固定为 schema 1 / release 2.0")
    raw_specs = contract.get("required_grants")
    observed: dict[str, dict[str, set[str]]] = {}
    if not isinstance(raw_specs, list):
        blockers.append("V2-LICENSE-CONTRACT: required_grants 必须是数组")
        return observed
    for index, raw_spec in enumerate(raw_specs):
        prefix = f"V2-LICENSE-CONTRACT[{index}]"
        if not isinstance(raw_spec, dict) or set(raw_spec) != GRANT_SPEC_FIELDS:
            blockers.append(f"{prefix}: 授权字段无效")
            continue
        grant_id = raw_spec.get("id")
        if not isinstance(grant_id, str) or grant_id not in EXPECTED_GRANTS:
            blockers.append(f"{prefix}: 未知授权 {grant_id}")
            continue
        if grant_id in observed:
            blockers.append(f"V2-LICENSE-CONTRACT: 授权重复：{grant_id}")
            continue
        scopes = _text_set(raw_spec.get("required_scopes"))
        files = _text_set(raw_spec.get("required_files"))
        expected = EXPECTED_GRANTS[grant_id]
        if scopes != expected["scopes"] or files != expected["files"]:
            blockers.append(f"{prefix}: {grant_id} 范围或文件集合与冻结契约不一致")
            continue
        if (
            raw_spec.get("commercial_use_required") is not True
            or raw_spec.get("redistribution_required") is not True
        ):
            blockers.append(f"{prefix}: 商业使用与再分发要求必须为 true")
            continue
        observed[grant_id] = {"scopes": scopes, "files": files}
    if set(observed) != set(EXPECTED_GRANTS):
        blockers.append("V2-LICENSE-CONTRACT: 必须完整定义三类许可授权")
    return observed


def _validate_grant(
    grant: dict[str, Any],
    index: int,
    spec: dict[str, set[str]],
    evidence_root: Path,
    evaluation_time: datetime,
    blockers: list[str],
) -> None:
    grant_id = grant.get("id")
    prefix = f"V2-LICENSE-GRANT[{index}:{grant_id or '未知'}]"
    if set(grant) != GRANT_FIELDS:
        blockers.append(f"{prefix}: 字段不完整或包含未知字段")
    for field in ("rights_holder", "license_identifier", "approved_by"):
        if not _is_nonempty_text(grant.get(field)):
            blockers.append(f"{prefix}: {field} 不能为空")
    scopes = _text_set(grant.get("granted_scopes"))
    if scopes is None:
        blockers.append(f"{prefix}: granted_scopes 必须是无重复非空字符串数组")
    else:
        missing = spec["scopes"] - scopes
        if missing:
            blockers.append(f"{prefix}: 缺少授权范围 {', '.join(sorted(missing))}")
    if grant.get("commercial_use") is not True:
        blockers.append(f"{prefix}: 未授权商业使用")
    if grant.get("redistribution") is not True:
        blockers.append(f"{prefix}: 未授权再分发")
    if grant.get("human_approved") is not True:
        blockers.append(f"{prefix}: 必须由人工明确批准")

    approved_at = _utc_datetime(grant.get("approved_at_utc"))
    valid_from = _utc_datetime(grant.get("valid_from_utc"))
    raw_expires = grant.get("expires_at_utc")
    expires_at = None if raw_expires is None else _utc_datetime(raw_expires)
    if approved_at is None:
        blockers.append(f"{prefix}: approved_at_utc 必须是带时区时间")
    elif approved_at > evaluation_time:
        blockers.append(f"{prefix}: 批准时间晚于校验时间")
    if valid_from is None:
        blockers.append(f"{prefix}: valid_from_utc 必须是带时区时间")
    elif valid_from > evaluation_time:
        blockers.append(f"{prefix}: 授权尚未生效")
    if raw_expires is not None:
        if expires_at is None:
            blockers.append(f"{prefix}: expires_at_utc 必须为空或带时区时间")
        elif expires_at <= evaluation_time:
            blockers.append(f"{prefix}: 授权已经过期")
        elif valid_from is not None and expires_at <= valid_from:
            blockers.append(f"{prefix}: 授权失效时间不晚于生效时间")

    files = grant.get("files")
    if not isinstance(files, dict) or set(files) != spec["files"]:
        blockers.append(f"{prefix}: 授权证据文件字段不完整")
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
    *,
    evaluation_time: datetime | None = None,
) -> list[str]:
    """Return every licence blocker without inferring rights from repository access."""

    blockers: list[str] = []
    specs = _validate_contract(contract, blockers)
    if set(evidence) != EVIDENCE_FIELDS:
        blockers.append("V2-LICENSE-EVIDENCE: 顶层字段不完整或包含未知字段")
    if evidence.get("schema_version") != 1 or evidence.get("release") != "2.0":
        blockers.append("V2-LICENSE-EVIDENCE: 版本必须固定为 schema 1 / release 2.0")
    raw_grants = evidence.get("grants")
    if not isinstance(raw_grants, list):
        blockers.append("V2-LICENSE-EVIDENCE: grants 必须是数组")
        raw_grants = []
    checked_at = evaluation_time or datetime.now(UTC)
    if checked_at.tzinfo is None:
        raise ValueError("evaluation_time 必须带时区")
    checked_at = checked_at.astimezone(UTC)
    observed: set[str] = set()
    for index, raw_grant in enumerate(raw_grants):
        if not isinstance(raw_grant, dict):
            blockers.append(f"V2-LICENSE-GRANT[{index}]: 授权必须是对象")
            continue
        grant_id = raw_grant.get("id")
        if not isinstance(grant_id, str) or grant_id not in specs:
            blockers.append(f"V2-LICENSE-GRANT[{index}]: 未知授权 {grant_id}")
            continue
        if grant_id in observed:
            blockers.append(f"V2-LICENSE-EVIDENCE: 授权重复：{grant_id}")
            continue
        observed.add(grant_id)
        _validate_grant(
            raw_grant,
            index,
            specs[grant_id],
            evidence_root,
            checked_at,
            blockers,
        )
    for grant_id in specs:
        if grant_id not in observed:
            blockers.append(f"V2-LICENSE-MISSING: 缺少授权 {grant_id}")
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
        raise ValueError("许可契约与证据根节点必须是对象")
    result = summarize(verify(contract, evidence, arguments.evidence_root))
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    print(serialized, end="")
    if arguments.output is not None:
        arguments.output.write_text(serialized, encoding="utf-8")
    return 0 if result["passed"] or arguments.allow_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
