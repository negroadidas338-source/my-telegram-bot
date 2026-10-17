"""
Модуль памяти для бота Соньки.
Хранит:
- Историю чатов
- Факты о каждом пользователе (отдельно!)
- Связи между пользователями (кто кому кто)
- Имена пользователей
"""

import json
import os
import time
import threading
from typing import Dict, List, Optional

MEMORY_FILE = os.path.join(os.path.dirname(__file__), "memory.json")
MAX_HISTORY_PER_CHAT = 20
MAX_CHATS = 200
AUTOSAVE_INTERVAL = 10


class Memory:
    def __init__(self, filepath: str = MEMORY_FILE):
        self.filepath = filepath
        self.lock = threading.Lock()
        self._dirty = False

        # Структура данных
        self.chats: Dict[str, List[dict]] = {}        # chat_id -> история сообщений
        self.users: Dict[str, dict] = {}              # user_id -> данные о юзере
        self.last_seen: Dict[str, float] = {}         # chat_id -> время последнего сообщения

        self._load()
        self._start_autosave()

    # ============================================================
    #  ИСТОРИЯ ЧАТОВ
    # ============================================================
    def get_history(self, chat_id: int) -> List[dict]:
        key = str(chat_id)
        with self.lock:
            return list(self.chats.get(key, []))

    def add_message(self, chat_id: int, role: str, content: str):
        key = str(chat_id)
        with self.lock:
            if key not in self.chats:
                self.chats[key] = []
            self.chats[key].append({"role": role, "content": content})
            if len(self.chats[key]) > MAX_HISTORY_PER_CHAT:
                self.chats[key] = self.chats[key][-MAX_HISTORY_PER_CHAT:]
            self.last_seen[key] = time.time()
            self._dirty = True

    def clear_history(self, chat_id: int):
        key = str(chat_id)
        with self.lock:
            self.chats.pop(key, None)
            self._dirty = True

    # ============================================================
    #  ДАННЫЕ О ПОЛЬЗОВАТЕЛЯХ (имя, факты, связи)
    # ============================================================
    def _ensure_user(self, user_id: int) -> dict:
        """Гарантирует, что запись о юзере существует."""
        key = str(user_id)
        if key not in self.users:
            self.users[key] = {
                "name": None,
                "facts": [],
                "relations": {},   # user_id другого юзера -> описание связи
                "created": time.time()
            }
        return self.users[key]

    def set_name(self, user_id: int, name: str):
        """Устанавливает имя пользователя."""
        with self.lock:
            user = self._ensure_user(user_id)
            user["name"] = name
            self._dirty = True

    def get_name(self, user_id: int) -> Optional[str]:
        """Возвращает имя пользователя (или None)."""
        key = str(user_id)
        with self.lock:
            return self.users.get(key, {}).get("name")

    def remember_fact(self, user_id: int, fact: str):
        """Запоминает факт о пользователе."""
        with self.lock:
            user = self._ensure_user(user_id)
            if fact not in user["facts"]:
                user["facts"].append(fact)
                if len(user["facts"]) > 30:
                    user["facts"] = user["facts"][-30:]
                self._dirty = True

    def get_user_facts(self, user_id: int) -> List[str]:
        """Возвращает все факты о пользователе."""
        key = str(user_id)
        with self.lock:
            return list(self.users.get(key, {}).get("facts", []))

    def set_relation(self, user_id: int, other_user_id: int, relation: str):
        """
        Устанавливает связь между двумя пользователями.
        Пример: set_relation(123, 456, "девушка") — юзер 123 считает 456 своей девушкой.
        """
        with self.lock:
            user = self._ensure_user(user_id)
            user["relations"][str(other_user_id)] = relation
            self._dirty = True

    def get_relation(self, user_id: int, other_user_id: int) -> Optional[str]:
        """Возвращает, кем является other_user_id для user_id."""
        key = str(user_id)
        other_key = str(other_user_id)
        with self.lock:
            return self.users.get(key, {}).get("relations", {}).get(other_key)

    def get_all_relations(self, user_id: int) -> Dict[str, str]:
        """Возвращает все связи пользователя."""
        key = str(user_id)
        with self.lock:
            return dict(self.users.get(key, {}).get("relations", {}))

    def forget_user(self, user_id: int):
        """Забывает всё о пользователе."""
        key = str(user_id)
        with self.lock:
            self.users.pop(key, None)
            # Удаляем связи, ведущие к этому юзеру
            for uid, data in self.users.items():
                data.get("relations", {}).pop(key, None)
            self._dirty = True

    def get_user_info(self, user_id: int) -> dict:
        """Полная информация о пользователе."""
        key = str(user_id)
        with self.lock:
            return dict(self.users.get(key, {}))

    def list_all_users(self) -> Dict[str, dict]:
        """Список всех известных пользователей."""
        with self.lock:
            return dict(self.users)

    # ============================================================
    #  СОХРАНЕНИЕ / ЗАГРУЗКА
    # ============================================================
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
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ Ошибка загрузки памяти: {e}. Начинаем с чистой.")
            self.chats = {}
            self.users = {}
            self.last_seen = {}

    def save(self, force: bool = False):
        with self.lock:
            if not force and not self._dirty:
                return
            if len(self.chats) > MAX_CHATS:
                sorted_chats = sorted(self.last_seen.items(), key=lambda x: x[1])
                to_remove = [k for k, _ in sorted_chats[:-MAX_CHATS]]
                for k in to_remove:
                    self.chats.pop(k, None)
                    self.last_seen.pop(k, None)

            data = {
                "chats": self.chats,
                "users": self.users,
                "last_seen": self.last_seen,
                "saved_at": time.time()
            }
            try:
                tmp_path = self.filepath + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.filepath)
                self._dirty = False
            except IOError as e:
                print(f"⚠️ Ошибка сохранения памяти: {e}")

    def _start_autosave(self):
        def _worker():
            while True:
                time.sleep(AUTOSAVE_INTERVAL)
                try:
                    self.save()
                except Exception as e:
                    print(f"⚠️ Ошибка автосохранения: {e}")
        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def shutdown(self):
        self.save(force=True)
        print("💾 Память сохранена.")


memory = Memory()