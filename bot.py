import asyncio
import os
import re
import time
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatType
from aiogram.exceptions import AiogramError
from aiohttp import web
from groq import AsyncGroq, APIConnectionError, APIStatusError

from personality import SYSTEM_PROMPT
from memory import memory

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# Используем самую стабильную и мощную модель Groq с поддержкой лонгридов
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

if not TOKEN:
    raise SystemExit("Ошибка: в .env или в окружении Render отсутствует BOT_TOKEN")
if not GROQ_API_KEY:
    raise SystemExit("Ошибка: в .env или в окружении Render отсутствует GROQ_API_KEY")

bot = Bot(token=TOKEN)
dp = Dispatcher()
ai = AsyncGroq(api_key=GROQ_API_KEY)

groq_cooldown_until = 0.0

# --- НЕБЛОКИРУЮЩИЙ ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def handle(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Веб-сервер Render успешно запущен на порту {port}")
# -------------------------------------------


@dp.message(CommandStart())
async def start_command(message: types.Message):
    await message.answer(
        "Привет ❤️ Я Алиса.\n"
        "Рада, что ты написала. Можно просто болтать, делиться чем угодно "
        "или спрашивать — я рядом.\n\n"
        "Напиши /help, если хочешь посмотреть, что умею."
    )


@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "Вот что я умею:\n\n"
        "• Просто писать мне — я отвечу и буду помнить наш недавний разговор\n"
        "• /start — начать сначала\n"
        "• /help — показать эту подсказку\n"
        "• /reset — забыть наш текущий диалог (история очистится)\n\n"
        "В групповом чате я участвую в битвах ботов и поддерживаю диалог со всеми!"
    )


@dp.message(Command("reset"))
async def reset_command(message: types.Message):
    chat_id = message.chat.id
    memory.clear_history(chat_id)
    await message.answer(
        "Хорошо, я всё забыла из нашего последнего разговора. "
        "Начинаем с чистого листа 💫"
    )


@dp.message()
async def handle_all_messages(message: types.Message):
    global groq_cooldown_until

    text = message.text

    if not text or text.startswith("/"):
        return

    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    # В личных сообщениях игнорируем чужих ботов, но в ГРУППЕ разрешаем битку с Лизой (@LissaAI_bot)
    if message.from_user and message.from_user.is_bot and not is_group:
        return

    if time.monotonic() < groq_cooldown_until:
        return

    chat_id = message.chat.id

    if is_group:
        # Если сообщение от другого бота (например, Лизы), фиксируем её как конкурента
        if message.from_user and message.from_user.is_bot:
            name = "Лиза (Конкурент)"
        else:
            name = message.from_user.full_name if message.from_user else "Участник"
        user_message = f"{name}: {text}"
    else:
        user_message = text

    memory.add_message(chat_id, "user", user_message)
    history = memory.get_history(chat_id)

    # Безопасная сборка истории с системным промптом
    valid_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        if isinstance(msg, dict) and "role" in msg and "content" in msg:
            valid_messages.append(msg)

    try:
        response = await ai.chat.completions.create(
            model=GROQ_MODEL,
            messages=valid_messages,
            temperature=0.85,
            max_tokens=3000,  # Увеличенный лимит для полноценных лонгридов
        )
        answer = response.choices[0].message.content

        if not answer or not answer.strip():
            await message.answer(
                "Ой, что-то я задумалась и слова потеряла. "
                "Напиши ещё раз, хорошо?"
            )
            return

        answer = answer.strip()[:4000]
        memory.add_message(chat_id, "assistant", answer)
        await message.answer(answer)

    except APIStatusError as error:
        if error.status_code == 429:
            headers = getattr(getattr(error, "response", None), "headers", {}) or {}
            try:
                retry_after = max(1, int(float(headers.get("retry-after", 60))))
            except (TypeError, ValueError):
                retry_after = 60
            groq_cooldown_until = time.monotonic() + retry_after
            print(f"Groq rate limit: пауза {retry_after} сек.")
            try:
                await message.answer(
                    f"Сейчас у меня небольшая пауза (лимит запросов). "
                    f"Подожди примерно {retry_after} секунд, и я снова буду на связи 💫"
                )
            except AiogramError:
                pass
            return

        print(f"Ошибка Groq API ({error.status_code}): {type(error).__name__}")
        try:
            await message.answer(
                "Что-то связь прервалась. Попробуй чуть позже, я никуда не денусь."
            )
        except AiogramError:
            pass

    except APIConnectionError:
        print("Временная ошибка Groq API (connection)")
        try:
            await message.answer(
                "Что-то связь прервалась. Попробуй чуть позже, я никуда не денусь."
            )
        except AiogramError:
            pass

    except AiogramError as error:
        print(f"Временная ошибка Telegram: {type(error).__name__}")

    except Exception as error:
        print(f"Ошибка обработки сообщения: {type(error).__name__}: {error}")
        try:
            await message.answer(
                "Что-то пошло не так. Попробуй ещё раз чуть позже, ладно?"
            )
        except AiogramError:
            pass


async def main():
    # Запускаем фоновый веб-сервер для Render, чтобы он сразу видел открытый порт
    asyncio.create_task(start_web_server())

    print("Алиса запускается...")
    try:
        await dp.start_polling(bot)
    finally:
        memory.shutdown()
        await bot.session.close()
        await ai.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nАлиса остановлена.")
        memory.shutdown()
