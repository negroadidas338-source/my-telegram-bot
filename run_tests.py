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
        "result": "passed" if failed == 0 and errors == 0 else "failed",
    }

    fd, tmp_name = tempfile.mkstemp(dir=str(reports), suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(summary, file, ensure_ascii=False, indent=2)
            file.write("\n")
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
    tests_dir = Path(__file__).resolve().parent

    # Put tests/ first so a same-named file in the project root cannot shadow it.
    tests_path = str(tests_dir)
    if tests_path in sys.path:
        sys.path.remove(tests_path)
    sys.path.insert(0, tests_path)

    if str(root) not in sys.path:
        sys.path.append(str(root))

    loader = unittest.TestLoader()
    pattern = "test_diagnostics.py" if args.android else "test_*.py"
    suite = loader.discover(
        start_dir=tests_path,
        pattern=pattern,
        top_level_dir=tests_path,
    )

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)

    try:
        _write_summary(root, result)
    except Exception as exc:
        print(f"Warning: could not write test summary: {exc}", file=sys.stderr)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
