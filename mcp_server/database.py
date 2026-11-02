"""SQLite-база каталога курсов, заявок и базы знаний.

Один класс ``CourseDatabase`` инкапсулирует схему и все SQL-запросы. MCP-инструменты
работают только через его методы, поэтому валидация и форматирование не дублируются.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from .seed import seed_database

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    language TEXT NOT NULL,
    level TEXT NOT NULL,
    format TEXT NOT NULL,
    weeks INTEGER NOT NULL,
    lessons_per_week INTEGER NOT NULL,
    price INTEGER NOT NULL,
    old_price INTEGER,
    description TEXT NOT NULL,
    audience TEXT NOT NULL,
    teacher TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    UNIQUE (course_id, position)
);

CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    schedule TEXT NOT NULL,
    seats INTEGER NOT NULL,
    format TEXT NOT NULL,
    UNIQUE (course_id, schedule)
);

CREATE TABLE IF NOT EXISTS promos (
    code TEXT PRIMARY KEY,
    discount_percent INTEGER NOT NULL,
    description TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS faq (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    question TEXT NOT NULL UNIQUE,
    answer TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    name TEXT NOT NULL,
    contact TEXT NOT NULL,
    course_id INTEGER NOT NULL REFERENCES courses(id),
    preferred_time TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new'
);

CREATE INDEX IF NOT EXISTS idx_courses_language ON courses(language);
CREATE INDEX IF NOT EXISTS idx_courses_price ON courses(price);
CREATE INDEX IF NOT EXISTS idx_leads_contact ON leads(contact);
"""

# Пользователь может написать язык по-русски или по-английски — приводим к языку в базе.
_LANGUAGE_ALIASES = {
    "english": "английский",
    "eng": "английский",
    "en": "английский",
    "англ": "английский",
    "german": "немецкий",
    "deutsch": "немецкий",
    "ger": "немецкий",
    "de": "немецкий",
    "нем": "немецкий",
    "spanish": "испанский",
    "espanol": "испанский",
    "es": "испанский",
    "исп": "испанский",
    "chinese": "китайский",
    "mandarin": "китайский",
    "zh": "китайский",
    "кит": "китайский",
}

_LEVEL_ALIASES = {
    "beginner": "a1",
    "elementary": "a1",
    "начальный": "a1",
    "новичок": "a1",
    "intermediate": "b1",
    "средний": "b1",
    "upper-intermediate": "b2",
    "upper": "b2",
    "advanced": "c1",
    "продвинутый": "c1",
}

_STOP_WORDS = {
    "как",
    "что",
    "где",
    "когда",
    "зачем",
    "почему",
    "можно",
    "есть",
    "или",
    "для",
    "про",
    "при",
    "вас",
    "ваш",
    "ваши",
    "мне",
    "меня",
    "это",
    "the",
    "and",
    "for",
    "with",
    "you",
    "your",
    "can",
}


def _normalize(text: str) -> str:
    """Привести текст к виду, удобному для регистронезависимого поиска."""
    return (text or "").strip().lower().replace("ё", "е")


def _compact_course(row: sqlite3.Row) -> dict[str, Any]:
    """Короткое представление курса для каталога (меньше токенов в ответе)."""
    return {
        "id": row["id"],
        "title": row["title"],
        "language": row["language"],
        "level": row["level"],
        "price": row["price"],
        "format": row["format"],
        "weeks": row["weeks"],
    }


