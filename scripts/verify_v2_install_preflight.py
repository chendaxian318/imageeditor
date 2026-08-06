"""Fail-closed static and artifact preflight for the future v2.0.0 Windows bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any

REQUIRED_TOP_LEVEL = {
    "schema_version",
    "target_version",
    "previous_stable_version",
    "required_files",
    "build_tokens",
    "installer_tokens",
    "lifecycle_tokens",
}
PE_MACHINE_I386 = 0x014C
PE_MACHINE_AMD64 = 0x8664
CHECKSUM_PATTERN = re.compile(r"^([0-9a-fA-F]{64})  ([^\r\n]+)$")


def _safe_file(root: Path, relative: str) -> Path | None:
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


def _text_list(value: object, label: str, blockers: list[str]) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        blockers.append(f"V2-INSTALL-CONTRACT: {label} 必须是非空字符串数组")
        return []
    return list(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pe_machine(path: Path) -> int | None:
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                return None
            pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
            if pe_offset < 64 or pe_offset > path.stat().st_size - 6:
                return None
            stream.seek(pe_offset)
            pe_header = stream.read(6)
    except OSError:
        return None
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        return None
    return struct.unpack_from("<H", pe_header, 4)[0]


def _checksum_entries(path: Path, blockers: list[str]) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        blockers.append(f"V2-INSTALL-CHECKSUM: 无法读取 SHA256SUMS.txt：{error}")
        return {}
    entries: dict[str, str] = {}
    for index, line in enumerate(lines, start=1):
        match = CHECKSUM_PATTERN.fullmatch(line)
        if match is None:
            blockers.append(f"V2-INSTALL-CHECKSUM: 第 {index} 行格式无效")
            continue
        digest, relative = match.groups()
        normalized = relative.replace("\\", "/")
        candidate = Path(normalized)
        if candidate.is_absolute() or ".." in candidate.parts or normalized.startswith("/"):
            blockers.append(f"V2-INSTALL-CHECKSUM: 路径越出发布目录：{relative}")
            continue
        key = normalized.casefold()
        if key in entries:
            blockers.append(f"V2-INSTALL-CHECKSUM: 重复路径：{relative}")
            continue
        entries[key] = digest.casefold()
    return entries


def _verify_artifacts(
    repository_root: Path,
    *,
    target: str,
    previous: str,
    blockers: list[str],
) -> None:
    release_root = repository_root / "release"
    executables = (
        f"LightImageEditor-Portable-x64-{target}.exe",
        f"LightImageEditor-Setup-x64-{target}.exe",
        f"LightImageEditor-Rollback-x64-{previous}.exe",
        "LightImageEditor/ImageEditor.exe",
    )
    checksummed = (
        *executables,
        "SBOM.cdx.json",
    )
    expected = (
        *checksummed,
        "V2_INSTALL_LIFECYCLE.json",
        "SHA256SUMS.txt",
    )
    paths = {relative: release_root / Path(relative) for relative in expected}
    for relative, path in paths.items():
        if not path.is_file():
            blockers.append(f"V2-INSTALL-ARTIFACT: 缺少候选产物：{relative}")

    for relative in executables:
        path = paths[relative]
        if not path.is_file():
            continue
        machine = _pe_machine(path)
        if machine is None:
            blockers.append(f"V2-INSTALL-PE: 候选不是有效 PE 文件：{relative}")
        elif relative in {executables[0], executables[3]} and machine != PE_MACHINE_AMD64:
            blockers.append(f"V2-INSTALL-PE: x64 应用机器类型错误：{relative}")
        elif relative in {executables[1], executables[2]} and machine not in {
            PE_MACHINE_I386,
            PE_MACHINE_AMD64,
        }:
            blockers.append(f"V2-INSTALL-PE: 安装器机器类型不受支持：{relative}")

    sbom_path = paths["SBOM.cdx.json"]
    if sbom_path.is_file():
        try:
            sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            blockers.append(f"V2-INSTALL-SBOM: 无法读取 SBOM：{error}")
        else:
            component = (
                sbom.get("metadata", {}).get("component", {}) if isinstance(sbom, dict) else {}
            )
            if (
                not isinstance(sbom, dict)
                or sbom.get("bomFormat") != "CycloneDX"
                or sbom.get("specVersion") != "1.6"
                or not isinstance(component, dict)
                or component.get("version") != target
                or not isinstance(sbom.get("components"), list)
            ):
                blockers.append("V2-INSTALL-SBOM: 格式或应用版本与目标候选不一致")

    lifecycle_path = paths["V2_INSTALL_LIFECYCLE.json"]
    if lifecycle_path.is_file():
        try:
            lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            blockers.append(f"V2-INSTALL-LIFECYCLE: 无法读取生命周期证据：{error}")
        else:
            required_lifecycle = {
                "schema_version",
                "captured_at_utc",
                "target_version",
                "previous_version",
                "target_installer_sha256",
                "previous_installer_sha256",
                "iteration_count",
                "cycles",
                "passed",
            }
            if not isinstance(lifecycle, dict) or set(lifecycle) != required_lifecycle:
                blockers.append("V2-INSTALL-LIFECYCLE: 顶层结构不完整或包含未知字段")
            else:
                target_installer = paths[executables[1]]
                previous_installer = paths[executables[2]]
                iterations = lifecycle.get("iteration_count")
                cycles = lifecycle.get("cycles")
                identity_valid = (
                    lifecycle.get("schema_version") == 1
                    and lifecycle.get("target_version") == target
                    and lifecycle.get("previous_version") == previous
                    and lifecycle.get("passed") is True
                    and isinstance(iterations, int)
                    and not isinstance(iterations, bool)
                    and iterations >= 3
                    and isinstance(cycles, list)
                    and len(cycles) == iterations
                )
                if not identity_valid:
                    blockers.append("V2-INSTALL-LIFECYCLE: 版本、轮次或总体结果无效")
                hash_pairs = (
                    ("target_installer_sha256", target_installer),
                    ("previous_installer_sha256", previous_installer),
                )
                for field, artifact in hash_pairs:
                    if artifact.is_file() and lifecycle.get(field) != _sha256(artifact):
                        blockers.append(f"V2-INSTALL-LIFECYCLE: 安装包哈希不匹配：{field}")
                expected_cycle_fields = {
                    "Cycle",
                    "PreviousInstall",
                    "UpgradeFromPrevious",
                    "TargetRepairInstall",
                    "PreserveUninstall",
                    "ClearUninstall",
                    "ElapsedSeconds",
                }
                if isinstance(cycles, list):
                    for index, cycle in enumerate(cycles, start=1):
                        status_fields = expected_cycle_fields - {"Cycle", "ElapsedSeconds"}
                        elapsed_seconds = (
                            cycle.get("ElapsedSeconds") if isinstance(cycle, dict) else None
                        )
                        cycle_valid = (
                            isinstance(cycle, dict)
                            and set(cycle) == expected_cycle_fields
                            and cycle.get("Cycle") == index
                            and all(cycle.get(field) is True for field in status_fields)
                            and isinstance(elapsed_seconds, (int, float))
                            and not isinstance(elapsed_seconds, bool)
                            and elapsed_seconds >= 0
                        )
                        if not cycle_valid:
                            blockers.append(f"V2-INSTALL-LIFECYCLE: 第 {index} 轮结果无效")

    checksum_path = paths["SHA256SUMS.txt"]
    if not checksum_path.is_file():
        return
    entries = _checksum_entries(checksum_path, blockers)
    for relative in checksummed:
        path = paths[relative]
        if not path.is_file():
            continue
        recorded = entries.get(relative.casefold())
        if recorded is None:
            blockers.append(f"V2-INSTALL-CHECKSUM: 清单缺少候选：{relative}")
        elif recorded != _sha256(path):
            blockers.append(f"V2-INSTALL-CHECKSUM: 哈希不匹配：{relative}")


def verify(
    contract: dict[str, Any],
    repository_root: Path,
    *,
    require_artifacts: bool = True,
) -> list[str]:
    blockers: list[str] = []
    if set(contract) != REQUIRED_TOP_LEVEL:
        blockers.append("V2-INSTALL-CONTRACT: 顶层字段不完整或包含未知字段")
    if contract.get("schema_version") != 1:
        blockers.append("V2-INSTALL-CONTRACT: schema_version 必须为 1")
    target = contract.get("target_version")
    previous = contract.get("previous_stable_version")
    version_pattern = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
    if not isinstance(target, str) or version_pattern.fullmatch(target) is None:
        blockers.append("V2-INSTALL-CONTRACT: target_version 必须是三段式版本")
        target = "2.0.0"
    if not isinstance(previous, str) or version_pattern.fullmatch(previous) is None:
        blockers.append("V2-INSTALL-CONTRACT: previous_stable_version 必须是三段式版本")
        previous = "unknown"

    required_files = _text_list(contract.get("required_files"), "required_files", blockers)
    file_map: dict[str, Path] = {}
    for relative in required_files:
        resolved = _safe_file(repository_root, relative)
        if resolved is None:
            blockers.append(f"V2-INSTALL-CONTRACT: 路径越出仓库：{relative}")
        elif not resolved.is_file():
            blockers.append(f"V2-INSTALL-FILE: 缺少构建输入：{relative}")
        else:
            file_map[relative] = resolved

    version_file = repository_root / "light_image_editor/version.py"
    source_version = ""
    if version_file.is_file():
        match = re.search(
            r'^__version__\s*=\s*"([^"]+)"',
            version_file.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        source_version = "" if match is None else match.group(1)
    if source_version != target:
        blockers.append(
            f"V2-INSTALL-VERSION: 源码版本为 {source_version or '未知'}，目标版本为 {target}"
        )

    token_specs = (
        ("build.ps1", "build_tokens"),
        ("packaging/windows/LightImageEditor.iss", "installer_tokens"),
        ("scripts/test_install_cycles.ps1", "lifecycle_tokens"),
    )
    for relative, field in token_specs:
        tokens = _text_list(contract.get(field), field, blockers)
        path = file_map.get(relative)
        if path is None:
            continue
        content = path.read_text(encoding="utf-8")
        for token in tokens:
            if token not in content:
                blockers.append(f"V2-INSTALL-CONTRACT: {relative} 缺少契约：{token}")

    release_notes = repository_root / f"docs/release/RELEASE_NOTES-v{target}.md"
    release_page = repository_root / f"docs/release/RELEASE_PAGE-v{target}.md"
    for path in (release_notes, release_page):
        if not path.is_file():
            blockers.append(f"V2-INSTALL-DOC: 缺少目标版本文档：{path.name}")

    build_text = (repository_root / "build.ps1").read_text(encoding="utf-8")
    fixed_v1_documents = re.search(
        r'\$ReleaseDocuments\s*=\s*@\(\s*"docs\\release\\RELEASE_NOTES-v1\.0\.0\.md"',
        build_text,
    )
    if fixed_v1_documents is not None:
        blockers.append("V2-INSTALL-DOC: build.ps1 仍固定引用 v1.0.0 发布文档")

    if require_artifacts:
        _verify_artifacts(
            repository_root,
            target=target,
            previous=previous,
            blockers=blockers,
        )
    return blockers


def summarize(contract: dict[str, Any], blockers: list[str]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "target_version": contract.get("target_version"),
        "passed": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--without-artifacts", action="store_true")
    parser.add_argument("--allow-blocked", action="store_true")
    arguments = parser.parse_args()
    contract = json.loads(arguments.contract.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("安装预检契约根节点必须是对象")
    blockers = verify(
        contract,
        arguments.repository,
        require_artifacts=not arguments.without_artifacts,
    )
    result = summarize(contract, blockers)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    print(serialized, end="")
    if arguments.output is not None:
        arguments.output.write_text(serialized, encoding="utf-8")
    return 0 if result["passed"] or arguments.allow_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
