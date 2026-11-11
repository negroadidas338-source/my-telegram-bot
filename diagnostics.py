"""Small, dependency-free health and saved-test-result helpers for Alice."""

from __future__ import annotations

import importlib
import json
import os
import platform
import sys
from pathlib import Path


def _has_config_value(name: str, env_file: Path, environ: dict[str, str]) -> bool:
    if environ.get(name, "").strip():
        return True
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            candidate = line.strip()
            if not candidate or candidate.startswith("#") or "=" not in candidate:
                continue
            key, value = candidate.split("=", 1)
            if key.strip() == name and value.strip().strip("\"'"):
                return True
    except OSError:
        pass
    return False


def load_test_summary(path: str | Path) -> dict | None:
    """Return validated aggregate test counts, never raw test output."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    keys = ("total", "passed", "failed", "errors", "skipped")
    try:
        counts = {key: max(0, int(data.get(key, 0))) for key in keys}
    except (TypeError, ValueError):
        return None
    counts["timestamp"] = str(data.get("timestamp", ""))[:32]
    counts["result"] = "passed" if counts["failed"] == 0 and counts["errors"] == 0 else "failed"
    return counts


def inspect_memory(path: str | Path) -> dict:
    """Return aggregate memory health only; never return user content or IDs."""
    memory_path = Path(path)
    if not memory_path.exists():
        return {"state": "warning", "chats": 0, "users": 0, "messages": 0, "detail": "Файл памяти ещё не создан"}
    try:
        data = json.loads(memory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "error", "chats": 0, "users": 0, "messages": 0, "detail": "Не удалось прочитать JSON памяти"}
    if not isinstance(data, dict):
        return {"state": "error", "chats": 0, "users": 0, "messages": 0, "detail": "Структура памяти повреждена"}
    chats = data.get("chats", {})
    users = data.get("users", {})
    if not isinstance(chats, dict) or not isinstance(users, dict):
        return {"state": "error", "chats": 0, "users": 0, "messages": 0, "detail": "Структура памяти повреждена"}
    messages = sum(len(history) for history in chats.values() if isinstance(history, list))
    return {"state": "ok", "chats": len(chats), "users": len(users), "messages": messages, "detail": "Память читается"}


def _android_runtime(environ: dict[str, str]) -> bool:
    return sys.platform == "android" or bool(environ.get("ANDROID_ROOT")) or "com.termux" in environ.get("PREFIX", "")


def run_health_check(
    project_root: str | Path,
    memory_file: str | Path,
    *,
    bot_configured: bool | None = None,
    groq_configured: bool | None = None,
    telegram_state: str = "unknown",
    groq_state: str = "unknown",
    env_file: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> dict:
    """Offline diagnostics that are safe to call from /status and the web panel."""
    root = Path(project_root)
    env = os.environ if environ is None else environ
    config_path = Path(env_file) if env_file else root / ".env"
    bot_configured = bot_configured if bot_configured is not None else _has_config_value("BOT_TOKEN", config_path, env)
    groq_configured = groq_configured if groq_configured is not None else _has_config_value("GROQ_API_KEY", config_path, env)
    android = _android_runtime(env)

    checks = [{"name": "Python", "state": "ok", "detail": platform.python_version()}]
    required_files = ("bot.py", "activity_adapter.py", "character_adapter.py", "persona_adapter.py", "relationship_adapter.py", "requirements.txt")
    missing = [name for name in required_files if not (root / name).is_file()]
    checks.append({"name": "Файлы проекта", "state": "error" if missing else "ok", "detail": "Нет: " + ", ".join(missing) if missing else "Основные файлы найдены"})
    telegram_check = "error" if not bot_configured or telegram_state == "error" else "ok" if telegram_state == "online" else "warning"
    telegram_detail = "BOT_TOKEN не задан" if not bot_configured else "Соединение работает" if telegram_state == "online" else "Токен настроен; соединение не подтверждено" if telegram_state == "unknown" else "Последняя проверка соединения не прошла"
    checks.append({"name": "Telegram", "state": telegram_check, "detail": telegram_detail})
    groq_check = "error" if not groq_configured or groq_state == "error" else "ok" if groq_state == "available" else "warning"
    groq_detail = "Ключ не задан" if not groq_configured else "API доступен" if groq_state == "available" else "Последняя проверка API не прошла" if groq_state == "error" else "Настроен; доступность ещё не подтверждена"
    checks.append({"name": "Groq", "state": groq_check, "detail": groq_detail})

    memory = inspect_memory(memory_file)
    checks.append({"name": "Память", "state": memory["state"], "detail": memory["detail"]})

    required_modules = ("aiohttp", "aiogram", "groq", "dotenv")
    missing_modules = []
    for name in required_modules:
        try:
            importlib.import_module(name)
        except Exception as error:
            missing_modules.append(f"{name} ({type(error).__name__})")
    if missing_modules and android:
        dep_state = "warning"
        dep_detail = "SKIP на Android: " + ", ".join(missing_modules)
    elif missing_modules:
        dep_state = "error"
        dep_detail = "Не найдены: " + ", ".join(missing_modules)
    else:
        dep_state = "ok"
        dep_detail = "Обязательные библиотеки обнаружены"
    checks.append({"name": "Зависимости", "state": dep_state, "detail": dep_detail})

    states = {item["state"] for item in checks}
    overall = "error" if "error" in states else "warning" if "warning" in states else "ok"
    return {"overall": overall, "checks": checks, "memory": memory}
