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

from .seed import seed_database, sync_promo_conditions

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
    status TEXT NOT NULL DEFAULT 'new',
    promo_code TEXT NOT NULL DEFAULT '',
    discount_percent INTEGER NOT NULL DEFAULT 0,
    total_price INTEGER NOT NULL DEFAULT 0,
    history TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reminders (
    chat_id INTEGER PRIMARY KEY,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    lead_created INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_courses_language ON courses(language);
CREATE INDEX IF NOT EXISTS idx_courses_price ON courses(price);
CREATE INDEX IF NOT EXISTS idx_leads_contact ON leads(contact);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at);
"""

# Витрина для менеджера: одна строка = одна заявка со всеми деньгами.
# В ``leads`` лежит course_id, и смотреть цены «глазами» через SELECT * неудобно:
# приходится помнить курс в голове или делать JOIN. Вьюха отдаёт название курса,
# базовую цену, скидку в рублях и итог — видно и скидку, и сумму к оплате.
LEADS_VIEW = """
CREATE VIEW IF NOT EXISTS lead_details AS
SELECT
    l.id,
    l.created_at,
    l.name,
    l.contact,
    l.course_id,
    c.title AS course,
    c.price AS base_price,
    l.promo_code,
    l.discount_percent,
    c.price - l.total_price AS discount_rub,
    l.total_price,
    l.status,
    l.preferred_time,
    l.comment,
    l.history
