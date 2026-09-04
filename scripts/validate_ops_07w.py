"""Executable isolated OPS-07W domain, migration, and security validation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/unit/test_ops07w.py",
            "tests/unit/test_ops07.py",
            "tests/unit/test_ops07_routes.py",
            "tests/unit/test_bid_control_center.py",
            "tests/unit/test_ops06_ui.py",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit("OPS-07W validator: FAIL")
    if "passed" not in result.stdout:
        raise SystemExit("OPS-07W validator: FAIL (focused behavior did not execute)")
    print(result.stdout.strip())
    print(
        "OPS-07W validator: PASS — deterministic classification; append-only evidence; "
        "atomic gate-approval audit; truthful navigator states; exact manufacturer proof; "
        "selected contextual links; strict multipart form partitioning; governance guidance; "
        "durable idempotent source-first PRG; executable 27-action "
        "trace; bidirectional evidence; "
        "contextual work atomicity; CSV security; migration restart/FK/integrity"
    )


if __name__ == "__main__":
    main()
