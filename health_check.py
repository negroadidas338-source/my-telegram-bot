"""Offline health check shared by the CLI, Telegram status, and admin panel."""

from __future__ import annotations

import os
from pathlib import Path

from diagnostics import run_health_check


ROOT = Path(__file__).resolve().parent
MEMORY_FILE = Path(os.environ.get("MEMORY_FILE", ROOT / "memory.json"))


def main() -> int:
    result = run_health_check(ROOT, MEMORY_FILE)
    for check in result["checks"]:
        print(f"{check['state'].upper():7} {check['name']}: {check['detail']}")
    print(f"Итог: {result['overall'].upper()}")
    return 1 if result["overall"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
