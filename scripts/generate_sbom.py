"""Generate a deterministic CycloneDX SBOM from the locked runtime dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from light_image_editor.identity import APP_ID, PRODUCT_NAME_EN
from light_image_editor.version import __version__
from scripts.verify_locked_environment import read_lock


def component(name: str, version: str) -> dict[str, object]:
    normalized = name.replace("_", "-").casefold()
    return {
        "type": "library",
        "bom-ref": f"pkg:pypi/{normalized}@{version}",
        "name": name,
        "version": version,
        "purl": f"pkg:pypi/{normalized}@{version}",
    }


def create_sbom(lock_path: Path) -> dict[str, object]:
    dependencies = read_lock(lock_path)
    runtime_names = {"pillow", "numpy"}
    components = [
        component(name, version)
        for name, version in sorted(dependencies.items())
        if name in runtime_names
    ]
    fingerprint = hashlib.sha256(
        json.dumps(components, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    serial_source = f"{APP_ID}:{__version__}:{fingerprint}"
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, serial_source)}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": APP_ID,
                "name": PRODUCT_NAME_EN,
                "version": __version__,
            }
        },
        "components": components,
        "dependencies": [
            {"ref": APP_ID, "dependsOn": [str(item["bom-ref"]) for item in components]}
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(json.dumps(create_sbom(arguments.lock), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
