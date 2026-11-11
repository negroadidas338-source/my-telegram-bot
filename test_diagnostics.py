"""Tests for diagnostics.py – aggregates only, no secrets or message text."""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from diagnostics import inspect_memory, load_test_summary, run_health_check


class TestInspectMemory(unittest.TestCase):
    def test_returns_only_aggregates(self):
        """Memory summary must contain only state/chats/users/messages/detail – no text, names or IDs."""
        data = {
            "chats": {
                "12345": [
                    {"id": 1, "text": "private message content", "from": "Alice"},
                    {"id": 2, "text": "another secret", "from": "Bob"},
                ]
            },
            "users": {
                "999": {"name": "Owner", "role": "admin"},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            summary = inspect_memory(str(path))

        self.assertIsInstance(summary, dict)
        allowed = {"state", "chats", "users", "messages", "detail"}
        self.assertTrue(set(summary.keys()).issubset(allowed))

        dumped = json.dumps(summary)
        self.assertNotIn("private message content", dumped)
        self.assertNotIn("another secret", dumped)
        self.assertNotIn("Alice", dumped)
        self.assertNotIn("Bob", dumped)
        self.assertNotIn("Owner", dumped)
        self.assertNotIn("12345", dumped)
        self.assertNotIn("999", dumped)

        self.assertEqual(summary["chats"], 1)
        self.assertEqual(summary["users"], 1)
        self.assertEqual(summary["messages"], 2)
        self.assertEqual(summary["state"], "ok")

    def test_missing_and_corrupt_memory(self):
        """Absent or invalid memory files are recognised without raising."""
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no_such_file.json"
            summary = inspect_memory(str(missing))
            self.assertIsInstance(summary, dict)
            self.assertEqual(summary["state"], "warning")
            self.assertEqual(summary["chats"], 0)
            self.assertEqual(summary["users"], 0)
            self.assertEqual(summary["messages"], 0)

            corrupt = Path(tmp) / "broken.json"
            corrupt.write_text("{not valid json", encoding="utf-8")
            summary2 = inspect_memory(str(corrupt))
            self.assertIsInstance(summary2, dict)
            self.assertEqual(summary2["state"], "error")
            self.assertEqual(summary2["chats"], 0)
            self.assertEqual(summary2["users"], 0)
            self.assertEqual(summary2["messages"], 0)


class TestLoadTestSummary(unittest.TestCase):
    def test_validates_counters_and_hides_extra(self):
        """load_test_summary returns only the documented counters; invalid files yield None."""
        good = {
            "total": 10,
            "passed": 8,
            "failed": 1,
            "errors": 0,
            "skipped": 1,
            "timestamp": "2026-01-01T00:00:00Z",
            "result": "failed",
            "secret_extra": "should_not_appear",
            "memory_dump": {"chats": 99},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.json"
            path.write_text(json.dumps(good), encoding="utf-8")
            summary = load_test_summary(str(path))

            self.assertIsNotNone(summary)
            expected_keys = {"total", "passed", "failed", "errors", "skipped", "timestamp", "result"}
            self.assertEqual(set(summary.keys()), expected_keys)
            self.assertEqual(summary["total"], 10)
            self.assertEqual(summary["passed"], 8)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["errors"], 0)
            self.assertEqual(summary["skipped"], 1)
            self.assertNotIn("secret_extra", summary)
            self.assertNotIn("memory_dump", summary)

            self.assertIsNone(load_test_summary(str(Path(tmp) / "absent.json")))
            bad = Path(tmp) / "bad.json"
            bad.write_text("not json", encoding="utf-8")
            self.assertIsNone(load_test_summary(str(bad)))


class TestAndroidHealthCheck(unittest.TestCase):
    def test_missing_module_gives_skip_warning(self):
        """On Android-style environment a missing incompatible dependency must be SKIP/warning, not hard failure."""
        real_import = importlib.import_module

        def fake_import(name, *args, **kwargs):
            if name == "aiogram":
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            mem = Path(tmp) / "memory.json"
            mem.write_text('{"chats": {}, "users": {}}', encoding="utf-8")
            # Create minimal required project files so file-check does not fail hard
            for name in (
                "bot.py",
                "activity_adapter.py",
                "character_adapter.py",
                "persona_adapter.py",
                "relationship_adapter.py",
                "requirements.txt",
            ):
                (Path(tmp) / name).write_text("# stub\n", encoding="utf-8")

            with mock.patch("importlib.import_module", side_effect=fake_import):
                result = run_health_check(
                    project_root=tmp,
                    memory_file=str(mem),
                    bot_configured=False,
                    groq_configured=False,
                    telegram_state="unknown",
                    groq_state="unknown",
                    env_file=None,
                    environ={"ANDROID_ROOT": "/system", "PREFIX": "/data/data/com.termux/files/usr"},
                )

        self.assertIsInstance(result, dict)
        self.assertIn("overall", result)
        self.assertIn("checks", result)
        self.assertIn("memory", result)

        checks = result["checks"]
        self.assertIsInstance(checks, list)

        dep_check = next((c for c in checks if c.get("name") == "Зависимости"), None)
        self.assertIsNotNone(dep_check)
        self.assertEqual(dep_check["state"], "warning")
        self.assertIn("SKIP", dep_check["detail"])
        self.assertIn("aiogram", dep_check["detail"])

        # The missing Android dependency itself must not force a hard failure of the whole check
        # (other checks may still be error, e.g. missing token)
        self.assertNotEqual(dep_check["state"], "error")


if __name__ == "__main__":
    unittest.main()
