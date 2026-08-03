"""Reject missing, mismatched, or unexpected packages in the release environment."""

from __future__ import annotations

import argparse
import importlib.metadata
import re
from pathlib import Path

_PIN = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s;]+)$")
_INCLUDE = re.compile(r"^(?:-r|--requirement)\s+(?P<path>.+)$")
_ALLOWED_BOOTSTRAP_PACKAGES = {"pip"}


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(path: Path, visited: set[Path] | None = None) -> dict[str, str]:
    """Read exact pins, following nested ``-r`` includes."""

    resolved = path.resolve()
    seen = set() if visited is None else visited
    if resolved in seen:
        return {}
    seen.add(resolved)

    requirements: dict[str, str] = {}
    for line_number, raw_line in enumerate(resolved.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        include_match = _INCLUDE.fullmatch(line)
        if include_match:
            nested_path = resolved.parent / include_match.group("path").strip()
            nested = read_lock(nested_path, seen)
            for name, version in nested.items():
                if name in requirements and requirements[name] != version:
                    raise ValueError(f"Conflicting pins for {name} in {resolved}")
                requirements[name] = version
            continue
        pin_match = _PIN.fullmatch(line)
        if pin_match is None:
            raise ValueError(f"Unsupported lock entry at {resolved}:{line_number}: {line!r}")
        name = canonical_name(pin_match.group("name"))
        version = pin_match.group("version")
        if name in requirements and requirements[name] != version:
            raise ValueError(f"Conflicting pins for {name} in {resolved}")
        requirements[name] = version
    return requirements


def installed_packages() -> dict[str, str]:
    return {
        canonical_name(distribution.metadata["Name"]): distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }


def environment_errors(expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for name, expected_version in sorted(expected.items()):
        actual_version = actual.get(name)
        if actual_version is None:
            errors.append(f"missing: {name}=={expected_version}")
        elif actual_version != expected_version:
            errors.append(f"version: {name}=={actual_version} (expected {expected_version})")
    for name in sorted(actual.keys() - expected.keys() - _ALLOWED_BOOTSTRAP_PACKAGES):
        errors.append(f"unexpected: {name}=={actual[name]}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    errors = environment_errors(read_lock(args.lock), installed_packages())
    if errors:
        details = "\n  ".join(errors)
        raise SystemExit(f"Release environment does not match {args.lock}:\n  {details}")
    print(f"Release environment exactly matches {args.lock} (pip excluded).")


if __name__ == "__main__":
    main()
