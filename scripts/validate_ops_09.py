"""Validate frozen contract provenance and the executable OPS-09 workflow."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.proposal_exchange_contract import (  # noqa: E402
    CONTRACT_ROOT,
    canonical_sha256,
    strict_json_loads,
)
from scripts.asgi_acceptance_ops09 import main as acceptance_main  # noqa: E402

EXPECTED_RAW_HASHES = {
    "proposal-package-v1.schema.json": (
        "3416365b51abedf3dcc7f17996ecb08467997b660679ae890c5624761d472462"
    ),
    "proposal-generation-manifest-v1.schema.json": (
        "da5c24e75371498109ba28d81d8840753a30c28bbfb9554fc2a5f288649ef408"
    ),
    "examples/proposal-package-v1.example.json": (
        "8943882932419d3c8ffc03e1b1552433d5c6b807d5fa69dea039275ac245c3fc"
    ),
    "examples/proposal-generation-manifest-v1.example.json": (
        "ef30b682e058039b8eab9d8e98c564785b657bc5dc1b518635300e8868304c80"
    ),
    "CONTRACTIQ_EXCHANGE_CONTRACT_V1.md": (
        "864e578a33e8fd17c6f753bb92d6333c9d83ae82728d10746221d74dea27ea30"
    ),
    "reference/test_exchange_contract_v1.py": (
        "5e16ba335763de0cd03a76546467a597310aaa3180d217ec4568053257bdbec0"
    ),
}


def validate_frozen_files() -> None:
    """Fail if any authoritative frozen V1 byte or canonical example changes."""
    for relative, expected in EXPECTED_RAW_HASHES.items():
        path = CONTRACT_ROOT / Path(relative)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise AssertionError(f"Frozen contract hash mismatch: {relative}")
    package = strict_json_loads(
        (CONTRACT_ROOT / "examples" / "proposal-package-v1.example.json").read_bytes()
    )
    if canonical_sha256(package) != (
        "f087af73dc3306d0ca174ffaa6df464f5c6102a86929926a038797e9b71b310b"
    ):
        raise AssertionError("Canonical example package hash mismatch")


def main() -> None:
    """Run the hash oracle and socketless rendered workflow."""
    validate_frozen_files()
    asyncio.run(acceptance_main())
    print("OPS-09 validation: PASS")


if __name__ == "__main__":
    main()
