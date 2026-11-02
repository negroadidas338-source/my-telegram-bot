# -*- coding: utf-8 -*-
"""
Алиса — Telegram-бот (aiogram 3 + Groq) для запуска на Render.

Всё в одном файле: память, системный промпт, мини-веб-сервер для порта Render.

Переменные окружения:
  BOT_TOKEN              — токен бота (обязательно)
  GROQ_API_KEY           — ключ Groq (обязательно)
  GROQ_MODEL             — предпочтительная модель (необязательно)
  GROQ_FALLBACK_MODELS   — запасные модели через запятую (необязательно)
  RIVAL_BOTS             — username ботов-собеседников через запятую (по умолчанию LissaAI_bot)
  BOT_CHAIN_LIMIT        — сколько ответов подряд боту без вмешательства человека (по умолчанию 3)
  BOT_REPLY_COOLDOWN     — минимальная пауза между ответами боту, сек (по умолчанию 8)
  MEMORY_FILE            — путь к файлу памяти (по умолчанию memory.json рядом с bot.py)
  PORT                   — порт веб-сервера (Render задаёт сам)
"""

import asyncio
import html
import json
import os
import random
import re
import threading
import time
from datetime import datetime, timezone
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aiohttp import web
from dotenv import load_dotenv
from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.enums import ChatType
from aiogram.exceptions import AiogramError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.utils.backoff import BackoffConfig
from groq import APIConnectionError, APIStatusError, AsyncGroq
from persona_adapter import (
    ALICE,
    DEFAULT_PERSONA,
    MAX_CUSTOM_STYLE_LENGTH,
    NEUROHAM,
    build_system_prompt,
    mode_keyboard,
    normalize_persona,
    persona_name,
)

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


# ============================================================
#  НАСТРОЙКИ
# ============================================================
def _env(name: str, default: str = "") -> str:
    """Читает переменную окружения и убирает пробелы/кавычки по краям
    (частая причина «странных» ошибок, когда значение вставили с кавычками)."""
    return os.getenv(name, default).strip().strip('"').strip("'").strip()


