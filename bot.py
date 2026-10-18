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
    print("Предупреждение: отсутствует BOT_TOKEN")
if not GROQ_API_KEY:
    print("Предупреждение: отсутствует GROQ_API_KEY")

bot = Bot(token=TOKEN) if TOKEN else None
dp = Dispatcher()
ai = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

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

RANDOM_GIFS = [
    "https://media.giphy.com/media/3o7TKSjRrfIPjeiVyM/giphy.gif",
    "https://media.giphy.com/media/l0HlRnAWXxn0MhOBK/giphy.gif",
    "https://media.giphy.com/media/9Jvj4N96r2hO54s9O5/giphy.gif",
    "https://media.giphy.com/media/26u6dTP6p4y0iyBIQ/giphy.gif",
    "https://media.giphy.com/media/3oKIPnAiaMCws8nOsE/giphy.gif",
    "https://media.giphy.com/media/5vkWb96W158h2/giphy.gif",
    "https://media.giphy.com/media/12NUbkX6p4xOO4/giphy.gif",
    "https://media.giphy.com/media/xTiTnMhJTwNHCHdTZS/giphy.gif",
    "https://media.giphy.com/media/13Hgw5hF0jWM92/giphy.gif",
    "https://media.giphy.com/media/L95W4wv8nnb9K/giphy.gif",
    "https://media.giphy.com/media/JIX9t2j0ZTN9S/giphy.gif",
    "https://media.giphy.com/media/8L0P9W5o0pFsY/giphy.gif",
    "https://media.giphy.com/media/uWlpPGquhGZNFzY90z/giphy.gif",
    "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",
    "https://media.giphy.com/media/g9582DNuQppxC/giphy.gif"
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
        "В группе я участвую в битвах ботов и отвечаю на всё!"
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
    try:
        await message.answer_animation(
            animation=chosen_gif,
            caption="Лови гифку по заказу! 😎"
        )
    except Exception as e:
        print(f"Ошибка отправки гифки: {e}")


@dp.message()
async def handle_all_messages(message: types.Message):
    global groq_cooldown_until

    text = message.text

    if not text or text.startswith("/"):
        return

    # Проверка на ботов: обычных ботов игнорируем, но ДАЕМ ЗЕЛЕНЫЙ СВЕТ для NeuroHamBot (Лизы)
    if message.from_user and message.from_user.is_bot:
        is_competitor = (
            message.from_user.username 
            and message.from_user.username.casefold() == "NeuroHamBot".casefold()
        )
        if not is_competitor:
            return

    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if time.monotonic() < groq_cooldown_until:
        return

    chat_id = message.chat.id

    if is_group:
        # Если пишет бот-конкурент, подписываем его имя для контекста ИИ
        if message.from_user and message.from_user.is_bot:
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

    try:
        response = await ai.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *history,
            ],
            temperature=0.85,
            max_tokens=3000,
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

        if not is_group and random.random() < 0.25:
            try:
                chosen_gif = random.choice(RANDOM_GIFS)
                await message.answer_animation(
                    animation=chosen_gif,
                    caption="Кстати, настроение примерно такое 😏"
                )
            except Exception as e:
                print(f"Ошибка при отправке рандомной гифки: {e}")

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


