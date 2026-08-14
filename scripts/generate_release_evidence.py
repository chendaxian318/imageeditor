"""Generate a source-to-artifact provenance record for one Windows build."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from light_image_editor.version import __version__


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def create_evidence(root: Path, release: Path, tests_executed: bool) -> dict[str, Any]:
    critical = [
        release / "LightImageEditor" / "CSImage.exe",
        release / f"CSImage-Portable-x64-{__version__}.exe",
        release / f"CSImage-Setup-x64-{__version__}.exe",
        release / "SBOM.cdx.json",
    ]
    artifacts = []
    for path in critical:
        if not path.is_file():
            raise FileNotFoundError(path)
        artifacts.append(
            {
                "path": path.relative_to(release).as_posix(),
                "size": path.stat().st_size,
                "sha256": _hash(path),
            }
        )
    commit = _git(root, "rev-parse", "HEAD")
    tags = [item for item in _git(root, "tag", "--points-at", "HEAD").splitlines() if item]
    annotated_tags = [
        tag for tag in tags if _git(root, "cat-file", "-t", f"refs/tags/{tag}") == "tag"
    ]
    return {
        "schema_version": 1,
        "generated_utc": datetime.now(UTC).isoformat(),
        "application_version": __version__,
        "source": {
            "repository": "https://github.com/chendaxian318/imageeditor",
            "commit": commit,
            "tags": tags,
            "annotated_tags": annotated_tags,
            "dirty": bool(_git(root, "status", "--porcelain")),
        },
        "build": {
            "tests_executed_in_this_build": tests_executed,
            "python": "3.12.10",
            "architecture": "x64",
            "dependency_lock": _hash(root / "requirements-dev.lock"),
        },
        "artifacts": artifacts,
        "diagnostics": {
            "runtime_logs": "%LOCALAPPDATA%/LightImageEditor/logs",
            "crash_records": "%LOCALAPPDATA%/LightImageEditor/crashes",
            "source_symbol_commit": commit,
            "pyinstaller_warning": "diagnostics/pyinstaller-warnings.txt",
        },
        "security_evidence": {
            "local_scan": "LOCAL_SECURITY_SCAN.json",
            "signature_policy": "docs/release/UPDATE_SECURITY.md",
            "multi_engine_review": "external evidence required",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tests-executed", choices=("true", "false"), required=True)
    arguments = parser.parse_args()
    evidence = create_evidence(
        arguments.root.resolve(),
        arguments.release.resolve(),
        arguments.tests_executed == "true",
    )
    arguments.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