def _env_number(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


TOKEN = _env("BOT_TOKEN")
GROQ_API_KEY = _env("GROQ_API_KEY")

if not TOKEN:
    raise SystemExit("Ошибка: не задан BOT_TOKEN (Render → Environment)")
if not GROQ_API_KEY:
    raise SystemExit("Ошибка: не задан GROQ_API_KEY (Render → Environment)")

# llama-3.3-70b-versatile Groq отключил 16.08.2026 для free/developer-тарифов,
# поэтому по умолчанию — рекомендованные Groq замены.
DEFAULT_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"]


def _build_model_candidates() -> List[str]:
    result: List[str] = []
    fallbacks = [m.strip() for m in _env("GROQ_FALLBACK_MODELS").split(",")]
    for model in [_env("GROQ_MODEL"), *fallbacks, *DEFAULT_MODELS]:
        if model and model not in result:
            result.append(model)
    return result


MODEL_CANDIDATES = _build_model_candidates()
MAX_TOKENS = int(_env_number("GROQ_MAX_TOKENS", 1200))  # с запасом на «размышления» reasoning-моделей

RIVAL_BOTS = {
    name.strip().lstrip("@").lower()
    for name in _env("RIVAL_BOTS", "LissaAI_bot").split(",")
    if name.strip()
}
BOT_CHAIN_LIMIT = int(_env_number("BOT_CHAIN_LIMIT", 3))
BOT_REPLY_COOLDOWN = _env_number("BOT_REPLY_COOLDOWN", 8.0)

ALICE_TRIGGER = re.compile(r"^\s*алиса\b", re.IGNORECASE)
NEUROHAM_TRIGGER = re.compile(r"^\s*нейрохам\b", re.IGNORECASE)
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
SELF_PREFIX_RE = re.compile(r"^\s*(?:алиса|alisa|alice|нейрохам|neuroham)\s*:\s*", re.IGNORECASE)


# ============================================================
#  ПАМЯТЬ (из memory.py)
# ============================================================
MEMORY_FILE = _env("MEMORY_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
MAX_HISTORY_PER_CHAT = 20
MAX_CHATS = 200
AUTOSAVE_INTERVAL = 10


class Memory:
    """История чатов + данные о пользователях (имена, факты, связи). Потокобезопасно."""

    def __init__(self, filepath: str = MEMORY_FILE):
        self.filepath = filepath
        self.lock = threading.Lock()
        self._dirty = False
        self.chats: Dict[str, List[dict]] = {}
        self.users: Dict[str, dict] = {}
        self.last_seen: Dict[str, float] = {}
        self._load()
        self._start_autosave()

    # --- история чатов ---
    def get_history(self, chat_id: int) -> List[dict]:
        with self.lock:
            return list(self.chats.get(str(chat_id), []))

    def add_message(self, chat_id: int, role: str, content: str):
        key = str(chat_id)
        with self.lock:
            history = self.chats.setdefault(key, [])
            history.append({"role": role, "content": content})
            if len(history) > MAX_HISTORY_PER_CHAT:
                self.chats[key] = history[-MAX_HISTORY_PER_CHAT:]
            self.last_seen[key] = time.time()
            self._dirty = True

    def pop_last(self, chat_id: int, role: str):
        """Убирает последнее сообщение, если оно от указанной роли
        (чтобы неотвеченные из-за ошибки реплики не копились в истории)."""
        key = str(chat_id)
        with self.lock:
            history = self.chats.get(key)
            if history and history[-1].get("role") == role:
                history.pop()
                self._dirty = True

    def clear_history(self, chat_id: int):
        with self.lock:
            self.chats.pop(str(chat_id), None)
            self._dirty = True

    def clear_chat_histories(self, chat_id: int):
        """Очищает историю чата для всех режимов личности."""
        prefix = f"{chat_id}:"
        with self.lock:
            for key in list(self.chats):
                if key == str(chat_id) or key.startswith(prefix):
                    self.chats.pop(key, None)
                    self.last_seen.pop(key, None)
            self._dirty = True

    # --- пользователи ---
    def _ensure_user(self, user_id: int) -> dict:
        key = str(user_id)
        if key not in self.users:
            self.users[key] = {"name": None, "facts": [], "relations": {}, "created": time.time()}
        return self.users[key]

    def set_name(self, user_id: int, name: str):
        with self.lock:
            self._ensure_user(user_id)["name"] = name
            self._dirty = True

    def get_name(self, user_id: int) -> Optional[str]:
        with self.lock:
            return self.users.get(str(user_id), {}).get("name")

    def get_persona(self, user_id: int) -> str:
        with self.lock:
            return normalize_persona(
                self.users.get(str(user_id), {}).get("persona", DEFAULT_PERSONA)
            )

    def set_persona(self, user_id: int, persona: str):
        with self.lock:
            self._ensure_user(user_id)["persona"] = normalize_persona(persona)
            self._dirty = True

    def get_custom_style(self, user_id: int) -> str:
        with self.lock:
            value = self.users.get(str(user_id), {}).get("custom_style", "")
            return value if isinstance(value, str) else ""

    def set_custom_style(self, user_id: int, style: str):
        with self.lock:
            self._ensure_user(user_id)["custom_style"] = style[:MAX_CUSTOM_STYLE_LENGTH]
            self._dirty = True

    def remember_fact(self, user_id: int, fact: str):
        with self.lock:
            user = self._ensure_user(user_id)
            if fact not in user["facts"]:
                user["facts"] = (user["facts"] + [fact])[-30:]
                self._dirty = True

    def get_user_facts(self, user_id: int) -> List[str]:
        with self.lock:
            return list(self.users.get(str(user_id), {}).get("facts", []))

    def set_relation(self, user_id: int, other_user_id: int, relation: str):
        with self.lock:
            self._ensure_user(user_id)["relations"][str(other_user_id)] = relation
            self._dirty = True

    def get_relation(self, user_id: int, other_user_id: int) -> Optional[str]:
        with self.lock:
            return self.users.get(str(user_id), {}).get("relations", {}).get(str(other_user_id))

    def get_all_relations(self, user_id: int) -> Dict[str, str]:
        with self.lock:
            return dict(self.users.get(str(user_id), {}).get("relations", {}))

    def forget_user(self, user_id: int):
        key = str(user_id)
        with self.lock:
            self.users.pop(key, None)
            for data in self.users.values():
                data.get("relations", {}).pop(key, None)
            self._dirty = True

    def get_user_info(self, user_id: int) -> dict:
        with self.lock:
            return dict(self.users.get(str(user_id), {}))

    def list_all_users(self) -> Dict[str, dict]:
        with self.lock:
            return dict(self.users)

    # --- сохранение / загрузка ---
    def _load(self):
        if not os.path.exists(self.filepath):
            print(f"📂 Файл памяти не найден, создаём новый: {self.filepath}")
            return
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.chats = data.get("chats", {})
            self.users = data.get("users", {})
            self.last_seen = data.get("last_seen", {})
            print(f"📂 Память загружена: {len(self.chats)} чатов, {len(self.users)} юзеров")
        except (json.JSONDecodeError, OSError) as error:
            print(f"⚠️ Ошибка загрузки памяти: {error}. Начинаем с чистой.")
            self.chats, self.users, self.last_seen = {}, {}, {}

    def save(self, force: bool = False):
        with self.lock:
            if not force and not self._dirty:
                return
            if len(self.chats) > MAX_CHATS:
                oldest_first = sorted(self.last_seen.items(), key=lambda item: item[1])
                for key, _ in oldest_first[:-MAX_CHATS]:
                    self.chats.pop(key, None)
                    self.last_seen.pop(key, None)
            data = {
                "chats": self.chats,
                "users": self.users,
                "last_seen": self.last_seen,
                "saved_at": time.time(),
            }
            try:
                tmp_path = self.filepath + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.filepath)
                self._dirty = False
            except OSError as error:
                print(f"⚠️ Ошибка сохранения памяти: {error}")

    def _start_autosave(self):
        def worker():
            while True:
                time.sleep(AUTOSAVE_INTERVAL)
                try:
                    self.save()
                except Exception as error:
                    print(f"⚠️ Ошибка автосохранения: {error}")

        threading.Thread(target=worker, daemon=True).start()

    def shutdown(self):
        self.save(force=True)
        print("💾 Память сохранена.")


memory = Memory()


# ============================================================
#  ОБЪЕКТЫ БОТА И СОСТОЯНИЕ
# ============================================================
bot = Bot(token=TOKEN)
dp = Dispatcher()
ai = AsyncGroq(api_key=GROQ_API_KEY, max_retries=2, timeout=45.0)

bot_username: Optional[str] = None
groq_cooldown_until = 0.0

active_model = MODEL_CANDIDATES[0]
model_dead_until: Dict[str, float] = {}  # модель -> время, до которого её не трогаем (после 404)

chat_locks: Dict[int, asyncio.Lock] = {}   # один запрос за раз на чат
bot_chain: Dict[int, int] = {}              # сколько раз подряд ответили боту без человека
bot_last_reply: Dict[int, float] = {}      # когда последний раз отвечали боту
pending_style_users: set[int] = set()       # пользователи, ожидающие текст своих инструкций
pending_wish_users: set[int] = set()
pending_clear_wishes_users: set[int] = set()
WISHES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wishes.json")
WISH_BUTTON_TEXT = "📝 Оставить пожелание"


def read_wishes() -> List[dict]:
    try:
        with open(WISHES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as error:
        print(f"⚠️ Ошибка чтения пожеланий: {error}")
        return []


def write_wishes(wishes: List[dict]) -> bool:
    tmp_path = WISHES_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(wishes, file, ensure_ascii=False, indent=2)
        os.replace(tmp_path, WISHES_FILE)
        return True
    except OSError as error:
        print(f"⚠️ Ошибка записи пожеланий: {error}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False


def wishes_keyboard() -> types.ReplyKeyboardMarkup:
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=WISH_BUTTON_TEXT)]],
        resize_keyboard=True,
    )


