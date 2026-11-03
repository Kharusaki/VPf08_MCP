"""MCP-инструменты, которые доступны Telegram-боту.

Каждый инструмент возвращает компактную JSON-строку. Это снижает расход токенов
и оставляет модели однозначные поля вместо длинных текстовых простыней.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .app import db, mcp, settings
from .pricing import (
    INSTALLMENT_MARKUP,
    PricingError,
    calculate_order,
    calculate_price,
)


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
    """Рассчитать цену одного курса со скидкой и рассрочкой.

    Для заказа из нескольких курсов используйте calculate_order_price:
    промокод действует только на один курс, и складывать его между курсами нельзя.

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
    """Показать активные промокоды и размер скидки.

    Поле ``rule`` объясняет главное ограничение: промокод действует на один курс,
    а не на весь заказ. Скидку для заказа из нескольких курсов считайте через
    calculate_order_price, а не складывайте несколько calculate_course_price.
    """
    promotions = db.list_promotions()
    return _json(
        {
            "ok": True,
            "count": len(promotions),
            "rule": "Промокод применяется только к одному курсу в заказе.",
            "promotions": promotions,
        }
    )


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
    promo_code: str = "",
) -> str:
    """Создать заявку на курс, когда у пользователя есть имя, контакт и выбранный курс.

    Args:
        name: Имя пользователя.
        contact: Телефон, email или Telegram в свободной форме.
        course_id: ID выбранного курса.
        preferred_time: Удобное время занятий; можно оставить пустым.
        comment: Комментарий или вопрос; можно оставить пустым.
        promo_code: Промокод, если клиент его назвал; пустая строка — без скидки.
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

    promo = None
    if promo_code.strip():
        promo = db.get_promo(promo_code)
        if promo is None:
            active = [item["code"] for item in db.list_promotions()]
            return _error(
                f"Промокод «{promo_code.strip()}» не найден или неактивен.",
                active_promos=active,
            )

    lead, created = db.create_lead(
        name=name,
        contact=contact,
        course_id=course_id,
        preferred_time=preferred_time,
        comment=comment,
        promo_code=promo["code"] if promo else "",
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
            "promo_code": lead["promo_code"] or None,
            "discount_percent": lead["discount_percent"],
            "total_price": lead["total_price"],
            "message": message,
        }
    )


@mcp.tool()
def calculate_order_price(
    course_ids: list[int],
    promo_code: str = "",
    apply_to_course_id: int = 0,
) -> str:
    """Рассчитать стоимость заказа из нескольких курсов с промокодом.

    Промокод центра действует ровно на один курс, поэтому итог считает сервер:
    так скидка не может случайно примениться ко всем курсам сразу.

    Args:
        course_ids: ID курсов из заказа.
        promo_code: Промокод; пустая строка — считать без скидки.
        apply_to_course_id: Курс, к которому применить скидку.
            0 — самый дорогой курс (наибольшая выгода для клиента).
    """
    ids = [int(course_id) for course_id in (course_ids or [])]
    if not ids:
        return _error("Укажите ID курсов из заказа.")
    if len(set(ids)) != len(ids):
        return _error("В заказе повторяются курсы. Уберите дубликаты.")

    courses: list[tuple[int, str, int]] = []
    for course_id in ids:
        course = db.get_course(course_id)
        if course is None:
            return _error(f"Курс с ID {course_id} не найден. Уточните состав заказа.")
        courses.append((course["id"], course["title"], int(course["price"])))

    percent = 0
    promo = None
    if promo_code.strip():
        promo = db.get_promo(promo_code)
        if promo is None:
            active = [item["code"] for item in db.list_promotions()]
            return _error(
                f"Промокод «{promo_code.strip()}» не найден или неактивен.",
                active_promos=active,
            )
        percent = int(promo["discount_percent"])

    try:
        order = calculate_order(
            courses=courses,
            promo_code=promo["code"] if promo else "",
            discount_percent=percent,
            apply_to_course_id=apply_to_course_id,
        )
    except PricingError as exc:
        return _error(str(exc))

    return _json(
        {
            "ok": True,
            "rule": "Промокод применён только к одному курсу заказа.",
            "promo": order.promo_code,
            "discount_percent": percent,
            "discounted_course_id": order.discounted_course_id,
            "lines": [
                {
                    "course_id": line.course_id,
                    "title": line.title,
                    "base_price": line.base_price,
                    "discount_percent": line.discount_percent,
                    "discount_rub": line.discount_rub,
                    "total": line.total,
                }
                for line in order.lines
            ],
            "total": order.total,
            "options": list(order.options),
            "hint": (
                "Скидку нельзя применять ко всем курсам сразу. Если клиент хочет "
                "другой курс со скидкой — пересчитайте с apply_to_course_id."
            ),
        }
    )


@mcp.tool()
def get_lead_details(contact: str, limit: int = 10) -> str:
    """Показать заявки клиента по контакту: курсы, промокод, статус и итог.

    Вызывайте этот инструмент, когда клиент спрашивает про уже оставленную заявку
    или состав заказа. Отвечать по памяти диалога нельзя: в длинном разговоре
    легко перепутать курсы между заявками.

    Args:
        contact: Телефон, email или Telegram клиента.
        limit: Сколько последних заявок вернуть, от 1 до 20.
    """
    contact = (contact or "").strip()
    if len(contact) < 5:
        return _error("Укажите контакт, по которому искать заявки.")

    leads = db.list_leads(contact, limit=limit)
    result: dict[str, Any] = {
        "ok": True,
        "count": len(leads),
        "leads": [
            {
                "lead_id": lead["id"],
                "course_id": lead["course_id"],
                "title": lead["title"],
                "base_price": lead["price"],
                "promo_code": lead["promo_code"] or None,
                "discount_percent": lead["discount_percent"],
                "total_price": lead["total_price"] or lead["price"],
                "status": lead["status"],
                "preferred_time": lead["preferred_time"],
                "comment": lead["comment"],
                "created_at": lead["created_at"],
                "history": lead["history"],
            }
            for lead in leads
        ],
    }
    if leads:
        result["order_total"] = sum(
            lead["total_price"] or lead["price"] for lead in leads
        )
        result["discounted_courses"] = sum(1 for lead in leads if lead["discount_percent"])
    else:
        result["hint"] = (
            "Заявок с таким контактом нет. Уточните контакт или предложите создать заявку."
        )
    return _json(result)


@mcp.tool()
def update_lead(
    lead_id: int,
    comment: str | None = None,
    preferred_time: str | None = None,
    status: str | None = None,
    course_id: int | None = None,
    promo_code: str | None = None,
) -> str:
    """Изменить заявку: курс, промокод и скидку, комментарий, время или статус.

    Это единственный способ исправить уже созданную заявку. Поля, которые менять
    не нужно, оставляйте ``None`` — они не перезапишутся. Фраза «передам менеджеру»
    без вызова этого инструмента считается невыполненной просьбой клиента.

    Args:
        lead_id: ID заявки из get_lead_details.
        comment: Новый комментарий; None — оставить прежний.
        preferred_time: Новое удобное время; None — оставить прежнее.
        status: Новый статус, например ``new`` или ``needs_call``; None — не менять.
        course_id: На какой курс перенести заявку; None — не менять курс.
        promo_code: Промокод для этой заявки; пустая строка — снять скидку;
            None — оставить промокод без изменений.
    """
    lead = db.get_lead(lead_id)
    if lead is None:
        return _error("Заявка с таким ID не найдена. Сначала получите её через get_lead_details.")

    updated, error = db.update_lead(
        lead_id=lead_id,
        comment=comment,
        preferred_time=preferred_time,
        status=status,
        course_id=course_id,
        promo_code=promo_code,
    )
    if error:
        return _error(error)
    assert updated is not None

    return _json(
        {
            "ok": True,
            "lead_id": updated["id"],
            "course_id": updated["course_id"],
            "title": updated["title"],
            "base_price": updated["price"],
            "promo_code": updated["promo_code"] or None,
            "discount_percent": updated["discount_percent"],
            "total_price": updated["total_price"],
            "status": updated["status"],
            "preferred_time": updated["preferred_time"],
            "comment": updated["comment"],
            "message": "Заявка обновлена и сохранена в базе.",
        }
    )


@mcp.tool()
def cancel_lead(lead_id: int) -> str:
    """Отменить заявку клиента по ID (просьба удалить или отменить запись).

    Заявка помечается статусом ``cancelled`` и остаётся в базе с записью в
    журнале — так менеджер видит отказ клиента. Удалять заявку «на словах»
    нельзя: пока не вызван этот инструмент, запись считается активной.

    Args:
        lead_id: ID заявки из get_lead_details.
    """
    cancelled, error = db.cancel_lead(lead_id)
    if error:
        return _error(error)

    return _json(
        {
            "ok": True,
            "lead_id": lead_id,
            "cancelled": cancelled,
            "status": "cancelled",
            "message": "Заявка отменена и помечена в базе.",
        }
    )


@mcp.tool()
def apply_promo_to_lead(
    contact: str,
    promo_code: str,
    course_id: int = 0,
) -> str:
    """Применить промокод к одной заявке клиента и пересчитать цены в базе.

    Инструмент сам снимает скидку с остальных заявок контакта: правило «промокод
    на один курс» обеспечивается на уровне базы. После изменения заявки всегда
    показывайте клиенту новый итог из ответа.

    Args:
        contact: Контакт клиента из заявки.
        promo_code: Промокод, например START10.
        course_id: Курс, к которому применить скидку; 0 — самый дорогой курс.
    """
    contact = (contact or "").strip()
    promo_code = (promo_code or "").strip()
    if not promo_code:
        return _error("Укажите промокод, который нужно применить.")

    leads, error = db.apply_promo_to_contact(contact, promo_code, course_id)
    if error:
        return _error(error)

    discounted = next((lead for lead in leads if lead["discount_percent"]), None)
    return _json(
        {
            "ok": True,
            "promo_code": promo_code.upper(),
            "discounted_course_id": discounted["course_id"] if discounted else None,
            "discounted_course_title": discounted["title"] if discounted else None,
            "order_total": sum(lead["total_price"] for lead in leads),
            "leads": [
                {
                    "lead_id": lead["id"],
                    "course_id": lead["course_id"],
                    "title": lead["title"],
                    "base_price": lead["price"],
                    "discount_percent": lead["discount_percent"],
                    "total_price": lead["total_price"],
                }
                for lead in leads
            ],
            "message": "Скидка применена к одной заявке, цены пересчитаны.",
        }
    )


@mcp.tool()
def schedule_lead_reminder(chat_id: int, delay_seconds: int = 0) -> str:
    """Запланировать напоминание о заявке после паузы в диалоге.

    Вызывается ботом после каждого ответа пользователю: таймер перезапускается
    от последнего сообщения. Если заявка уже создана или напоминание в этом
    диалоге отправлено, расписание не меняется.

    Args:
        chat_id: ID чата Telegram.
        delay_seconds: Пауза в секундах; 0 — значение REMINDER_DELAY_SECONDS.
    """
    delay = int(delay_seconds) if int(delay_seconds) > 0 else settings.reminder_delay_seconds
    if delay <= 0:
        return _json({"ok": True, "scheduled": False, "reason": "напоминания выключены"})

    reminder = db.schedule_reminder(chat_id, delay)
    return _json(
        {
            "ok": True,
            "scheduled": reminder["status"] == "pending",
            "chat_id": reminder["chat_id"],
            "due_at": reminder["due_at"],
            "status": reminder["status"],
            "delay_seconds": delay,
        }
    )


@mcp.tool()
def cancel_lead_reminder(chat_id: int, reason: str = "cancelled") -> str:
    """Отменить ожидающее напоминание: заявка создана или диалог очищен.

    Args:
        chat_id: ID чата Telegram.
        reason: Причина отмены для журнала: ``lead_created`` или ``cancelled``.
    """
    cancelled = db.cancel_reminder(chat_id, reason=reason or "cancelled")
    return _json({"ok": True, "cancelled": cancelled, "chat_id": chat_id})


@mcp.tool()
def get_due_reminders(limit: int = 20) -> str:
    """Получить напоминания, время которых наступило.

    Бот опрашивает этот инструмент по таймеру и отправляет текст напоминания
    в указанные чаты. Текст берётся из поля ``text``.

    Args:
        limit: Сколько напоминаний вернуть за один опрос, от 1 до 50.
    """
    safe_limit = max(1, min(int(limit), 50))
    items = db.due_reminders(limit=safe_limit)
    return _json(
        {
            "ok": True,
            "count": len(items),
            "text": settings.reminder_text,
            "reminders": items,
        }
    )


@mcp.tool()
def complete_lead_reminder(chat_id: int) -> str:
    """Отметить напоминание отправленным, чтобы не присылать его повторно.

    Args:
        chat_id: ID чата Telegram.
    """
    marked = db.mark_reminder_sent(chat_id)
    return _json({"ok": True, "marked": marked, "chat_id": chat_id})


@mcp.tool()
def get_lead_reminder_status(chat_id: int) -> str:
    """Показать состояние напоминания о заявке для чата.

    Args:
        chat_id: ID чата Telegram.
    """
    reminder = db.reminder_status(chat_id)
    if reminder is None:
        return _json({"ok": True, "chat_id": chat_id, "status": "none"})
    return _json(
        {
            "ok": True,
            "chat_id": chat_id,
            "status": reminder["status"],
            "due_at": reminder["due_at"],
            "sent_at": reminder["sent_at"],
            "lead_created": bool(reminder["lead_created"]),
        }
    )
