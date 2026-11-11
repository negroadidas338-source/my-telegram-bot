#!/usr/bin/env python3
"""Test runner for the project. Standard library only."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _write_summary(root: Path, result: unittest.TestResult) -> None:
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    target = reports / "test_results.json"

    total = result.testsRun
    failed = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)
    passed = total - failed - errors - skipped

    summary = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "result": "passed" if (failed == 0 and errors == 0) else "failed",
    }

    # Atomic write: temp file then replace
    fd, tmp_name = tempfile.mkstemp(dir=str(reports), suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Run project tests")
    parser.add_argument(
        "--android",
        action="store_true",
        help="Run only diagnostics tests (Android-friendly)",
    )
    args = parser.parse_args()

    root = _project_root()
    # Make project root importable
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    tests_dir = Path(__file__).resolve().parent
    if args.android:
        suite.addTests(loader.discover(str(tests_dir), pattern="test_diagnostics.py"))
    else:
        suite.addTests(loader.discover(str(tests_dir), pattern="test_*.py"))

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)

    try:
        _write_summary(root, result)
    except Exception as exc:
        print(f"Warning: could not write test summary: {exc}", file=sys.stderr)

    if result.wasSuccessful():
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