# ============================================================
#  ДЕДУПЛИКАЦИЯ ВХОДЯЩИХ СООБЩЕНИЙ
# ============================================================
class Deduper:
    """Помнит (chat_id, message_id) и говорит, видели ли мы уже такое сообщение.
    Проверка и пометка выполняются синхронно (без await), поэтому гонок нет."""

    def __init__(self, ttl: float = 3600.0, max_size: int = 5000):
        self.ttl = ttl
        self.max_size = max_size
        self._seen: "OrderedDict[tuple, float]" = OrderedDict()

    def seen(self, chat_id: int, message_id: int) -> bool:
        now = time.monotonic()
        while self._seen:
            _, stamp = next(iter(self._seen.items()))
            if now - stamp > self.ttl or len(self._seen) > self.max_size:
                self._seen.popitem(last=False)
            else:
                break
        key = (chat_id, message_id)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


deduper = Deduper()


class DedupMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, types.Message) and deduper.seen(event.chat.id, event.message_id):
            print(f"Дубликат {event.chat.id}/{event.message_id} пропущен")
            return None
        return await handler(event, data)


dp.message.outer_middleware(DedupMiddleware())


# ============================================================
#  GROQ: выбор модели, запрос, очистка ответа
# ============================================================
async def resolve_model():
    """При старте сверяет модели с реальным списком Groq и выбирает первую доступную."""
    global active_model
    try:
        listing = await ai.models.list()
        available = {m.id for m in listing.data}
    except Exception as error:
        print(f"Не удалось получить список моделей Groq ({type(error).__name__}), беру {active_model}")
        return
    for model in MODEL_CANDIDATES:
        if model in available:
            active_model = model
            print(f"Модель Groq: {active_model}")
            return
    print(
        "⚠️ Ни одна из моделей-кандидатов не найдена у Groq. "
        f"Кандидаты: {MODEL_CANDIDATES}. Доступные: {sorted(available)}"
    )


