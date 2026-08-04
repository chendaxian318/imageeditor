"""Publisher signature verification for canonical update manifests."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def trusted_keys_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "resources" / "update" / "trusted_keys.json"


def canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class RsaPublicKey:
    key_id: str
    modulus: int
    exponent: int

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> RsaPublicKey:
        return cls(
            key_id=str(payload["key_id"]),
            modulus=int(str(payload["modulus_hex"]), 16),
            exponent=int(payload.get("exponent", 65537)),
        )


def load_trusted_keys(path: str | Path) -> dict[str, RsaPublicKey]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    keys = [RsaPublicKey.from_json(item) for item in payload.get("keys", [])]
    return {key.key_id: key for key in keys if key.modulus.bit_length() >= 2048}


def verify_rs256(payload: object, signature: str, key: RsaPublicKey) -> bool:
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
    except (ValueError, TypeError):
        return False
    key_size = (key.modulus.bit_length() + 7) // 8
    if len(signature_bytes) != key_size:
        return False
    encoded = pow(int.from_bytes(signature_bytes, "big"), key.exponent, key.modulus).to_bytes(
        key_size, "big"
    )
    digest_info = SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(canonical_json(payload)).digest()
    padding_length = key_size - len(digest_info) - 3
    if padding_length < 8:
        return False
    expected = b"\x00\x01" + b"\xff" * padding_length + b"\x00" + digest_info
    return hashlib.sha256(encoded).digest() == hashlib.sha256(expected).digest()


def sign_rs256(payload: object, modulus: int, private_exponent: int) -> str:
    key_size = (modulus.bit_length() + 7) // 8
    digest_info = SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(canonical_json(payload)).digest()
    padding_length = key_size - len(digest_info) - 3
    if padding_length < 8:
        raise ValueError("RSA key is too small for RS256")
    encoded = b"\x00\x01" + b"\xff" * padding_length + b"\x00" + digest_info
    signature = pow(int.from_bytes(encoded, "big"), private_exponent, modulus).to_bytes(
        key_size, "big"
    )
    return base64.b64encode(signature).decode("ascii")
