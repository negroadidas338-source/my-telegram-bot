"""Per-user proactive Alice messages, paced and persisted in the existing memory file."""

import asyncio
import hashlib
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from aiogram.exceptions import AiogramError, TelegramForbiddenError
from groq import APIConnectionError, APIStatusError


class AliceActivity:
    MIN_DAILY_MESSAGES = 30
    MAX_DAILY_MESSAGES = 50
    SCHEDULE_VERSION = 2
    FIRST_MESSAGE_MIN_SECONDS = 5
    FIRST_MESSAGE_MAX_SECONDS = 10
    MIN_INTERVAL_SECONDS = 30
    MAX_INTERVAL_SECONDS = 90
    RETRY_MIN_SECONDS = 60
    RETRY_MAX_SECONDS = 120

    def __init__(
        self,
        *,
        bot,
        memory,
        ask_groq: Callable,
        build_prompt: Callable,
        clean_reply: Callable,
        get_custom_style: Callable,
        get_facts: Callable,
    ):
        self.bot = bot
        self.memory = memory
        self.ask_groq = ask_groq
        self.build_prompt = build_prompt
        self.clean_reply = clean_reply
        self.get_custom_style = get_custom_style
        self.get_facts = get_facts
        self.tasks: Dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _state(self, user_id: int) -> Dict[str, Any]:
        return self.memory.get_activity_state(user_id)

    def _save(self, user_id: int, **updates):
        self.memory.update_activity_state(user_id, updates)

    def _ensure_day(self, user_id: int) -> Dict[str, Any]:
        state = self._state(user_id)
        today = self._today()
        if state.get("date") != today:
            state.update(
                date=today,
                sent=0,
                target=random.randint(self.MIN_DAILY_MESSAGES, self.MAX_DAILY_MESSAGES),
                next_at=None,
            )
            self._save(user_id, date=today, sent=0, target=state["target"], next_at=None)
        try:
            schedule_version = int(state.get("schedule_version", 0))
        except (TypeError, ValueError):
            schedule_version = 0
        if schedule_version < self.SCHEDULE_VERSION:
            next_at = None
            if state.get("enabled", True) and int(state.get("sent", 0)) < int(
                state.get("target", self.MIN_DAILY_MESSAGES)
            ):
                next_at = time.time() + random.randint(
                    self.FIRST_MESSAGE_MIN_SECONDS, self.FIRST_MESSAGE_MAX_SECONDS
                )
            self._save(user_id, schedule_version=self.SCHEDULE_VERSION, next_at=next_at)
            state["schedule_version"] = self.SCHEDULE_VERSION
            state["next_at"] = next_at
        return state

    def _ensure_task(self, user_id: int) -> None:
        task = self.tasks.get(user_id)
        if task is None or task.done():
            self.tasks[user_id] = asyncio.create_task(
                self._run(user_id), name=f"alice-activity-{user_id}"
            )

    def register_user(self, user_id: int) -> None:
        """Enroll a user after they explicitly open the private chat with /start."""
        state = self._state(user_id)
        if not state.get("enrolled"):
            self._save(user_id, enrolled=True, enabled=True)
        state = self._ensure_day(user_id)
        if state.get("enabled", True):
            self._ensure_task(user_id)

    async def start(self):
        """Resume schedulers for users who previously started the private bot chat."""
        users = self.memory.get_activity_users()
        for user_id in users:
            state = self._ensure_day(user_id)
            if state.get("enabled", True):
                if not state.get("next_at") or float(state.get("next_at", 0)) <= time.time():
                    self._save(
                        user_id,
                        next_at=time.time() + random.randint(
                            self.FIRST_MESSAGE_MIN_SECONDS, self.FIRST_MESSAGE_MAX_SECONDS
                        ),
                    )
                self._ensure_task(user_id)
        print(f"Планировщик активности Алисы запущен для {len(users)} личных чатов")

    async def stop(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    def _schedule(self, user_id: int, delay: float):
        self._save(user_id, next_at=time.time() + max(1, delay))

    def status(self, user_id: int) -> Dict[str, Any]:
        state = self._ensure_day(user_id)
        return {
            "enabled": bool(state.get("enabled", True)),
            "sent": int(state.get("sent", 0)),
            "target": int(state.get("target", self.MIN_DAILY_MESSAGES)),
            "next_at": state.get("next_at"),
        }

    async def pause(self, user_id: int):
        async with self._lock:
            self._save(user_id, enrolled=True, enabled=False, next_at=None)

    async def resume(self, user_id: int):
        async with self._lock:
            self._ensure_day(user_id)
            self._save(
                user_id,
                enrolled=True,
                enabled=True,
                next_at=time.time() + random.randint(
                    self.FIRST_MESSAGE_MIN_SECONDS, self.FIRST_MESSAGE_MAX_SECONDS
                ),
            )
            self._ensure_task(user_id)

    async def _run(self, user_id: int):
        while True:
            state = self._ensure_day(user_id)
            if not state.get("enabled", True):
                await asyncio.sleep(60)
                continue

            # A pending reservation means the process stopped during send. Drop it rather
            # than risk delivering the same initiative twice after restart.
            if state.get("pending_hash"):
                self._save(user_id, pending_hash=None)
                state["pending_hash"] = None

            sent = int(state.get("sent", 0))
            target = int(state.get("target", self.MIN_DAILY_MESSAGES))
            if sent >= target:
                await asyncio.sleep(60)
                continue

            next_at = state.get("next_at")
            now = time.time()
            if not next_at:
                self._schedule(
                    user_id,
                    random.randint(self.FIRST_MESSAGE_MIN_SECONDS, self.FIRST_MESSAGE_MAX_SECONDS),
                )
                continue
            remaining = next_at - now
            if remaining > 0:
                await asyncio.sleep(min(remaining, 60))
                continue

            try:
                answer = await self._generate(user_id)
                if not answer:
                    raise RuntimeError("Groq вернул пустую инициативу")
                digest = hashlib.sha256(answer.casefold().encode("utf-8")).hexdigest()
                recent = state.get("recent_hashes", [])
                if digest in recent:
                    raise RuntimeError("Groq повторил недавнее сообщение")

                # Respect a pause that was requested while Groq was generating.
                current = self._state(user_id)
                if not current.get("enabled", True):
                    continue

                self._save(user_id, pending_hash=digest, recent_hashes=(recent + [digest])[-20:])
                await self.bot.send_message(user_id, answer)
                self._save(user_id, sent=sent + 1, pending_hash=None)
                self.memory.add_message(user_id, "assistant", answer)
                print(f"Инициатива Алисы отправлена в личный чат {user_id} ({sent + 1}/{target} за UTC-день)")
                self._schedule(user_id, self._next_interval(sent + 1, target))
            except TelegramForbiddenError:
                self._save(user_id, enrolled=True, enabled=False, next_at=None, pending_hash=None)
                print(f"Самостоятельная активность остановлена: чат {user_id} больше недоступен")
            except (APIConnectionError, APIStatusError, AiogramError) as error:
                self._save(user_id, pending_hash=None)
                self._schedule(
                    user_id, random.randint(self.RETRY_MIN_SECONDS, self.RETRY_MAX_SECONDS)
                )
                print(f"Временный сбой инициативы Алисы для чата {user_id}: {type(error).__name__}")
            except Exception as error:
                current = self._state(user_id)
                if current.get("pending_hash"):
                    self._save(user_id, pending_hash=None)
                    if int(current.get("sent", 0)) > sent:
                        self._schedule(user_id, self._next_interval(sent + 1, target))
                    else:
                        self._schedule(
                            user_id, random.randint(self.RETRY_MIN_SECONDS, self.RETRY_MAX_SECONDS)
                        )
                else:
                    self._schedule(
                        user_id, random.randint(self.RETRY_MIN_SECONDS, self.RETRY_MAX_SECONDS)
                    )
                print(f"Не удалось создать инициативу Алисы для чата {user_id}: {type(error).__name__}")

    def _next_interval(self, sent: int, target: int) -> float:
        return random.randint(self.MIN_INTERVAL_SECONDS, self.MAX_INTERVAL_SECONDS)

    async def _generate(self, user_id: int) -> str:
        history = self.memory.get_history(user_id)[-20:]
        system = self.build_prompt(
            "alice",
            custom_style=self.get_custom_style(user_id),
            in_group=False,
        )
        facts = self.get_facts(user_id)
        if facts:
            system += (
                "\n\nСОХРАНЁННЫЕ ФАКТЫ О СОБЕСЕДНИКЕ (используй только к месту, "
                "не перечисляй и не выдумывай новые):\n"
                + "\n".join(f"- {fact}" for fact in facts)
            )
        system += (
            "\n\nСАМОСТОЯТЕЛЬНАЯ РЕПЛИКА: сейчас ты сама начинаешь естественный разговор "
            "с собеседником в личном чате. Напиши одно оригинальное сообщение от лица Алисы: "
            "подхвати реальный недавний контекст, если он есть; иначе выбери живую тему, "
            "наблюдение, вопрос или забавную идею. Не делай вид, что знаешь, чем собеседник занят. "
            "Не упоминай расписание, лимиты, модель или то, что сообщение сгенерировано. "
            "Не требуй ответа, не пиши шаблонное «ты где?» и не повторяй недавние инициативы. "
            "Верни только текст сообщения, без кавычек и пояснений."
        )
        request = {"role": "user", "content": "Начни разговор сейчас. Напиши одну уместную реплику."}
        response = await self.ask_groq([{"role": "system", "content": system}, *history, request])
        choice = response.choices[0].message.content
        return (self.clean_reply(choice) or "").strip()[:4000]
