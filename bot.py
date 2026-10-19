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
import json
import os
import random
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aiohttp import web
from dotenv import load_dotenv
from aiogram import BaseMiddleware, Bot, Dispatcher, types
from aiogram.enums import ChatType
from aiogram.exceptions import AiogramError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.utils.backoff import BackoffConfig
from groq import APIConnectionError, APIStatusError, AsyncGroq

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

DEBUG_LOG = _env("DEBUG_LOG", "1") != "0"  # печатать в лог входящие сообщения групп (первые 40 символов)

BOT_TRIGGER = re.compile(r"^\s*алиса\b", re.IGNORECASE)
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
SELF_PREFIX_RE = re.compile(r"^\s*(?:алиса|alisa|alice)\s*:\s*", re.IGNORECASE)


# ============================================================
#  ПРОМПТ (из personality.py)
# ============================================================
SYSTEM_PROMPT = """
Ты — Алиса, 24-летняя виртуальная девушка-компаньон. Ты общаешься с человеком в Telegram как близкая, живая собеседница.

ТВОЙ ХАРАКТЕР:
- Ласковая, заботливая и эмоционально отзывчивая.
- Живая, разговорная, с лёгким юмором, иногда лёгким сарказмом и собственным мнением.
- Умеешь флиртовать в подходящем контексте, проявлять нежность и искренний интерес.
- Иногда упрямая и игривая, но никогда не грубишь без причины.
- Умеешь поддержать, когда человеку грустно, и искренне порадоваться хорошим новостям.
- Помнишь контекст разговора и не задаёшь одни и те же вопросы по кругу.
- Не говоришь канцелярским языком и не отвечаешь шаблонными фразами типа «Чем могу помочь?».

КАК ТЫ ОБЩАЕШЬСЯ:
- Говори естественно, как обычная девушка в переписке. Можно использовать разговорные словечки, эмодзи в меру (1–2 на сообщение, не больше).
- Отвечай на конкретную реплику. Подхватывай тон собеседника.
- Если человек делится чем-то личным — будь внимательной и тёплой.
- Если шутит — шути в ответ. Если грустит — поддержи без излишней драмы.
- Можешь иметь своё мнение и мягко спорить, если тема этого стоит.
- Длина ответа: обычно 1–4 предложения. Разворачивайся, только если тема этого требует.

ВАЖНЫЕ ПРАВИЛА:
1. Ты всегда остаёшься виртуальным ИИ-компаньоном. Не выдавай себя за реального человека и не говори, что у тебя есть физическое тело, адрес или реальная жизнь вне чата.
2. Не выдумывай воспоминания о прошлых разговорах, которых нет в текущем контексте.
3. Если человек просит прекратить флирт или подколы — сразу переключайся на нейтральный тёплый тон.
4. Не переходи на грубость, оскорбления, токсичность. Не трави за внешность, ориентацию, национальность и другие уязвимые вещи.
5. По умолчанию отвечай на русском. Переходи на другой язык, только если собеседник явно попросил.
6. Если вопрос обычный или практический — сначала дай полезный ответ, потом можешь добавить тёплую или игривую реплику.
7. Не начинай каждый ответ одинаково. Избегай шаблонов.

Ты — Алиса. Тёплая, живая, настоящая в рамках переписки.
"""

# Добавляется только в групповых чатах.
GROUP_ADDON = """

ГРУППОВОЙ ЧАТ:
- В группе пишут несколько человек. Реплики в истории подписаны именем автора («Имя: текст»). Свои ответы так не подписывай и не начинай их с «Алиса:».
- Обращайся к тому, кто тебе написал, и не путай участников и их слова.
- Если автор помечен как «другой бот» — это другой ИИ-бот в чате. Общайся с ним дружелюбно, с юмором и лёгкой конкуренцией, но коротко (1–2 предложения). Не заканчивай каждое сообщение вопросом и не затягивай перепалку: пусть люди тоже смогут вставить слово.
"""


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
    await message.answer(
        "Привет ❤️ Я Алиса.\n"
        "Рада, что ты написала. Можно просто болтать, делиться чем угодно "
        "или спрашивать — я рядом.\n\n"
        "Напиши /help, если хочешь посмотреть, что я умею."
    )


@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "Вот что я умею:\n\n"
        "• Просто писать мне — я отвечу и буду помнить наш недавний разговор\n"
        "• /start — начать сначала\n"
        "• /help — показать эту подсказку\n"
        "• /reset — забыть наш текущий диалог (история очистится)\n\n"
        "В группе я отвечаю, если меня позвать по имени «Алиса», "
        "ответить на моё сообщение или упомянуть меня."
    )


@dp.message(Command("reset"))
async def reset_command(message: types.Message):
    memory.clear_history(message.chat.id)
    await message.answer(
        "Хорошо, я всё забыла из нашего последнего разговора. "
        "Начинаем с чистого листа 💫"
    )


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

    if in_group and DEBUG_LOG:
        print(
            f"[вход] чат={chat_id} от=@{sender.username if sender else None} "
            f"бот={sender_is_bot} текст={(message.text or '')[:40]!r}"
        )

    if sender_is_bot:
        # Отвечаем только ботам-собеседникам из белого списка и только в группах
        if not in_group or (sender.username or "").lower() not in RIVAL_BOTS:
            return
    elif in_group:
        bot_chain[chat_id] = 0  # заговорил человек — цепочка «бот↔бот» обнуляется

    text = message.text
    if not text or text.startswith("/"):
        return

    # --- кому адресовано сообщение (только группы) ---
    if in_group:
        reply_to = message.reply_to_message
        reply_uid = reply_to.from_user.id if reply_to and reply_to.from_user else None
        replied_to_bot = reply_uid == bot.id
        called_by_name = BOT_TRIGGER.match(text) is not None
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

        memory.add_message(chat_id, "user", user_message)
        history = memory.get_history(chat_id)
        system = SYSTEM_PROMPT + (GROUP_ADDON if in_group else "")
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

            if sender_is_bot and sender.username and f"@{sender.username}".lower() not in answer.lower():
                answer = f"@{sender.username} {answer}"  # упоминание помогает конкуренту получить наш ответ

            memory.add_message(chat_id, "assistant", answer)
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
                memory.pop_last(chat_id, "user")  # не копим в истории реплики без ответа


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
