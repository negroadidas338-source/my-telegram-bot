import asyncio
import os
import random
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
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

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

# --- МИНИ-СЕРВЕР ДЛЯ RENDER (чтобы порт не падал по тайм-ауту) ---
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
    print(f"Веб-сервер для Render запущен на порту {port}")
# -------------------------------------------------------------

# Подборка классных гифок для «настроения» бота
RANDOM_GIFS = [
    "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif",
    "https://media.giphy.com/media/l0HlRnAWXxn0MhOBK/giphy.gif",
    "https://media.giphy.com/media/9Jvj4N96r2hO54s9O5/giphy.gif",
    "https://media.giphy.com/media/26u6dTP6p4y0iyBIQ/giphy.gif",
    "https://media.giphy.com/media/3oKIPnAiaMCws8nOsE/giphy.gif"
]


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
        "• /gif — прислать случайную анимацию\n"
        "• /start — начать сначала\n"
        "• /help — показать эту подсказку\n"
        "• /reset — забыть наш текущий диалог (история очистится)\n\n"
        "В группе я отвечаю, если меня позвать по имени «Алиса», "
        "ответить на моё сообщение или упомянуть меня."
    )


@dp.message(Command("reset"))
async def reset_command(message: types.Message):
    chat_id = message.chat.id
    memory.clear_history(chat_id)
    await message.answer(
        "Хорошо, я всё забыла из нашего последнего разговора. "
        "Начинаем с чистого листа 💫"
    )


@dp.message(Command("gif"))
async def gif_command(message: types.Message):
    chosen_gif = random.choice(RANDOM_GIFS)
    await message.answer_animation(
        animation=chosen_gif,
        caption="Лови гифку по заказу! 😎"
    )


@dp.message()
async def handle_all_messages(message: types.Message):
    global groq_cooldown_until
    global bot_username

    text = message.text

    if not text or text.startswith("/"):
        return

    if message.from_user and message.from_user.is_bot:
        return

    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if is_group:
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

    try:
        response = await ai.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *history,
            ],
            temperature=0.85,
            max_tokens=500,
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

        # Отправляем текстовый ответ от ИИ
        await message.answer(answer)

        # С вероятностью 25% (в личных сообщениях) бот по настроению кидает гифку
        if not is_group and random.random() < 0.25:
            chosen_gif = random.choice(RANDOM_GIFS)
            await message.answer_animation(
                animation=chosen_gif,
                caption="Кстати, настроение примерно такое 😏"
            )

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
    # Запускаем веб-сервер для Render в фоновом режиме
    await start_web_server()
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
