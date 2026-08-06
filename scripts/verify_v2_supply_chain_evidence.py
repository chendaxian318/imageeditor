"""Validate v2 signed artifact, Defender, and multi-engine review evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeGuard

CONTRACT_FIELDS = {
    "schema_version",
    "release",
    "target_version",
    "previous_stable_version",
    "required_artifacts",
}
ARTIFACT_SPEC_FIELDS = {"role", "path"}
LOCAL_SCAN_FIELDS = {
    "SchemaVersion",
    "GeneratedUtc",
    "ApplicationVersion",
    "AuthenticodePassed",
    "Signatures",
    "DefenderStatus",
    "DefenderSignatureVersion",
    "DefenderScans",
    "MultiEngineReview",
    "PublicReleaseReady",
}
SIGNATURE_FIELDS = {"File", "Status", "Thumbprint", "TimestampSubject", "Sha256", "Size"}
DEFENDER_FIELDS = {"File", "ExitCode", "Passed"}
MULTI_ENGINE_FIELDS = {
    "SchemaVersion",
    "Approved",
    "Provider",
    "ReviewedUtc",
    "Reviewer",
    "Artifacts",
}
MULTI_ARTIFACT_FIELDS = {"File", "Sha256", "Result"}
EXPECTED_ARTIFACTS = {
    "application": "LightImageEditor/ImageEditor.exe",
    "portable": "LightImageEditor-Portable-x64-2.0.0.exe",
    "installer": "LightImageEditor-Setup-x64-2.0.0.exe",
    "rollback": "LightImageEditor-Rollback-x64-1.1.3.exe",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
THUMBPRINT_PATTERN = re.compile(r"^[0-9A-F]{40}$")


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


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _normalized_thumbprint(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace(" ", "").upper()


def _required_artifacts(contract: dict[str, Any], blockers: list[str]) -> dict[str, str]:
    if set(contract) != CONTRACT_FIELDS:
        blockers.append("V2-SUPPLY-CONTRACT: 顶层字段不完整或包含未知字段")
    if contract.get("schema_version") != 1:
        blockers.append("V2-SUPPLY-CONTRACT: schema_version 必须为 1")
    if contract.get("release") != "2.0" or contract.get("target_version") != "2.0.0":
        blockers.append("V2-SUPPLY-CONTRACT: 发布版本必须固定为 2.0 / 2.0.0")
    if contract.get("previous_stable_version") != "1.1.3":
        blockers.append("V2-SUPPLY-CONTRACT: 回滚基线必须固定为 1.1.3")
    raw_specs = contract.get("required_artifacts")
    observed: dict[str, str] = {}
    if not isinstance(raw_specs, list):
        blockers.append("V2-SUPPLY-CONTRACT: required_artifacts 必须是数组")
        return observed
    for index, raw_spec in enumerate(raw_specs):
        prefix = f"V2-SUPPLY-CONTRACT[{index}]"
        if not isinstance(raw_spec, dict) or set(raw_spec) != ARTIFACT_SPEC_FIELDS:
            blockers.append(f"{prefix}: 产物字段无效")
            continue
        role = raw_spec.get("role")
        path = raw_spec.get("path")
        if not isinstance(role, str) or role not in EXPECTED_ARTIFACTS:
            blockers.append(f"{prefix}: 未知产物角色 {role}")
            continue
        if role in observed:
            blockers.append(f"V2-SUPPLY-CONTRACT: 产物角色重复：{role}")
            continue
        if path != EXPECTED_ARTIFACTS[role]:
            blockers.append(f"{prefix}: {role} 路径与冻结契约不一致")
            continue
        observed[role] = path
    if observed != EXPECTED_ARTIFACTS:
        blockers.append("V2-SUPPLY-CONTRACT: 必须完整定义四类冻结产物")
    return observed


def _validate_local_scan(
    local_scan: dict[str, Any],
    artifacts: dict[str, tuple[Path, str]],
    approved_thumbprint: str,
    blockers: list[str],
) -> None:
    if set(local_scan) != LOCAL_SCAN_FIELDS:
        blockers.append("V2-SUPPLY-LOCAL: 本地扫描字段不完整或包含未知字段")
    if local_scan.get("SchemaVersion") != 1:
        blockers.append("V2-SUPPLY-LOCAL: SchemaVersion 必须为 1")
    if local_scan.get("ApplicationVersion") != "2.0.0":
        blockers.append("V2-SUPPLY-LOCAL: ApplicationVersion 必须为 2.0.0")
    if not _is_utc_timestamp(local_scan.get("GeneratedUtc")):
        blockers.append("V2-SUPPLY-LOCAL: GeneratedUtc 必须是 UTC 时间")
    if local_scan.get("AuthenticodePassed") is not True:
        blockers.append("V2-SUPPLY-LOCAL: Authenticode 汇总未通过")
    if local_scan.get("DefenderStatus") != "passed":
        blockers.append("V2-SUPPLY-LOCAL: Defender 扫描未通过")
    if not _is_nonempty_text(local_scan.get("DefenderSignatureVersion")):
        blockers.append("V2-SUPPLY-LOCAL: Defender 病毒库版本不能为空")
    if local_scan.get("MultiEngineReview") != "approved":
        blockers.append("V2-SUPPLY-LOCAL: 多引擎复核汇总未批准")
    if local_scan.get("PublicReleaseReady") is not True:
        blockers.append("V2-SUPPLY-LOCAL: 本地安全报告未达到公开发布条件")

    signatures = local_scan.get("Signatures")
    observed_signatures: dict[str, dict[str, Any]] = {}
    if not isinstance(signatures, list):
        blockers.append("V2-SUPPLY-LOCAL: Signatures 必须是数组")
        signatures = []
    for index, raw_signature in enumerate(signatures):
        prefix = f"V2-SUPPLY-SIGNATURE[{index}]"
        if not isinstance(raw_signature, dict) or set(raw_signature) != SIGNATURE_FIELDS:
            blockers.append(f"{prefix}: 签名字段无效")
            continue
        relative = raw_signature.get("File")
        if not isinstance(relative, str) or relative not in artifacts:
            blockers.append(f"{prefix}: 未知产物 {relative}")
            continue
        if relative in observed_signatures:
            blockers.append(f"V2-SUPPLY-LOCAL: 签名产物重复：{relative}")
            continue
        observed_signatures[relative] = raw_signature
        path, digest = artifacts[relative]
        if raw_signature.get("Status") != "Valid":
            blockers.append(f"{prefix}: Authenticode 状态不是 Valid")
        if _normalized_thumbprint(raw_signature.get("Thumbprint")) != approved_thumbprint:
            blockers.append(f"{prefix}: 发布者证书指纹不匹配")
        if not _is_nonempty_text(raw_signature.get("TimestampSubject")):
            blockers.append(f"{prefix}: 缺少可信时间戳证书")
        if raw_signature.get("Sha256") != digest:
            blockers.append(f"{prefix}: SHA-256 与最终产物不匹配")
        size = raw_signature.get("Size")
        if not _is_int(size) or size != path.stat().st_size:
            blockers.append(f"{prefix}: 文件大小与最终产物不匹配")
    for relative in artifacts:
        if relative not in observed_signatures:
            blockers.append(f"V2-SUPPLY-LOCAL: 缺少签名结果 {relative}")

    defender_scans = local_scan.get("DefenderScans")
    observed_scans: set[str] = set()
    if not isinstance(defender_scans, list):
        blockers.append("V2-SUPPLY-LOCAL: DefenderScans 必须是数组")
        defender_scans = []
    for index, raw_scan in enumerate(defender_scans):
        prefix = f"V2-SUPPLY-DEFENDER[{index}]"
        if not isinstance(raw_scan, dict) or set(raw_scan) != DEFENDER_FIELDS:
            blockers.append(f"{prefix}: Defender 结果字段无效")
            continue
        relative = raw_scan.get("File")
        if not isinstance(relative, str) or relative not in artifacts:
            blockers.append(f"{prefix}: 未知产物 {relative}")
            continue
        if relative in observed_scans:
            blockers.append(f"V2-SUPPLY-LOCAL: Defender 产物重复：{relative}")
            continue
        observed_scans.add(relative)
        if raw_scan.get("ExitCode") != 0 or raw_scan.get("Passed") is not True:
            blockers.append(f"{prefix}: Defender 扫描未通过")
    for relative in artifacts:
        if relative not in observed_scans:
            blockers.append(f"V2-SUPPLY-LOCAL: 缺少 Defender 结果 {relative}")


def _validate_multi_engine(
    multi_engine: dict[str, Any],
    artifacts: dict[str, tuple[Path, str]],
    blockers: list[str],
) -> None:
    if set(multi_engine) != MULTI_ENGINE_FIELDS:
        blockers.append("V2-SUPPLY-MULTI: 多引擎证据字段不完整或包含未知字段")
    if multi_engine.get("SchemaVersion") != 1:
        blockers.append("V2-SUPPLY-MULTI: SchemaVersion 必须为 1")
    if multi_engine.get("Approved") is not True:
        blockers.append("V2-SUPPLY-MULTI: 必须由人工明确批准多引擎复核")
    for field in ("Provider", "Reviewer"):
        if not _is_nonempty_text(multi_engine.get(field)):
            blockers.append(f"V2-SUPPLY-MULTI: {field} 不能为空")
    if not _is_utc_timestamp(multi_engine.get("ReviewedUtc")):
        blockers.append("V2-SUPPLY-MULTI: ReviewedUtc 必须是 UTC 时间")
    raw_artifacts = multi_engine.get("Artifacts")
    observed: set[str] = set()
    if not isinstance(raw_artifacts, list):
        blockers.append("V2-SUPPLY-MULTI: Artifacts 必须是数组")
        raw_artifacts = []
    for index, raw_artifact in enumerate(raw_artifacts):
        prefix = f"V2-SUPPLY-MULTI[{index}]"
        if not isinstance(raw_artifact, dict) or set(raw_artifact) != MULTI_ARTIFACT_FIELDS:
            blockers.append(f"{prefix}: 产物字段无效")
            continue
        relative = raw_artifact.get("File")
        if not isinstance(relative, str) or relative not in artifacts:
            blockers.append(f"{prefix}: 未知产物 {relative}")
            continue
        if relative in observed:
            blockers.append(f"V2-SUPPLY-MULTI: 产物重复：{relative}")
            continue
        observed.add(relative)
        if raw_artifact.get("Result") != "passed":
            blockers.append(f"{prefix}: 多引擎复核未通过")
        if raw_artifact.get("Sha256") != artifacts[relative][1]:
            blockers.append(f"{prefix}: SHA-256 与最终产物不匹配")
    for relative in artifacts:
        if relative not in observed:
            blockers.append(f"V2-SUPPLY-MULTI: 缺少复核结果 {relative}")


def verify(
    contract: dict[str, Any],
    local_scan: dict[str, Any],
    multi_engine: dict[str, Any],
    artifact_root: Path,
    approved_thumbprint: str,
) -> list[str]:
    """Return every v2 supply-chain blocker without trusting summary booleans."""

    blockers: list[str] = []
    required = _required_artifacts(contract, blockers)
    normalized_thumbprint = _normalized_thumbprint(approved_thumbprint)
    if THUMBPRINT_PATTERN.fullmatch(normalized_thumbprint) is None:
        blockers.append("V2-SUPPLY-APPROVAL: 必须提供四十位批准发布者证书指纹")

    artifacts: dict[str, tuple[Path, str]] = {}
    for relative in required.values():
        path = _safe_path(artifact_root, relative)
        if path is None:
            blockers.append(f"V2-SUPPLY-ARTIFACT: 路径越出发布目录：{relative}")
        elif not path.is_file():
            blockers.append(f"V2-SUPPLY-ARTIFACT: 缺少最终产物：{relative}")
        else:
            artifacts[relative] = (path, _sha256(path))

    _validate_local_scan(local_scan, artifacts, normalized_thumbprint, blockers)
    _validate_multi_engine(multi_engine, artifacts, blockers)
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
    parser.add_argument("--local-scan", type=Path, required=True)
    parser.add_argument("--multi-engine", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--approved-thumbprint", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-blocked", action="store_true")
    arguments = parser.parse_args()
    contract = json.loads(arguments.contract.read_text(encoding="utf-8"))
    local_scan = json.loads(arguments.local_scan.read_text(encoding="utf-8"))
    multi_engine = json.loads(arguments.multi_engine.read_text(encoding="utf-8"))
    if not all(isinstance(item, dict) for item in (contract, local_scan, multi_engine)):
        raise ValueError("供应链契约与证据根节点必须是对象")
    result = summarize(
        verify(
            contract,
            local_scan,
            multi_engine,
            arguments.artifact_root,
            arguments.approved_thumbprint,
        )
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    print(serialized, end="")
    if arguments.output is not None:
        arguments.output.write_text(serialized, encoding="utf-8")
    return 0 if result["passed"] or arguments.allow_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