def _extra_body(model: str) -> Optional[dict]:
    # gpt-oss «думает» перед ответом; ограничиваем, чтобы не съедать лимит токенов
    if "gpt-oss" in model:
        return {"reasoning_effort": "low"}
    return None


async def _create(model: str, messages: List[dict]):
    kwargs = dict(model=model, messages=messages, temperature=0.85, max_tokens=MAX_TOKENS)
    extra = _extra_body(model)
    if extra:
        try:
            return await ai.chat.completions.create(**kwargs, extra_body=extra)
        except APIStatusError as error:
            if error.status_code != 400:
                raise
            print(f"Модель {model} не приняла доп. параметры, повторяю без них")
    return await ai.chat.completions.create(**kwargs)


def _model_is_gone(error: APIStatusError) -> bool:
    text = str(error).lower()
    return error.status_code == 404 or (
        error.status_code == 400 and ("decommission" in text or "does not exist" in text)
    )


async def ask_groq(messages: List[dict]):
    """Пробует активную модель, при 404/«модель снята» переходит к следующей."""
    global active_model
    now = time.monotonic()
    alive = [m for m in MODEL_CANDIDATES if model_dead_until.get(m, 0.0) <= now] or list(MODEL_CANDIDATES)
    order = [active_model] + [m for m in alive if m != active_model] if active_model in alive else alive

    last_error: Optional[APIStatusError] = None
    for model in order:
        try:
            response = await _create(model, messages)
        except APIStatusError as error:
            if _model_is_gone(error):
                print(f"Модель {model} недоступна ({error.status_code}), пробую следующую")
                model_dead_until[model] = time.monotonic() + 600
                last_error = error
                continue
            raise
        if model != active_model:
            print(f"Переключился на модель {model}")
            active_model = model
        return response
    assert last_error is not None
    raise last_error


def clean_answer(text: Optional[str]) -> str:
    if not text:
        return ""
    text = THINK_RE.sub("", text)
    text = SELF_PREFIX_RE.sub("", text.strip())
    return text.strip()[:4000]


