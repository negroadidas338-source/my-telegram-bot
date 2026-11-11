"""Tests for DashboardAdapter – auth and backup behaviour."""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

import unittest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dashboard_adapter import ConsoleLogBuffer, DashboardAdapter


def _make_auth(owner_id: int):
    """Fake auth that reads a test header instead of real Telegram session."""

    def auth_user_id(request):
        raw = request.headers.get("X-Test-User-Id")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    return auth_user_id


class TestDashboardAccess(unittest.IsolatedAsyncioTestCase):
    async def test_status_access_control(self):
        """GET /api/panel/status rejects missing/foreign identity and accepts the owner."""
        with tempfile.TemporaryDirectory() as tmp:
            mem = Path(tmp) / "memory.json"
            mem.write_text(json.dumps({"chats": {}, "users": {}}), encoding="utf-8")

            async def status_provider(user_id):
                return {"ok": True, "bot": {"state": "ok", "label": "running"}, "uptime": "0s"}

            async def health_provider():
                return {"overall": "ok", "checks": []}

            adapter = DashboardAdapter(
                project_root=tmp,
                owner_id=42,
                auth_user_id=_make_auth(42),
                status_provider=status_provider,
                health_provider=health_provider,
                log_buffer=ConsoleLogBuffer(),
                memory_file=str(mem),
                character_data_dir=tmp,
            )

            app = web.Application()
            adapter.register_routes(app)

            async with TestClient(TestServer(app)) as client:
                # No identity
                resp = await client.get("/api/panel/status")
                self.assertIn(resp.status, (401, 403))

                # Wrong owner
                resp = await client.get(
                    "/api/panel/status",
                    headers={"X-Test-User-Id": "999"},
                )
                self.assertIn(resp.status, (401, 403))

                # Correct owner
                resp = await client.get(
                    "/api/panel/status",
                    headers={"X-Test-User-Id": "42"},
                )
                self.assertEqual(resp.status, 200)
                body = await resp.json()
                self.assertTrue(body.get("ok") or "bot" in body)


class TestDashboardBackup(unittest.IsolatedAsyncioTestCase):
    async def test_backup_contains_memory_not_env(self):
        """POST /api/panel/backup produces a zip with memory.json and never includes .env or secret strings."""
        secret_value = "SUPER_SECRET_TOKEN_FOR_TEST_ONLY_XYZ"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mem = root / "memory.json"
            mem.write_text(
                json.dumps({"chats": {"1": []}, "users": {"1": {}}}),
                encoding="utf-8",
            )

            # Create a .env that must never appear in the archive
            env_file = root / ".env"
            env_file.write_text(f"BOT_TOKEN={secret_value}\nGROQ_API_KEY={secret_value}\n", encoding="utf-8")

            # Minimal stubs so backup has something to pack
            for name in (
                "bot.py",
                "activity_adapter.py",
                "character_adapter.py",
                "persona_adapter.py",
                "personality.py",
                "relationship_adapter.py",
                "dashboard_adapter.py",
                "diagnostics.py",
                "health_check.py",
                "requirements.txt",
            ):
                (root / name).write_text("# stub\n", encoding="utf-8")

            async def status_provider(user_id):
                return {"ok": True}

            async def health_provider():
                return {"overall": "ok", "checks": []}

            adapter = DashboardAdapter(
                project_root=str(root),
                owner_id=42,
                auth_user_id=_make_auth(42),
                status_provider=status_provider,
                health_provider=health_provider,
                log_buffer=ConsoleLogBuffer(),
                memory_file=str(mem),
                character_data_dir=str(root),
            )

            app = web.Application()
            adapter.register_routes(app)

            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/panel/backup",
                    headers={"X-Test-User-Id": "42"},
                )
                self.assertEqual(resp.status, 200)
                body = await resp.json()
                self.assertIn("name", body)
                self.assertGreater(body.get("files", 0), 0)

                backup_name = body["name"]
                backup_path = root / "backups" / backup_name
                self.assertTrue(backup_path.is_file())

                with zipfile.ZipFile(backup_path, "r") as zf:
                    names = zf.namelist()
                    self.assertIn("memory.json", names)
                    self.assertNotIn(".env", names)
                    self.assertFalse(any(n.endswith(".env") or n.startswith(".env") for n in names))

                    # Ensure secret string itself is not present in any file content
                    for info in zf.infolist():
                        data = zf.read(info.filename)
                        self.assertNotIn(secret_value.encode("utf-8"), data)


if __name__ == "__main__":
    unittest.main()
