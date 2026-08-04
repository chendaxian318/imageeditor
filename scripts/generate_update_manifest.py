"""Create a publisher-signed update manifest for one release channel."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from light_image_editor.update_security import sign_rs256


def create_manifest(
    installer: Path,
    *,
    version: str,
    channel: str,
    url: str,
    thumbprint: str,
    private_key: dict[str, object],
) -> dict[str, object]:
    payload = {
        "channel": channel,
        "version": version,
        "published_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "minimum_version": "0.7.0",
        "release_notes_url": f"https://github.com/chendaxian318/imageeditor/releases/tag/v{version}",
        "assets": {
            "windows_x64": {
                "url": url,
                "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
                "size": installer.stat().st_size,
                "authenticode_thumbprints": [thumbprint.replace(" ", "").upper()],
            }
        },
    }
    modulus = int(str(private_key["modulus_hex"]), 16)
    private_exponent = int(str(private_key["private_exponent_hex"]), 16)
    return {
        "schema_version": 1,
        "key_id": private_key["key_id"],
        "payload": payload,
        "signature": sign_rs256(payload, modulus, private_exponent),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", choices=("stable", "beta"), required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--thumbprint", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    key = json.loads(arguments.private_key.read_text(encoding="utf-8"))
    manifest = create_manifest(
        arguments.installer,
        version=arguments.version,
        channel=arguments.channel,
        url=arguments.url,
        thumbprint=arguments.thumbprint,
        private_key=key,
    )
    arguments.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