# ============================================================
#  ВСПОМОГАТЕЛЬНОЕ
# ============================================================
def is_bot_mentioned(message: types.Message, text: str) -> bool:
    if not message.entities or not bot_username:
        return False
    utf16 = text.encode("utf-16-le")
    for entity in message.entities:
        if entity.type == "mention":
            raw = utf16[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")
            if raw.casefold() == f"@{bot_username}".casefold():
                return True
        elif entity.type == "text_mention" and entity.user and entity.user.id == bot.id:
            return True
    return False


async def safe_send(message: types.Message, text: str, in_group: bool):
    """В группах отвечает реплаем (видно, кому адресовано), в личке — обычным сообщением."""
    try:
        if in_group:
            try:
                await message.reply(text)
                return
            except TelegramBadRequest:
                pass  # например, исходное сообщение удалили — шлём без реплая
        await message.answer(text)
    except AiogramError as error:
        print(f"Не удалось отправить сообщение: {type(error).__name__}")


# ============================================================
#  КОМАНДЫ
# ============================================================
@dp.message(CommandStart())
async def start_command(message: types.Message):
    user_id = message.from_user.id if message.from_user else None
    current = memory.get_persona(user_id) if user_id is not None else DEFAULT_PERSONA
    if current == NEUROHAM:
        greeting = "Нейрохам на связи. Ну, чего надо? 😈"
    else:
        greeting = (
            "Привет ❤️ Я Алиса.\n"
            "Рада, что ты написала. Можно просто болтать, делиться чем угодно "
            "или спрашивать — я рядом."
        )
    await message.answer(
        f"{greeting}\n\nНапиши /mode, чтобы выбрать персонажа, "
        "а /settings — чтобы настроить стиль под себя.",
        reply_markup=wishes_keyboard() if message.chat.type == ChatType.PRIVATE else None,
    )


@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "Вот что я умею:\n\n"
        "• Просто писать мне — я отвечу и буду помнить наш недавний разговор\n"
        "• /start — начать сначала\n"
        "• /help — показать эту подсказку\n"
        "• /reset — забыть наш текущий диалог (история очистится)\n\n"
        "• /mode — выбрать режим Алисы или Злого Нейрохама\n"
        "• /settings — открыть личную настройку стиля\n\n"
        "В группе я отвечаю на обращение по имени выбранного режима, "
        "ответ на моё сообщение или упоминание. Выбор режима личный для каждого участника."
    )


@dp.message(Command("mode"))
async def mode_command(message: types.Message):
    user_id = message.from_user.id if message.from_user else None
    current = memory.get_persona(user_id) if user_id is not None else DEFAULT_PERSONA
    await message.answer(
        f"Твой текущий режим: {persona_name(current)}. Выбери свой:",
        reply_markup=mode_keyboard(include_custom=False),
    )


@dp.message(Command("settings"))
async def settings_command(message: types.Message):
    if message.chat.type != ChatType.PRIVATE:
        await message.answer(
            "Личные инструкции настраиваются в личном чате со мной. "
            "Текущий режим можно сменить прямо здесь командой /mode."
        )
        return
    user_id = message.from_user.id if message.from_user else None
    current = memory.get_persona(user_id) if user_id is not None else DEFAULT_PERSONA
    await message.answer(
        f"Твой режим: {persona_name(current)}. Выбери режим или настрой стиль своими словами.\n"
        f"Инструкции можно задать сообщением до {MAX_CUSTOM_STYLE_LENGTH} символов.",
        reply_markup=mode_keyboard(include_custom=True),
    )


@dp.message(Command("cancel"))
async def cancel_style_command(message: types.Message):
    if message.from_user:
        pending_style_users.discard(message.from_user.id)
        pending_wish_users.discard(message.from_user.id)
        pending_clear_wishes_users.discard(message.from_user.id)
    await message.answer("Ввод отменён. Сохранённые настройки не изменены.")


@dp.message(Command("wishes"))
async def wishes_command(message: types.Message):
    wishes = read_wishes()
    if not wishes:
        await message.answer("Пока пожеланий нет.")
        return
    chunks: List[str] = []
    current = ""
    for index, wish in enumerate(wishes, 1):
        name = html.escape(str(wish.get("user_name") or "Неизвестный пользователь"))
        date = html.escape(str(wish.get("created_at") or ""))
        text = html.escape(str(wish.get("text") or ""))
        entry = f"{index}. <b>{name}</b> ({date})\n{text}"
        candidate = f"{current}\n\n{entry}" if current else entry
        if len(candidate) > 3500 and current:
            chunks.append(current)
            current = entry
        else:
            current = candidate
    if current:
        chunks.append(current)
    for chunk in chunks:
        await message.answer(chunk, parse_mode="HTML")


