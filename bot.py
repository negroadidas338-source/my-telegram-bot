import asyncio
import os
import re
import time
from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatType
from aiogram.exceptions import AiogramError
from groq import AsyncGroq, APIConnectionError, APIStatusError

from personality import SYSTEM_PROMPT
from memory import memory

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# Актуальная и мощная модель Groq с поддержкой лонгридов
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

if not TOKEN:
    raise SystemExit("Ошибка: в .env отсутствует BOT_TOKEN")
if not GROQ_API_KEY:
    raise SystemExit("Ошибка: в .env отсутствует GROQ_API_KEY")

bot = Bot(token=TOKEN)
dp = Dispatcher()
ai = AsyncGroq(api_key=GROQ_API_KEY)

groq_cooldown_until = 0.0
bot_username = None
BOT_TRIGGER = re.compile(r"^\s*алиса\b", re.IGNORECASE)

# Защита от дублирования сообщений
processed_messages = set()


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
        "В группе я участвую в битвах ботов и поддерживаю диалог со всеми!"
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
    global bot_username

    # Защита от дублей по ID сообщения
    msg_unique_key = (message.chat.id, message.message_id)
    if msg_unique_key in processed_messages:
        return
    processed_messages.add(msg_unique_key)
    if len(processed_messages) > 200:
        processed_messages.pop()

    text = message.text

    if not text or text.startswith("/"):
        return

    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if message.from_user and message.from_user.is_bot and not is_group:
        return

    # Разрешаем диалог с Лизой (@LissaAI_bot) в группах для битвы ботов
    is_rival_bot = bool(
        is_group 
        and message.from_user 
        and message.from_user.is_bot 
        and message.from_user.id != bot.id
    )

    if is_group and not is_rival_bot:
        replied_to_bot = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == bot.id
        )
        called_by_name = BOT_TRIGGER.match(text) is not None
        mentioned_bot = False

        if not replied_to_bot and message.entities:
            if bot_username is None:
                try:
                    me = await bot.get_me()
                    bot_username = me.username
                except AiogramError as error:
                    print(f"Не удалось получить имя бота: {type(error).__name__}")
                    return
            if bot_username:
                text_utf16 = text.encode("utf-16-le")
                for entity in message.entities:
                    if entity.type == "mention":
                        start = entity.offset * 2
                        end = (entity.offset + entity.length) * 2
                        mention = text_utf16[start:end].decode("utf-16-le")
                        if mention.casefold() == f"@{bot_username}".casefold():
                            mentioned_bot = True
                            break
                    elif (
                        entity.type == "text_mention"
                        and entity.user
                        and entity.user.id == bot.id
                    ):
                        mentioned_bot = True
                        break

        if not replied_to_bot and not mentioned_bot and not called_by_name:
            return

    if time.monotonic() < groq_cooldown_until:
        return

    chat_id = message.chat.id

    if is_group:
        if is_rival_bot:
            name = "Лиза (Конкурент)"
        else:
            name = (
                message.from_user.full_name
                if message.from_user
                else "Участник группы"
            )
        user_message = f"{name}: {text}"
    else:
        user_message = text

    memory.add_message(chat_id, "user", user_message)
    history = memory.get_history(chat_id)

    valid_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        if isinstance(msg, dict) and "role" in msg and "content" in msg:
            valid_messages.append(msg)

    try:
        response = await ai.chat.completions.create(
            model=GROQ_MODEL,
            messages=valid_messages,
            temperature=0.85,
            max_tokens=3000,  # Лимит для лонгридов
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


async def health(request):
    return web.Response(text="ok")


async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000)))
    await site.start()
    print("Веб-сервер для Render запущен.")
    return runner


async def main():
    print("Алиса запускается...")
    runner = await start_web()
    try:
        await dp.start_polling(bot)
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
        memory.shutdown()
