"""MCP-инструменты, которые доступны Telegram-боту.

Каждый инструмент возвращает компактную JSON-строку. Это снижает расход токенов
и оставляет модели однозначные поля вместо длинных текстовых простыней.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .app import db, mcp
from .pricing import INSTALLMENT_MARKUP, PricingError, calculate_price


def _json(payload: dict[str, Any]) -> str:
    """Сериализовать ответ без пробелов и экранирования кириллицы."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _error(message: str, **extra: Any) -> str:
    """Единый формат ошибки, который модель умеет объяснять пользователю."""
    return _json({"ok": False, "error": message, **extra})


@mcp.tool()
def search_courses(language: str = "", level: str = "", max_price: int = 0) -> str:
    """Найти курсы по языку, уровню и бюджету.

    Args:
        language: Язык или его часть: «английский», «english», «немецкий».
        level: Уровень или его часть: «A1», «B2», «beginner», «средний».
        max_price: Максимальная цена в рублях; 0 — без ограничения.

    Возвращает компактный список курсов с id, ценой и длительностью.
    """
    courses = db.search_courses(language=language, level=level, max_price=max_price)
    result: dict[str, Any] = {"ok": True, "count": len(courses), "courses": courses}
    if not courses:
        result["hint"] = "Подходящих курсов нет. Попробуйте изменить язык, уровень или бюджет."
    return _json(result)


@mcp.tool()
def get_course_details(course_id: int) -> str:
    """Показать полную карточку курса: описание, программу, преподавателя и группы.

    Args:
        course_id: ID курса из результата search_courses.
    """
    course = db.get_course(course_id)
    if course is None:
        return _error(
            "Курс с таким ID не найден. Сначала найдите курс через search_courses."
        )
    return _json({"ok": True, "course": course})


@mcp.tool()
def calculate_course_price(
    course_id: int,
    promo_code: str = "",
    installment_months: int = 0,
) -> str:
    """Рассчитать цену курса со скидкой и рассрочкой.

    Args:
        course_id: ID курса.
        promo_code: Промокод (например, START10); пустая строка — без скидки.
        installment_months: 0 — полная оплата, 3, 6 или 12 месяцев — рассрочка.

    Возвращает базовую цену, скидку, итог и ежемесячный платёж.
    """
    course = db.get_course(course_id)
    if course is None:
        return _error("Курс с таким ID не найден. Сначала найдите курс через search_courses.")

    promo = None
    discount = 0
    if promo_code.strip():
        promo = db.get_promo(promo_code)
        if promo is None:
            active = [item["code"] for item in db.list_promotions()]
            return _error(
                f"Промокод «{promo_code.strip()}» не найден или неактивен.",
                active_promos=active,
            )
        discount = int(promo["discount_percent"])

    try:
        breakdown = calculate_price(
            base_price=int(course["price"]),
            discount_percent=discount,
            installment_months=int(installment_months),
        )
    except PricingError as exc:
        return _error(str(exc))

    return _json(
        {
            "ok": True,
            "course_id": course["id"],
            "course_title": course["title"],
            "base_price": breakdown.base_price,
            "promo": promo["code"] if promo else None,
            "discount_percent": breakdown.discount_percent,
            "discount_rub": breakdown.discount_rub,
            "installment_months": breakdown.installment_months,
            "installment_markup_percent": breakdown.installment_markup_percent,
            "installment_options": [
                {"months": months, "markup_percent": markup}
                for months, markup in sorted(INSTALLMENT_MARKUP.items())
            ],
            "total": breakdown.total,
            "monthly_payment": breakdown.monthly_payment,
        }
    )


@mcp.tool()
def list_promotions() -> str:
    """Показать активные промокоды и размер скидки."""
    promotions = db.list_promotions()
    return _json({"ok": True, "count": len(promotions), "promotions": promotions})


@mcp.tool()
def search_faq(query: str, limit: int = 3) -> str:
    """Найти ответ в базе знаний по обучению, оплате, расписанию и формату.

    Args:
        query: Вопрос или ключевые слова пользователя.
        limit: Сколько ответов вернуть, от 1 до 5.
    """
    items = db.search_faq(query, limit=limit)
    result: dict[str, Any] = {"ok": True, "count": len(items), "items": items}
    if not items:
        result["hint"] = "В базе знаний нет подходящего ответа — уточните у менеджера."
    return _json(result)


@mcp.tool()
def create_lead(
    name: str,
    contact: str,
    course_id: int,
    preferred_time: str = "",
    comment: str = "",
) -> str:
    """Создать заявку на курс, когда у пользователя есть имя, контакт и выбранный курс.

    Args:
        name: Имя пользователя.
        contact: Телефон, email или Telegram в свободной форме.
        course_id: ID выбранного курса.
        preferred_time: Удобное время занятий; можно оставить пустым.
        comment: Комментарий или вопрос; можно оставить пустым.
    """
    name = (name or "").strip()
    contact = (contact or "").strip()
    preferred_time = (preferred_time or "").strip()[:120]
    comment = (comment or "").strip()[:500]

    if len(name) < 2 or len(name) > 100:
        return _error("Укажите имя пользователя (от 2 до 100 символов).")
    if len(contact) < 5 or len(contact) > 100:
        return _error("Укажите контакт для связи: телефон, email или Telegram.")
    if not re.search(r"[0-9@]", contact):
        return _error("Контакт должен содержать телефон, email или @username.")
    if not db.course_exists(course_id):
        return _error("Выбранный курс не найден. Сначала уточните курс через search_courses.")

    lead, created = db.create_lead(
        name=name,
        contact=contact,
        course_id=course_id,
        preferred_time=preferred_time,
        comment=comment,
    )
    if lead is None:
        return _error("Не удалось сохранить заявку. Попросите пользователя повторить позже.")

    message = (
        "Заявка создана. Менеджер свяжется с пользователем в рабочее время."
        if created
        else "Такая заявка уже есть — повторно создавать её не нужно."
    )
    return _json(
        {
            "ok": True,
            "created": created,
            "lead_id": lead["id"],
            "status": lead["status"],
            "message": message,
        }
    )