FROM leads l
JOIN courses c ON c.id = l.course_id
"""

# Единый формат времени в базе: UTC, секунды. Так строки сравниваются как даты.
_TS = "%Y-%m-%d %H:%M:%S"

# Сколько последних строк хранить в журнале заявки (колонка leads.history).
HISTORY_MAX_LINES = 20

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
        """Создать схему, заполнить пустую базу каталогом и обновить условия акций."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            self._migrate(connection)
            connection.executescript(LEADS_VIEW)
            count = connection.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
            if count == 0:
                seed_database(connection)
                logger.info("База создана и заполнена стартовым каталогом: %s", self.path)
            else:
                sync_promo_conditions(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Добавить колонки заявки, появившиеся после выпуска первой версии схемы.

        ``CREATE TABLE IF NOT EXISTS`` не досоздаёт поля в существующей таблице,
        поэтому старые базы обновляются точечно и сохранённые заявки не теряются.
        """
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(leads)").fetchall()
        }
        for name, definition in (
            ("promo_code", "TEXT NOT NULL DEFAULT ''"),
            ("discount_percent", "INTEGER NOT NULL DEFAULT 0"),
            ("total_price", "INTEGER NOT NULL DEFAULT 0"),
            ("history", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE leads ADD COLUMN {name} {definition}")

        # Заявки из старой базы не имеют итога: проставляем стоимость курса,
        # иначе в таблице у всех прежних записей красуется 0 ₽.
        connection.execute(
            """
            UPDATE leads SET total_price = (
                SELECT c.price FROM courses c WHERE c.id = leads.course_id
            ) WHERE total_price = 0
            """
        )

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
        promo_code: str = "",
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

            promo = self._promo_row(connection, promo_code)
            code = promo["code"] if promo else ""
            percent = int(promo["discount_percent"]) if promo else 0
            total = self._lead_total(connection, course_id, percent)

            cursor = connection.execute(
                """
                INSERT INTO leads (
                    created_at, name, contact, course_id, preferred_time, comment, status,
                    promo_code, discount_percent, total_price
                ) VALUES (?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
                """,
                (
                    created_at,
                    name.strip(),
                    contact.strip(),
                    course_id,
                    preferred_time.strip(),
                    comment.strip(),
                    code,
                    percent,
                    total,
                ),
            )
            note = f"Заявка создана: курс {course_id}, {total} ₽"
            if code:
                note += f", скидка {percent}% по промокоду {code}"
            self.append_history(connection, int(cursor.lastrowid), note)
            row = connection.execute(
                "SELECT * FROM leads WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()

        return dict(row), True

    @staticmethod
    def _promo_row(connection: sqlite3.Connection, code: str) -> sqlite3.Row | None:
        """Найти активный промокод внутри открытого соединения."""
        normalized = (code or "").strip().upper()
        if not normalized:
            return None
        return connection.execute(
            """
            SELECT code, discount_percent FROM promos
            WHERE upper(code) = ? AND active = 1
            """,
            (normalized,),
        ).fetchone()

    @staticmethod
    def _lead_total(
        connection: sqlite3.Connection,
        course_id: int,
        discount_percent: int,
    ) -> int:
        """Цена заявки: стоимость курса минус скидка применённого промокода."""
        row = connection.execute(
            "SELECT price FROM courses WHERE id = ?", (course_id,)
        ).fetchone()
        price = int(row["price"]) if row else 0
        return price - round(price * int(discount_percent) / 100)

    @staticmethod
    def append_history(
        connection: sqlite3.Connection,
        lead_id: int,
        note: str,
    ) -> None:
        """Дописать строку в журнал заявки.

        Журнал живёт в колонке ``leads.history``, чтобы менеджер видел историю
        работы с клиентом прямо в таблице: что создали, какую скидку поставили,
        что исправили и когда отменили. Без него заявка выглядит так, будто её
        ни разу не меняли, — именно на это жаловался тест пользователя.
        """
        stamp = datetime.now().strftime("%d.%m %H:%M")
        row = connection.execute(
            "SELECT history FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        lines = [line for line in (row["history"] if row else "").splitlines() if line]
        lines.append(f"{stamp} {note.strip()}"[:300])
        connection.execute(
            "UPDATE leads SET history = ? WHERE id = ?",
            ("\n".join(lines[-HISTORY_MAX_LINES:]), lead_id),
        )

    def find_contact_key(self, contact: str) -> str | None:
        """Найти реальный ключ контакта в базе по тому, что назвал клиент.

        Клиенты часто пишут несколько контактов сразу: «olga@mail.ru, +79055553322».
        Точное совпадение тогда не находит заявку, и бот отвечает «заявок нет».
        Поэтому сначала пробуем точное совпадение, а затем ищем запись, которая
        содержит названный контакт как подстроку.
        """
        contact_key = (contact or "").strip().lower()
        if len(contact_key) < 5:
            return None
        with self._connect() as connection:
            exact = connection.execute(
                "SELECT lower(contact) AS key FROM leads WHERE lower(contact) = ? LIMIT 1",
                (contact_key,),
            ).fetchone()
            if exact is not None:
                return str(exact["key"])
            partial = connection.execute(
                """
                SELECT lower(contact) AS key FROM leads
                WHERE instr(lower(contact), ?) > 0 ORDER BY id DESC LIMIT 1
                """,
                (contact_key,),
            ).fetchone()
        return str(partial["key"]) if partial else None

    def list_leads(self, contact: str, limit: int = 10) -> list[dict[str, Any]]:
        """Вернуть последние заявки клиента по контакту.

        Нужен, чтобы бот отвечал о составе заказа из базы, а не по памяти диалога:
        в длинном разговоре модель иначе путает курсы между заявками.
        """
        contact_key = self.find_contact_key(contact)
        if not contact_key:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT l.id, l.created_at, l.name, l.contact, l.course_id, c.title,
                       c.price, l.preferred_time, l.comment, l.status,
                       l.promo_code, l.discount_percent, l.total_price, l.history
                FROM leads l
                JOIN courses c ON c.id = l.course_id
                WHERE lower(contact) = ?
                ORDER BY l.id DESC
                LIMIT ?
                """,
                (contact_key, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_lead(
        self,
        lead_id: int,
        comment: str | None = None,
        preferred_time: str | None = None,
        status: str | None = None,
        course_id: int | None = None,
        promo_code: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Изменить заявку: курс, промокод, комментарий, время или статус.

        Возвращает ``(заявка, ошибка)``. ``None`` в поле означает «не менять»,
        поэтому частичное обновление не затирает сохранённые значения.
        ``promo_code=""`` — снять скидку с заявки.

        Каждое изменение попадает в ``leads.history``: в базе остаётся видно,
        что именно и когда исправили по просьбе клиента.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT l.*, c.title, c.price FROM leads l
                JOIN courses c ON c.id = l.course_id
                WHERE l.id = ?
                """,
                (lead_id,),
            ).fetchone()
            if row is None:
                return None, "Заявка с таким ID не найдена. Уточните её через get_lead_details."

            fields: dict[str, Any] = {}
            notes: list[str] = []

            if comment is not None and comment.strip() != row["comment"]:
                fields["comment"] = comment.strip()[:500]
                notes.append("комментарий обновлён")
            if preferred_time is not None and preferred_time.strip() != row["preferred_time"]:
                fields["preferred_time"] = preferred_time.strip()[:120]
                notes.append(f"удобное время: {preferred_time.strip()[:120] or 'не указано'}")
            if status is not None and status.strip() != row["status"]:
                fields["status"] = status.strip()[:32]
                notes.append(f"статус: {status.strip()[:32]}")

            target_course = row["course_id"]
            if course_id is not None and int(course_id) != row["course_id"]:
                if self._course_row(connection, int(course_id)) is None:
                    return None, "Новый курс не найден в каталоге. Уточните его через search_courses."
                target_course = int(course_id)
                fields["course_id"] = target_course
                notes.append(f"курс заменён на {target_course}")

            percent = int(row["discount_percent"])
            code = row["promo_code"]
            if promo_code is not None:
                promo = self._promo_row(connection, promo_code)
                if promo is None and promo_code.strip():
                    return None, (
                        f"Промокод «{promo_code.strip()}» не найден или неактивен."
                    )
                code = promo["code"] if promo else ""
                percent = int(promo["discount_percent"]) if promo else 0
                fields["promo_code"] = code
                fields["discount_percent"] = percent
                notes.append(
                    f"скидка {percent}% по промокоду {code}" if code else "скидка снята"
                )

            if fields:
                fields["total_price"] = self._lead_total(connection, target_course, percent)
                assignments = ", ".join(f"{name} = ?" for name in fields)
                connection.execute(
                    f"UPDATE leads SET {assignments} WHERE id = ?",
                    [*fields.values(), lead_id],
                )
                if notes:
                    self.append_history(
                        connection,
                        lead_id,
                        "Исправлено: " + "; ".join(notes)
                        + f". Итог {fields['total_price']} ₽",
                    )
                row = connection.execute(
                    """
                    SELECT l.*, c.title, c.price FROM leads l
                    JOIN courses c ON c.id = l.course_id
                    WHERE l.id = ?
                    """,
                    (lead_id,),
                ).fetchone()

        return dict(row), None

    def cancel_lead(self, lead_id: int) -> tuple[bool, str | None]:
        """Отменить заявку по ID.

        Заявку не удаляем из таблицы: менеджеру нужен след от отказа клиента.
        Статус ставится ``cancelled``, в журнал добавляется причина.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, status FROM leads WHERE id = ?", (lead_id,)
            ).fetchone()
            if row is None:
                return False, "Заявка с таким ID не найдена. Уточните её через get_lead_details."
            connection.execute(
                "UPDATE leads SET status = 'cancelled' WHERE id = ?", (lead_id,)
            )
            self.append_history(connection, lead_id, "заявка отменена по просьбе клиента")
        return True, None

    @staticmethod
    def _course_row(
        connection: sqlite3.Connection, course_id: int
    ) -> sqlite3.Row | None:
        """Найти курс внутри открытого соединения, чтобы не открывать второе."""
        return connection.execute(
            "SELECT id, title, price FROM courses WHERE id = ?", (int(course_id),)
        ).fetchone()

    def get_lead(self, lead_id: int) -> dict[str, Any] | None:
        """Вернуть заявку по ID вместе с названием и базовой ценой курса."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT l.*, c.title, c.price
                FROM leads l JOIN courses c ON c.id = l.course_id
                WHERE l.id = ?
                """,
                (lead_id,),
            ).fetchone()
        return dict(row) if row else None

    def apply_promo_to_contact(
        self,
        contact: str,
        promo_code: str,
        course_id: int = 0,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Применить промокод ровно к одной заявке клиента и пересчитать цены.

        Возвращает ``(обновлённые заявки, ошибку)``. Сначала у всех заявок
        контакта снимается скидка и цены возвращаются к базовым, затем скидка
        начисляется выбранной заявке. Так заказ не может получить скидку на все
        курсы сразу — правило центра «промокод на один курс» обеспечивается
        на уровне базы, а не уговорами модели.

        ``course_id = 0`` означает «самый дорогой курс из заявок»: так скидка
        даёт наибольшую выгоду клиенту.
        """
        if not (contact or "").strip():
            return [], "Укажите контакт, по которому искать заявки."
        contact_key = self.find_contact_key(contact)
        if not contact_key:
            return [], "Заявок с таким контактом нет. Уточните контакт или создайте заявку."

        with self._connect() as connection:
            promo = self._promo_row(connection, promo_code)
            if promo is None:
                return [], f"Промокод «{(promo_code or '').strip()}» не найден или неактивен."

            rows = connection.execute(
                """
                SELECT l.*, c.price FROM leads l
                JOIN courses c ON c.id = l.course_id
                WHERE lower(contact) = ?
                ORDER BY l.id
                """,
                (contact_key,),
            ).fetchall()

            code = promo["code"]
            percent = int(promo["discount_percent"])

            # Шаг 1: снять скидку со всех заявок — иначе она «залипнет» на нескольких.
            connection.execute(
                """
                UPDATE leads SET promo_code = '', discount_percent = 0
                WHERE lower(contact) = ?
                """,
                (contact_key,),
            )
            for row in rows:
                connection.execute(
                    "UPDATE leads SET total_price = ? WHERE id = ?",
                    (int(row["price"]), row["id"]),
                )

            # Шаг 2: начислить скидку выбранной заявке.
            target = None
            if course_id:
                target = next((row for row in rows if row["course_id"] == int(course_id)), None)
                if target is None:
                    return [], "Курс не входит в заявки этого клиента."
            else:
                target = max(rows, key=lambda row: (int(row["price"]), -row["id"]))

            total = self._lead_total(connection, target["course_id"], percent)
            connection.execute(
                """
                UPDATE leads
                SET promo_code = ?, discount_percent = ?, total_price = ?
                WHERE id = ?
                """,
                (code, percent, total, target["id"]),
            )
            # Журнал: видно, что скидка переехала, а не «применилась сама».
            for row in rows:
                if row["id"] == target["id"]:
                    self.append_history(
                        connection,
                        int(row["id"]),
                        f"скидка {percent}% по промокоду {code}, итог {total} ₽",
                    )
                elif row["promo_code"]:
                    self.append_history(
                        connection,
                        int(row["id"]),
                        f"скидка {row['discount_percent']}% снята (промокод на один курс)",
                    )

            updated = connection.execute(
                """
                SELECT l.*, c.title, c.price FROM leads l
                JOIN courses c ON c.id = l.course_id
                WHERE lower(l.contact) = ?
                ORDER BY l.id
                """,
                (contact_key,),
            ).fetchall()

        return [dict(row) for row in updated], None

    # --- Напоминания о заявке -------------------------------------------------
    #
    # Расписание живёт в базе, а не в памяти бота: после перезапуска бота
    # напоминания не теряются, а сервер остаётся единственным источником правды.
    # Состояния: pending — ждёт отправки, sent — уже отправлено,
    # cancelled — отменено (создана заявка или диалог очищен).

    def schedule_reminder(self, chat_id: int, delay_seconds: int) -> dict[str, Any]:
        """Запланировать напоминание через ``delay_seconds`` после активности.

        Повторный вызов перезаписывает время: таймер отсчитывается от последнего
        сообщения пользователя. Если заявка уже создана или напоминание в этом
        диалоге отправлено, расписание не меняется.
        """
        now = datetime.now(timezone.utc)
        due_at = (now + timedelta(seconds=max(0, int(delay_seconds)))).strftime(_TS)
        created_at = now.strftime(_TS)

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE chat_id = ?", (chat_id,)
            ).fetchone()

            if row is not None and (row["lead_created"] or row["status"] == "sent"):
                return dict(row)

            connection.execute(
                """
                INSERT INTO reminders (chat_id, due_at, status, created_at, sent_at, lead_created)
                VALUES (?, ?, 'pending', ?, NULL, 0)
                ON CONFLICT(chat_id) DO UPDATE SET
                    due_at = excluded.due_at,
                    status = 'pending',
                    created_at = excluded.created_at,
                    sent_at = NULL
                """,
                (chat_id, due_at, created_at),
            )
            row = connection.execute(
                "SELECT * FROM reminders WHERE chat_id = ?", (chat_id,)
            ).fetchone()

        return dict(row)

    def cancel_reminder(self, chat_id: int, reason: str = "cancelled") -> bool:
        """Отменить ожидающее напоминание чата. Возвращает ``True``, если что-то сняли."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders SET status = ?
                WHERE chat_id = ? AND status = 'pending'
                """,
                (reason, chat_id),
            )
            cancelled = cursor.rowcount > 0
        return cancelled

    def mark_lead_created(self, chat_id: int) -> None:
        """Запомнить, что заявка создана: напоминание больше не нужно."""
        now = datetime.now(timezone.utc).strftime(_TS)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reminders (chat_id, due_at, status, created_at, sent_at, lead_created)
                VALUES (?, ?, 'cancelled', ?, NULL, 1)
                ON CONFLICT(chat_id) DO UPDATE SET
                    status = 'cancelled',
                    lead_created = 1
                """,
                (chat_id, now, now),
            )

    def reset_reminder(self, chat_id: int) -> None:
        """Забыть состояние чата, чтобы напоминание снова стало возможным."""
        with self._connect() as connection:
            connection.execute("DELETE FROM reminders WHERE chat_id = ?", (chat_id,))

    def due_reminders(self, limit: int = 20) -> list[dict[str, Any]]:
        """Вернуть напоминания, время которых наступило."""
        now = datetime.now(timezone.utc).strftime(_TS)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chat_id, due_at FROM reminders
                WHERE status = 'pending' AND lead_created = 0 AND due_at <= ?
                ORDER BY due_at LIMIT ?
                """,
                (now, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_reminder_sent(self, chat_id: int) -> bool:
        """Отметить напоминание отправленным. ``False`` — его уже сняли."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders SET status = 'sent', sent_at = ?
                WHERE chat_id = ? AND status = 'pending' AND lead_created = 0
                """,
                (datetime.now(timezone.utc).strftime(_TS), chat_id),
            )
            marked = cursor.rowcount > 0
        return marked

    def reminder_status(self, chat_id: int) -> dict[str, Any] | None:
        """Вернуть состояние напоминания чата или ``None``, если его нет."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return dict(row) if row else None
