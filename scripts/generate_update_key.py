"""Generate an RSA manifest-signing key pair without third-party dependencies."""

from __future__ import annotations

import argparse
import json
import math
import secrets
from pathlib import Path


def probable_prime(bits: int) -> int:
    small_primes = (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47)
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if any(candidate % prime == 0 for prime in small_primes):
            continue
        divisor = candidate - 1
        power = 0
        while divisor % 2 == 0:
            power += 1
            divisor //= 2
        for base in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
            value = pow(base, divisor, candidate)
            if value in (1, candidate - 1):
                continue
            for _ in range(power - 1):
                value = pow(value, 2, candidate)
                if value == candidate - 1:
                    break
            else:
                break
        else:
            return candidate


def generate(bits: int = 2048) -> tuple[int, int, int]:
    exponent = 65537
    while True:
        first = probable_prime(bits // 2)
        second = probable_prime(bits // 2)
        if first == second:
            continue
        phi = (first - 1) * (second - 1)
        if math.gcd(exponent, phi) == 1:
            modulus = first * second
            return modulus, exponent, pow(exponent, -1, phi)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--public", type=Path, required=True)
    arguments = parser.parse_args()
    modulus, exponent, private_exponent = generate()
    arguments.private.parent.mkdir(parents=True, exist_ok=True)
    arguments.public.parent.mkdir(parents=True, exist_ok=True)
    private_payload = {
        "key_id": arguments.key_id,
        "modulus_hex": format(modulus, "x"),
        "exponent": exponent,
        "private_exponent_hex": format(private_exponent, "x"),
    }
    public_payload = {
        "schema_version": 1,
        "keys": [
            {
                "key_id": arguments.key_id,
                "algorithm": "RS256",
                "modulus_hex": format(modulus, "x"),
                "exponent": exponent,
            }
        ],
    }
    arguments.private.write_text(json.dumps(private_payload, indent=2), encoding="utf-8")
    arguments.public.write_text(json.dumps(public_payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
