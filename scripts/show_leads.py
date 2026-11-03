"""Отчёт по заявкам для менеджера: курс, цена, скидка, итог, статус.

Запуск из корня проекта:

    python scripts/show_leads.py
    python scripts/show_leads.py --contact olga

Скрипт только читает базу: заявки не создаёт и не меняет. Вьюха
``lead_details`` уже содержит название курса и скидку в рублях, поэтому
отчёт не нужно собирать руками из ``course_id``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.config import load_settings

COLUMNS = (
    ("id", "№", 4),
    ("name", "Имя", 12),
    ("course", "Курс", 24),
    ("base_price", "Цена", 8),
    ("promo_code", "Промокод", 10),
    ("discount_percent", "Скидка%", 8),
    ("discount_rub", "Скидка₽", 9),
    ("total_price", "Итог", 9),
    ("status", "Статус", 10),
)


def money(value: object) -> str:
    """Цена в виде «29 990»: тонкий разделитель разрядов читается в консоли."""
    return f"{int(value or 0):,}".replace(",", " ")


def render(rows: list[sqlite3.Row]) -> str:
    """Собрать текстовую таблицу: заголовок, строки, итог по заказам."""
    header = "  ".join(title.ljust(width) for _key, title, width in COLUMNS)
    lines = [header, "-" * len(header)]
    for row in rows:
        cells = []
        for key, _title, width in COLUMNS:
            if key in {"name", "course", "promo_code", "status"}:
                text = str(row[key] or "")
            else:
                text = money(row[key])
            cells.append(text.ljust(width))
        lines.append("  ".join(cells))

    total = sum(row["total_price"] for row in rows if row["status"] != "cancelled")
    discount = sum(row["discount_rub"] for row in rows if row["status"] != "cancelled")
    lines.append("-" * len(header))
    lines.append(
        f"Заявок: {len(rows)} | Сумма без отменённых: {money(total)} ₽ | Скидок: {money(discount)} ₽"
    )
    return "\n".join(lines)


def main() -> int:
    """Напечатать заявки, при необходимости — только одного клиента."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Показать заявки с ценами и скидками")
    parser.add_argument("--contact", default="", help="фильтр по контакту или имени")
    args = parser.parse_args()

    db_path = load_settings().db_path
    if not Path(db_path).exists():
        print(f"База не найдена: {db_path}. Сначала запустите MCP-сервер.")
        return 1

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    query = "SELECT * FROM lead_details"
    params: tuple[object, ...] = ()
    if args.contact:
        query += " WHERE lower(contact) LIKE ? OR lower(name) LIKE ?"
        needle = f"%{args.contact.lower()}%"
        params = (needle, needle)
    rows = connection.execute(f"{query} ORDER BY id", params).fetchall()
    connection.close()

    if not rows:
        print("Заявок нет.")
        return 0
    print(render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