@dp.message(Command("clear_wishes"))
async def clear_wishes_command(message: types.Message):
    if message.from_user:
        pending_clear_wishes_users.add(message.from_user.id)
    await message.answer("Чтобы очистить все пожелания, отправь /confirm_clear_wishes. Для отмены — /cancel.")


@dp.message(Command("confirm_clear_wishes"))
async def confirm_clear_wishes_command(message: types.Message):
    user_id = message.from_user.id if message.from_user else None
    if user_id is None or user_id not in pending_clear_wishes_users:
        await message.answer("Сначала нужно вызвать /clear_wishes.")
        return
    if write_wishes([]):
        pending_clear_wishes_users.discard(user_id)
        await message.answer("Список пожеланий очищен.")
    else:
        await message.answer("Не удалось очистить список пожеланий. Попробуй ещё раз.")


@dp.callback_query(F.data.startswith("persona:"))
async def persona_callback(callback: types.CallbackQuery):
    if not callback.from_user:
        await callback.answer()
        return

    user_id = callback.from_user.id
    action = callback.data.split(":", 1)[1]
    if action in (ALICE, NEUROHAM):
        memory.set_persona(user_id, action)
        pending_style_users.discard(user_id)
        await callback.answer(f"Твой режим: {persona_name(action)}")
        if isinstance(callback.message, types.Message) and callback.message.chat.type == ChatType.PRIVATE:
            await callback.message.answer(
                f"Готово. Для тебя выбран режим {persona_name(action)}."
            )
    elif action == "custom":
        if not isinstance(callback.message, types.Message) or callback.message.chat.type != ChatType.PRIVATE:
            await callback.answer(
                "Свои инструкции можно задать в личном чате через /settings.",
                show_alert=True,
            )
            return
        pending_style_users.add(user_id)
        await callback.answer()
        await callback.message.answer(
            f"Напиши, каким ты хочешь видеть мой стиль (до {MAX_CUSTOM_STYLE_LENGTH} символов).\n"
            "Команда /cancel отменит настройку."
        )
    elif action == "clear":
        memory.set_custom_style(user_id, "")
        pending_style_users.discard(user_id)
        await callback.answer("Твои дополнительные инструкции удалены.")
        if isinstance(callback.message, types.Message) and callback.message.chat.type == ChatType.PRIVATE:
            await callback.message.answer("Готово. Остался выбранный режим без личных инструкций.")
    else:
        await callback.answer("Эта кнопка устарела. Открой /settings ещё раз.", show_alert=True)


@dp.message(Command("reset"))
async def reset_command(message: types.Message):
    memory.clear_chat_histories(message.chat.id)
    await message.answer("Историю диалога очистил. Начинаем заново.")