class CourseDatabase:
    """Обёртка над SQLite с ленивой инициализацией и потокобезопасными запросами."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Открыть соединение на одну операцию и корректно закрыть его."""
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Создать схему и заполнить пустую базу стартовым каталогом."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            count = connection.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
            if count == 0:
                seed_database(connection)
                logger.info("База создана и заполнена стартовым каталогом: %s", self.path)

    def search_courses(
        self,
        language: str = "",
        level: str = "",
        max_price: int = 0,
    ) -> list[dict[str, Any]]:
        """Найти курсы по языку, уровню и максимальной цене.

        Фильтрация выполняется в Python: встроенный SQLite ``lower()`` не умеет
        кириллицу, а каталог маленький, поэтому такой вариант надёжнее и проще.
        """
        language_term = _normalize(language)
        if language_term:
            language_term = _LANGUAGE_ALIASES.get(language_term, language_term)

        level_term = _normalize(level)
        if level_term:
            level_term = _LEVEL_ALIASES.get(level_term, level_term)

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM courses ORDER BY price, id"
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            haystack = _normalize(f"{row['language']} {row['title']}")
            if language_term and language_term not in haystack:
                continue
            if level_term and level_term not in _normalize(
                f"{row['level']} {row['title']}"
            ):
                continue
            if max_price and max_price > 0 and row["price"] > int(max_price):
                continue
            result.append(_compact_course(row))
        return result

    def get_course(self, course_id: int) -> dict[str, Any] | None:
        """Вернуть полную карточку курса с программой и свободными группами."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM courses WHERE id = ?", (course_id,)
            ).fetchone()
            if row is None:
                return None

            modules = connection.execute(
                """
                SELECT position, title, summary
                FROM modules WHERE course_id = ? ORDER BY position
                """,
                (course_id,),
            ).fetchall()
            groups = connection.execute(
                """
                SELECT schedule, seats, format
                FROM groups WHERE course_id = ? AND seats > 0 ORDER BY seats, id
                """,
                (course_id,),
            ).fetchall()

        course = dict(row)
        course["modules"] = [dict(item) for item in modules]
        course["groups"] = [dict(item) for item in groups]
        return course

    def course_exists(self, course_id: int) -> bool:
        """Проверить существование курса без загрузки программы и групп."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM courses WHERE id = ?", (course_id,)
            ).fetchone()
        return row is not None

    def get_promo(self, code: str) -> dict[str, Any] | None:
        """Найти активный промокод без учёта регистра."""
        normalized = (code or "").strip().upper()
        if not normalized:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT code, discount_percent, description
                FROM promos WHERE upper(code) = ? AND active = 1
                """,
                (normalized,),
            ).fetchone()
        return dict(row) if row else None

    def list_promotions(self) -> list[dict[str, Any]]:
        """Вернуть все активные промокоды."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT code, discount_percent, description
                FROM promos WHERE active = 1 ORDER BY discount_percent DESC, code
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def search_faq(self, query: str, limit: int = 3) -> list[dict[str, str]]:
        """Найти ответы базы знаний по ключевым словам запроса.

        Простой взвешенный поиск выбран осознанно: база маленькая, а SQLite FTS
        на разных сборках Python может быть недоступен. Для 12 вопросов этого
        достаточно, и результат остаётся предсказуемым.
        """
        normalized = _normalize(query)
        tokens = [
            token
            for token in re.findall(r"[a-zа-я0-9]+", normalized)
            if len(token) >= 3 and token not in _STOP_WORDS
        ]
        if not tokens:
            return []

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT topic, question, answer FROM faq ORDER BY id"
            ).fetchall()

        scored: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            question = _normalize(row["question"])
            answer = _normalize(row["answer"])
            topic = _normalize(row["topic"])
            score = 0
            for token in tokens:
                if token in question:
                    score += 4
                if token in topic:
                    score += 3
                if token in answer:
                    score += 1
            if normalized and normalized in question:
                score += 5
            if score:
                scored.append((score, row))

        scored.sort(key=lambda item: (-item[0], item[1]["question"]))
        safe_limit = max(1, min(int(limit), 5))
        return [
            {
                "topic": row["topic"],
                "question": row["question"],
                "answer": row["answer"],
            }
            for _, row in scored[:safe_limit]
        ]

    def create_lead(
        self,
        name: str,
        contact: str,
        course_id: int,
        preferred_time: str = "",
        comment: str = "",
    ) -> tuple[dict[str, Any] | None, bool]:
        """Сохранить заявку и вернуть ``(заявка, создана_ли_новая)``.

        Повторная заявка с тем же контактом по тому же курсу в течение 15 минут
        не создаёт дубль: это защищает от двойного нажатия и повторных сообщений.
        """
        if not self.course_exists(course_id):
            return None, False

        now = datetime.now(timezone.utc)
        created_at = now.strftime("%Y-%m-%d %H:%M:%S")
        cutoff = (now - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        contact_key = contact.strip().lower()

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM leads
                WHERE lower(contact) = ? AND course_id = ? AND created_at >= ?
                ORDER BY id DESC LIMIT 1
                """,
                (contact_key, course_id, cutoff),
            ).fetchone()
            if existing is not None:
                return dict(existing), False

            cursor = connection.execute(
                """
                INSERT INTO leads (
                    created_at, name, contact, course_id, preferred_time, comment, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'new')
                """,
                (
                    created_at,
                    name.strip(),
                    contact.strip(),
                    course_id,
                    preferred_time.strip(),
                    comment.strip(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM leads WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()

        return dict(row), True
