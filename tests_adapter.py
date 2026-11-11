"""Owner-only command to run the project test suite and report results in chat."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

from aiogram import Dispatcher, types
from aiogram.filters import Command

from diagnostics import load_test_summary


class TestsAdapter:
    """Registers /tests (and /run_tests) for the bot owner only."""

    def __init__(
        self,
        *,
        dispatcher: Dispatcher,
        project_root: str | Path,
        owner_id: Optional[int],
        timeout: float = 240.0,
    ):
        self.dp = dispatcher
        self.project_root = Path(project_root).resolve()
        self.owner_id = owner_id
        self.timeout = timeout
        self._running = False

    def register_commands(self) -> None:
        self.dp.message.register(self.run_tests_command, Command("tests"))
        self.dp.message.register(self.run_tests_command, Command("run_tests"))

    async def run_tests_command(self, message: types.Message) -> None:
        if self.owner_id is None:
            await message.answer(
                "Команда /tests отключена: задай OWNER_USER_ID в окружении."
            )
            return
        if message.from_user is None or message.from_user.id != self.owner_id:
            await message.answer("Эта команда доступна только владельцу.")
            return
        if self._running:
            await message.answer("Тесты уже запущены, дождись завершения.")
            return

        runner = self.project_root / "tests" / "run_tests.py"
        if not runner.is_file():
            await message.answer(
                "Файл tests/run_tests.py не найден. Сначала загрузи папку tests."
            )
            return

        self._running = True
        status = await message.answer("Запускаю тесты…")

        env = os.environ.copy()
        # Do not leak tokens into the test process environment unnecessarily
        env.pop("BOT_TOKEN", None)
        env.pop("GROQ_API_KEY", None)

        command = [sys.executable, "-B", str(runner)]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.project_root),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                await status.edit_text("Тесты превысили лимит времени и были остановлены.")
                return

            output_text = (output or b"").decode("utf-8", errors="replace")
            summary = load_test_summary(
                self.project_root / "reports" / "test_results.json"
            )

            if summary:
                lines = [
                    f"Тесты завершены ({summary.get('result', '?')})",
                    f"Всего: {summary.get('total', 0)}",
                    f"Пройдено: {summary.get('passed', 0)}",
                    f"Провалено: {summary.get('failed', 0)}",
                    f"Ошибок: {summary.get('errors', 0)}",
                    f"Пропущено: {summary.get('skipped', 0)}",
                ]
                if summary.get("timestamp"):
                    lines.append(f"Время: {summary['timestamp']}")
                text = "\n".join(lines)
            else:
                # Fallback if summary file is missing
                last_lines = "\n".join(output_text.strip().splitlines()[-12:])
                text = (
                    f"Тесты завершились (код {process.returncode}).\n"
                    f"Сводка не найдена.\n\n"
                    f"Последние строки:\n{last_lines or '—'}"
                )

            # Keep message short and free of secrets
            if len(text) > 3500:
                text = text[:3490] + "…"

            await status.edit_text(text)

        except Exception as exc:
            await status.edit_text(f"Не удалось запустить тесты: {type(exc).__name__}")
        finally:
            self._running = False
