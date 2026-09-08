"""Run the deterministic OPS-08W workflow validation."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.asgi_acceptance_ops08w import main  # noqa: E402

if __name__ == "__main__":
    asyncio.run(main())
    print("OPS-08W validation: PASS")
