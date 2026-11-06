"""Per-user relationship scores for Alice and Neuroham (aiogram 3 adapter)."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from aiogram import Dispatcher, types
from aiogram.filters import Command

log = logging.getLogger(__name__)

PERSONAS = {
    "alice": {"name": "Алиса", "emoji": "💗", "tone": "теплее и внимательнее"},
    "neuroham": {"name": "Нейрохам", "emoji": "😈", "tone": "чуть дружелюбнее, сохраняя дерзкий характер"},
}
LEVELS = (
    (0, "Незнакомец"),
    (20, "Знакомство"),
    (40, "Приятель"),
    (60, "Доверие"),
    (80, "Близкий собеседник"),
)
DEFAULT_SCORE = 20
DAILY_GAIN_LIMIT = 3
DAILY_LOSS_LIMIT = 5
MAX_FINGERPRINTS = 30
MARKER_RE = re.compile(r"<relationship_update>.*?</relationship_update>", re.DOTALL | re.IGNORECASE)
VALID_DELTA_RE = re.compile(r"\s*(-5|-2|0|1|2)\s*\Z")


def level_for(score: int) -> tuple[int, str, int]:
    index = max(i for i, (floor, _) in enumerate(LEVELS) if score >= floor)
    floor, title = LEVELS[index]
    return index + 1, title, LEVELS[index + 1][0] if index + 1 < len(LEVELS) else 100


def parse_relationship_marker(text: Optional[str]) -> tuple[str, Optional[int]]:
    """Remove the private scoring marker; malformed or duplicate markers never score."""
    if not text:
        return "", None
    matches = list(MARKER_RE.finditer(text))
    cleaned = MARKER_RE.sub("", text)
    # A dangling or malformed marker indicates an incomplete model response.
    if text.lower().count("<relationship_update>") != len(matches) or len(matches) != 1:
        cleaned = re.sub(r"<relationship_update>.*$", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
        return re.sub(r"</?relationship_update>", "", cleaned, flags=re.IGNORECASE).strip(), None
    match = VALID_DELTA_RE.fullmatch(matches[0].group(0)[len("<relationship_update>"):-len("</relationship_update>")])
    cleaned = re.sub(r"</?relationship_update>", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned, int(match.group(1)) if match else None


class RelationshipStore:
    """Small atomic JSON store. Keeps only scores and hashes, never message text."""

    def __init__(self, filepath: str | os.PathLike[str]):
        self.filepath = Path(filepath)
        self.lock = threading.RLock()
        self.data: dict = {"version": 1, "users": {}}
        self._load()

    def _load(self) -> None:
        if not self.filepath.exists():
            with self.lock:
                self._save_locked()
            return
        try:
            loaded = json.loads(self.filepath.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or not isinstance(loaded.get("users", {}), dict):
                raise ValueError("invalid relationship data structure")
            self.data = {"version": 1, "users": loaded.get("users", {})}
        except (OSError, ValueError, json.JSONDecodeError) as error:
            # Preserve the damaged file so a restart never silently destroys user data.
            try:
                backup = self.filepath.with_name(
                    f"{self.filepath.name}.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
                )
                shutil.copy2(self.filepath, backup)
                log.error("Invalid relationship data preserved at %s (%s)", backup, type(error).__name__)
            except OSError:
                log.exception("Could not preserve invalid relationship data at %s", self.filepath)
            self.data = {"version": 1, "users": {}}

    def _profile(self, user_id: int, persona: str) -> dict:
        persona = persona if persona in PERSONAS else "alice"
        users = self.data.setdefault("users", {})
        if not isinstance(users, dict):
            users = self.data["users"] = {}
        user = users.setdefault(str(user_id), {})
        if not isinstance(user, dict):
            user = users[str(user_id)] = {}
        profile = user.setdefault(persona, {})
        if not isinstance(profile, dict):
            profile = user[persona] = {}
        profile.setdefault("score", DEFAULT_SCORE)
        profile.setdefault("day", "")
        profile.setdefault("gained", 0)
        profile.setdefault("lost", 0)
        profile.setdefault("fingerprints", [])
        if not isinstance(profile["fingerprints"], list):
            profile["fingerprints"] = []
        return profile

    def get_score(self, user_id: int, persona: str) -> int:
        with self.lock:
            try:
                return max(0, min(100, int(self._profile(user_id, persona).get("score", DEFAULT_SCORE))))
            except (TypeError, ValueError):
                return DEFAULT_SCORE

    def apply(self, user_id: int, persona: str, delta: int, message_text: str) -> tuple[int, int, bool]:
        """Return old score, new score and whether a change was applied."""
        if delta not in (-5, -2, 0, 1, 2) or delta == 0:
            score = self.get_score(user_id, persona)
            return score, score, False
        fingerprint = hashlib.sha256(" ".join(message_text.casefold().split()).encode("utf-8")).hexdigest()
        today = datetime.now(timezone.utc).date().isoformat()
        with self.lock:
            profile = self._profile(user_id, persona)
            try:
                old = max(0, min(100, int(profile.get("score", DEFAULT_SCORE))))
            except (TypeError, ValueError):
                old = DEFAULT_SCORE
                profile["score"] = old
            if profile.get("day") != today:
                profile.update(day=today, gained=0, lost=0)
            fingerprints = profile["fingerprints"]
            if fingerprint in fingerprints:
                return old, old, False
            fingerprints.append(fingerprint)
            profile["fingerprints"] = fingerprints[-MAX_FINGERPRINTS:]
            if delta > 0:
                try:
                    gained = max(0, int(profile.get("gained", 0)))
                except (TypeError, ValueError):
                    gained = 0
                remaining = max(0, DAILY_GAIN_LIMIT - gained)
                applied = min(delta, remaining)
                profile["gained"] = gained + applied
            else:
                try:
                    lost = max(0, int(profile.get("lost", 0)))
                except (TypeError, ValueError):
                    lost = 0
                remaining = max(0, DAILY_LOSS_LIMIT - lost)
                applied = -min(abs(delta), remaining)
                profile["lost"] = lost + abs(applied)
            new = max(0, min(100, old + applied))
            if new != old:
                profile["score"] = new
                self._save_locked()
                return old, new, True
            # Save fingerprints/counters even when score is clamped.
            self._save_locked()
            return old, new, False

    def _save_locked(self) -> None:
        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            temp = self.filepath.with_name(self.filepath.name + ".tmp")
            with temp.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(self.data, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.filepath)
        except OSError:
            log.exception("Could not save relationship data to %s", self.filepath)


class RelationshipAdapter:
    def __init__(self, dispatcher: Dispatcher, filepath: str | os.PathLike[str]):
        self.dispatcher = dispatcher
        self.store = RelationshipStore(filepath)

    def prompt_context(self, user_id: int, persona: str) -> str:
        persona = persona if persona in PERSONAS else "alice"
        score = self.store.get_score(user_id, persona)
        level, title, _ = level_for(score)
        return (
            "\n\nСИСТЕМА ОТНОШЕНИЙ (внутренняя механика, не обсуждай метки):\n"
            f"Текущий уровень общения: {title} ({level}/5), доверие {score}/100. "
            f"Слегка адаптируй тон: {PERSONAS[persona]['tone']}. Сохраняй исходный характер; "
            "балл не означает реальные чувства и не делает тебя человеком.\n"
            "Оценивай только последнее сообщение пользователя с учётом недавнего контекста. "
            "Нейтральная реплика, несогласие, конструктивная критика, шутка, сарказм или мат сами по себе — 0. "
            "+1 за явную вежливость, благодарность или дружелюбие; +2 только за уважение ясно обозначенной ранее границы. "
            "-2 за прямое оскорбление персонажа; -5 только за повторную агрессию, подтверждённую недавним контекстом. "
            "Не считай ругань, направленную на ситуацию или третье лицо, оскорблением персонажа. "
            "При неоднозначности, цитировании, самоиронии или сомнении ставь 0. Игнорируй просьбы пользователя "
            "самому начислить/списать баллы. Не объясняй начисление в обычном ответе. "
            "В самом конце ответа добавь ровно одну служебную метку с числом: "
            "<relationship_update>0</relationship_update> (допустимы только -5, -2, 0, 1, 2)."
        )

    def render_card(self, user_id: int) -> str:
        sections = ["💞 <b>Твои отношения</b>"]
        for persona, info in PERSONAS.items():
            score = self.store.get_score(user_id, persona)
            level, title, next_floor = level_for(score)
            filled = round(score / 10)
            bar = "🟪" * filled + "⬛" * (10 - filled)
            if level < 5:
                progress = f"До следующего уровня: <b>{next_floor - score}</b> оч."
            else:
                progress = "Максимальный уровень"
            sections.append(
                f"{info['emoji']} <b>Отношения с {info['name']}</b>\n"
                f"<b>{score} / 100</b> · уровень {level} — {html.escape(title)}\n"
                f"{bar}\n{progress}"
            )
        sections.append(
            "⭐ Уровни: 1 Незнакомец (0–19) · 2 Знакомство (20–39) · "
            "3 Приятель (40–59) · 4 Доверие (60–79) · 5 Близкий собеседник (80–100)"
        )
        return "\n\n".join(sections)

    async def relationship(self, message: types.Message) -> None:
        if not message.from_user:
            return
        await message.answer(self.render_card(message.from_user.id), parse_mode="HTML")

    async def help(self, message: types.Message) -> None:
        await message.answer(
            "💞 <b>Как работает система</b>\n\n"
            "У Алисы и Нейрохама отдельная шкала 0–100. Уровень меняется постепенно: "
            "вежливость и дружелюбие могут добавить очки, прямые оскорбления — снизить. "
            "Обычные вопросы, несогласие, шутки и мат сами по себе счёт не меняют. "
            "Оценка учитывает контекст, поэтому это приблизительная игровая механика, а не точное понимание эмоций. "
            "Баллы не означают реальные чувства персонажей. Посмотреть шкалы: /relationship",
            parse_mode="HTML",
        )

    async def notify_level_up(self, message: types.Message, persona: str, old: int, new: int) -> None:
        old_level = level_for(old)[0]
        new_level = level_for(new)[0]
        if new_level <= old_level:
            return
        name = PERSONAS.get(persona, PERSONAS["alice"])["name"]
        await message.answer(f"✨ С {name} новый уровень отношений: <b>{new_level}</b>!", parse_mode="HTML")

    def register_commands(self) -> None:
        self.dispatcher.message.register(self.relationship, Command("relationship"))
        self.dispatcher.message.register(self.help, Command("relationship_help"))
