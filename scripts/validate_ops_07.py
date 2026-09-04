"""Executable isolated validation for OPS-07 domain and security behavior."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    tests = (
        "tests/unit/test_ops07.py",
        "tests/unit/test_ops07_routes.py",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *tests],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit("OPS-07 validator: FAIL")
    required_signals = ("passed",)
    if not all(signal in result.stdout for signal in required_signals):
        raise SystemExit("OPS-07 validator: FAIL (focused tests did not execute)")
    print(result.stdout.strip())
    print(
        "OPS-07 validator: PASS — contributor migration; shared manufacturer evidence; "
        "CSV neutralization; scope/package/work constraints; atomic rollback; restart/FK/integrity"
    )


if __name__ == "__main__":
    main()