# ============================================================
#  ОСНОВНОЙ ОБРАБОТЧИК
# ============================================================
@dp.message()
async def handle_all_messages(message: types.Message):
    global groq_cooldown_until

    sender = message.from_user
    chat_id = message.chat.id
    in_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if sender and sender.id == bot.id:
        return
    sender_is_bot = bool(sender and sender.is_bot)

    if sender_is_bot:
        # Отвечаем только ботам-собеседникам из белого списка и только в группах
        if not in_group or (sender.username or "").lower() not in RIVAL_BOTS:
            return
    elif in_group:
        bot_chain[chat_id] = 0  # заговорил человек — цепочка «бот↔бот» обнуляется

    text = message.text
    if not text:
        return

    # Пожелания принимаются только от обычных пользователей в личном чате.
    if message.chat.type == ChatType.PRIVATE and sender and not sender_is_bot:
        if text == WISH_BUTTON_TEXT:
            pending_wish_users.add(sender.id)
            await message.answer("Напиши своё пожелание одним сообщением. Для отмены отправь /cancel")
            return
        if sender.id in pending_wish_users:
            if text.startswith("/"):
                await message.answer("Отправь текст пожелания или /cancel для отмены.")
                return
            wish_text = text.strip()
            if len(wish_text) > 2000:
                await message.answer("Пожелание слишком длинное. Сократи текст до 2000 символов.")
                return
            wishes = read_wishes()
            wishes.append({
                "user_id": sender.id,
                "user_name": sender.full_name,
                "text": wish_text,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            if write_wishes(wishes):
                pending_wish_users.discard(sender.id)
                await message.answer("Спасибо! Пожелание записано")
            else:
                await message.answer("Не удалось записать пожелание. Попробуй ещё раз позже.")
            return

    # Личные инструкции принимаются только в личном чате и не попадают в общую память диалога.
    if (
        not in_group
        and message.chat.type == ChatType.PRIVATE
        and sender
        and sender.id in pending_style_users
    ):
        style = text.strip()
        if len(style) > MAX_CUSTOM_STYLE_LENGTH:
            await message.answer(
                f"Слишком длинно: максимум {MAX_CUSTOM_STYLE_LENGTH} символов. "
                "Сократи инструкцию или отправь /cancel."
            )
            return
        memory.set_custom_style(sender.id, style)
        pending_style_users.discard(sender.id)
        await message.answer("Сохранил твои пожелания к стилю. Они применяются только к твоим сообщениям.")
        return

    if text.startswith("/"):
        return

    persona = (
        memory.get_persona(sender.id)
        if sender and not sender_is_bot
        else DEFAULT_PERSONA
    )

    # --- кому адресовано сообщение (только группы) ---
    if in_group:
        reply_to = message.reply_to_message
        reply_uid = reply_to.from_user.id if reply_to and reply_to.from_user else None
        replied_to_bot = reply_uid == bot.id
        trigger = ALICE_TRIGGER if persona == ALICE else NEUROHAM_TRIGGER
        called_by_name = trigger.match(text) is not None
        mentioned_bot = is_bot_mentioned(message, text)

        if sender_is_bot:
            # Сообщения конкурента в общий чат (не реплай кому-то другому) тоже считаем адресованными нам
            addressed_elsewhere = reply_uid is not None and not replied_to_bot
            triggered = replied_to_bot or mentioned_bot or called_by_name or not addressed_elsewhere
        else:
            triggered = replied_to_bot or mentioned_bot or called_by_name
        if not triggered:
            return

    if time.monotonic() < groq_cooldown_until:
        return

    # Запоминаем имя человека (только если изменилось, чтобы зря не писать на диск)
    if sender and not sender_is_bot and memory.get_name(sender.id) != sender.full_name:
        memory.set_name(sender.id, sender.full_name)

    # Сообщения одного чата обрабатываем строго по очереди
    async with chat_locks.setdefault(chat_id, asyncio.Lock()):
        if sender_is_bot:
            if bot_chain.get(chat_id, 0) >= BOT_CHAIN_LIMIT:
                print(f"Лимит ответов боту ({BOT_CHAIN_LIMIT}) в чате {chat_id}: жду человека")
                return
            if time.monotonic() - bot_last_reply.get(chat_id, 0.0) < BOT_REPLY_COOLDOWN:
                return
            await asyncio.sleep(random.uniform(2.0, 4.0))  # «человеческая» пауза и дополнительный тормоз

        if in_group:
            if sender_is_bot:
                label = f"{sender.full_name} (другой бот, @{sender.username})"
            else:
                label = sender.full_name if sender else "Участник группы"
            user_message = f"{label}: {text}"
        else:
            user_message = text

        try:
            await bot.send_chat_action(chat_id, "typing")
        except AiogramError:
            pass

        history_id = chat_id if persona == ALICE else f"{chat_id}:{persona}"
        memory.add_message(history_id, "user", user_message)
        history = memory.get_history(history_id)
        custom_style = memory.get_custom_style(sender.id) if sender and not sender_is_bot else ""
        system = build_system_prompt(persona, custom_style=custom_style, in_group=in_group)
        success = False

        try:
            response = await ask_groq([{"role": "system", "content": system}, *history])
            answer = clean_answer(response.choices[0].message.content)

            if not answer:
                if not sender_is_bot:
                    await safe_send(
                        message,
                        "Ой, что-то я задумалась и слова потеряла. Напиши ещё раз, хорошо?",
                        in_group,
                    )
                return

            memory.add_message(history_id, "assistant", answer)
            success = True
            await safe_send(message, answer, in_group)

            if sender_is_bot:
                bot_chain[chat_id] = bot_chain.get(chat_id, 0) + 1
                bot_last_reply[chat_id] = time.monotonic()

        except APIStatusError as error:
            if error.status_code == 429:
                headers = getattr(getattr(error, "response", None), "headers", {}) or {}
                try:
                    retry_after = max(1, int(float(headers.get("retry-after", 60))))
                except (TypeError, ValueError):
                    retry_after = 60
                groq_cooldown_until = time.monotonic() + retry_after
                print(f"Groq rate limit: пауза {retry_after} сек.")
                if not sender_is_bot:
                    await safe_send(
                        message,
                        f"Сейчас у меня небольшая пауза (лимит запросов). "
                        f"Подожди примерно {retry_after} секунд, и я снова буду на связи 💫",
                        in_group,
                    )
            else:
                print(f"Ошибка Groq API {error.status_code} (модель {active_model}): {error}")
                if not sender_is_bot:
                    await safe_send(
                        message,
                        "Что-то связь прервалась. Попробуй чуть позже, я никуда не денусь.",
                        in_group,
                    )

        except APIConnectionError:
            print("Временная ошибка Groq API (connection)")
            if not sender_is_bot:
                await safe_send(
                    message,
                    "Что-то связь прервалась. Попробуй чуть позже, я никуда не денусь.",
                    in_group,
                )

        except AiogramError as error:
            print(f"Временная ошибка Telegram: {type(error).__name__}")

        except Exception as error:
            print(f"Ошибка обработки сообщения: {type(error).__name__}: {error}")
            if not sender_is_bot:
                await safe_send(
                    message,
                    "Что-то пошло не так. Попробуй ещё раз чуть позже, ладно?",
                    in_group,
                )

        finally:
            if not success:
                memory.pop_last(history_id, "user")  # не копим реплики без ответа


# ============================================================
#  ВЕБ-СЕРВЕР ДЛЯ RENDER
# ============================================================
async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_web() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        port = int(os.environ.get("PORT", "10000"))
    except ValueError:
        port = 10000
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"Веб-сервер запущен на порту {port}")
    return runner


# ============================================================
#  ЗАПУСК
# ============================================================
async def init_bot_identity():
    global bot_username
    for attempt in range(1, 4):
        try:
            me = await bot.get_me()
            bot_username = me.username
            print(f"Бот: @{bot_username} (id {me.id})")
            return
        except AiogramError as error:
            print(f"get_me, попытка {attempt}: {type(error).__name__}")
            await asyncio.sleep(2 * attempt)
    print("⚠️ Не удалось получить username бота — упоминания через @ работать не будут")


async def run_polling():
    """Polling с автоперезапуском. aiogram сам повторяет запрос при TelegramConflictError
    (с нарастающей паузой); внешний цикл страхует от любых неожиданных падений."""
    backoff_config = BackoffConfig(min_delay=2.0, max_delay=30.0, factor=1.6, jitter=0.2)
    delay = 5.0
    while True:
        started = time.monotonic()
        try:
            await dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
                backoff_config=backoff_config,
                close_bot_session=False,
            )
            return  # штатная остановка (SIGTERM/SIGINT)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"Polling упал: {type(error).__name__}: {error}")
        delay = 5.0 if time.monotonic() - started > 120 else min(delay * 2, 60.0)
        print(f"Перезапуск polling через {delay:.0f} сек.")
        await asyncio.sleep(delay)


async def main():
    print("Алиса запускается...")
    runner = await start_web()  # порт открываем первым, чтобы Render не убил деплой по таймауту
    try:
        await init_bot_identity()
        await resolve_model()
        try:
            # drop_pending_updates: не отвечаем пачкой на накопленные за простой сообщения
            await bot.delete_webhook(drop_pending_updates=True)
        except AiogramError as error:
            print(f"delete_webhook: {type(error).__name__}")
        await run_polling()
    finally:
        await runner.cleanup()
        memory.shutdown()
        await bot.session.close()
        await ai.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nАлиса остановлена.")
