"""Тесты базы: каталог, база знаний и заявки."""

from __future__ import annotations

import pytest

from mcp_server.database import CourseDatabase


@pytest.fixture()
def db(tmp_path) -> CourseDatabase:
    """Отдельная временная база для каждого теста."""
    database = CourseDatabase(tmp_path / "courses.db")
    database.initialize()
    return database


def test_seed_is_idempotent(db: CourseDatabase) -> None:
    assert len(db.search_courses()) == 7
    db.initialize()
    assert len(db.search_courses()) == 7


def test_search_by_language_alias(db: CourseDatabase) -> None:
    courses = db.search_courses(language="english")
    assert len(courses) == 4
    assert {course["title"] for course in courses} >= {
        "Английский с нуля",
        "Разговорный английский",
    }


def test_search_by_level_and_budget(db: CourseDatabase) -> None:
    courses = db.search_courses(level="beginner", max_price=16000)
    assert courses
    assert all(course["price"] <= 16000 for course in courses)
    assert all("a1" in course["level"].lower() for course in courses)


def test_course_details_include_program_and_groups(db: CourseDatabase) -> None:
    course = db.get_course(1)
    assert course is not None
    assert course["title"] == "Английский с нуля"
    assert len(course["modules"]) == 3
    assert course["groups"]
    assert course["groups"][0]["schedule"]


def test_unknown_course(db: CourseDatabase) -> None:
    assert db.get_course(9999) is None
    assert db.course_exists(9999) is False


def test_active_promotions(db: CourseDatabase) -> None:
    promos = {item["code"]: item["discount_percent"] for item in db.list_promotions()}
    assert promos["START10"] == 10
    assert db.get_promo(" start10 ")["discount_percent"] == 10
    assert db.get_promo("NOPE") is None


def test_faq_search(db: CourseDatabase) -> None:
    items = db.search_faq("можно ли оплатить в рассрочку")
    assert items
    assert "рассроч" in items[0]["answer"].lower()


def test_faq_empty_query(db: CourseDatabase) -> None:
    assert db.search_faq("   ") == []


def test_create_lead_and_deduplicate(db: CourseDatabase) -> None:
    lead, created = db.create_lead(
        name="Ирина",
        contact="+7 999 123-45-67",
        course_id=1,
        preferred_time="вечером",
        comment="Хочу на пробный урок",
    )
    assert created is True
    assert lead is not None
    assert lead["status"] == "new"

    duplicate, created_again = db.create_lead(
        name="Ирина",
        contact="+7 999 123-45-67",
        course_id=1,
    )
    assert created_again is False
    assert duplicate is not None
    assert duplicate["id"] == lead["id"]


def test_create_lead_for_unknown_course(db: CourseDatabase) -> None:
    lead, created = db.create_lead("Иван", "+79990000000", course_id=999)
    assert lead is None
    assert created is False


def test_promo_rule_is_stored_in_database(db: CourseDatabase) -> None:
    """Правило «скидка на один курс» должно быть в данных, а не только в промпте."""
    promo = db.get_promo("START10")
    assert "один курс" in promo["description"].lower()


def test_promo_rule_synced_into_existing_database(tmp_path) -> None:
    """Старая база с формулировкой «на первый курс» обновляется при старте сервера."""
    import sqlite3

    path = tmp_path / "old.db"
    CourseDatabase(path).initialize()
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE promos SET description = ? WHERE code = 'START10'",
        ("Скидка 10% на первый курс для новых учеников.",),
    )
    connection.commit()
    connection.close()

    CourseDatabase(path).initialize()

    assert "один курс" in CourseDatabase(path).get_promo("START10")["description"].lower()


def test_lead_stores_promo_and_total(db: CourseDatabase) -> None:
    lead, created = db.create_lead(
        name="Пётр",
        contact="+79991112233",
        course_id=3,
        promo_code="START10",
    )
    assert created is True
    assert lead["promo_code"] == "START10"
    assert lead["discount_percent"] == 10
    assert lead["total_price"] == 26991


def test_list_leads_returns_courses_of_the_order(db: CourseDatabase) -> None:
    db.create_lead("Ольга", "olga@mail.ru", course_id=5)
    db.create_lead("Ольга", "olga@mail.ru", course_id=3)

    leads = db.list_leads("OLGA@mail.ru")

    assert {lead["title"] for lead in leads} == {"Немецкий с нуля", "Бизнес-английский"}
    assert all(lead["total_price"] for lead in leads)


def test_lead_found_by_any_contact_client_mentioned(db: CourseDatabase) -> None:
    """Клиент оставил «почта, телефон», а называет только почту — заявка находится."""
    db.create_lead("Ольга", "olga@mail.ru, +79055553322", course_id=5)
    db.create_lead("Ольга", "olga@mail.ru, +79055553322", course_id=3)

    assert len(db.list_leads("olga@mail.ru")) == 2
    assert len(db.list_leads("+79055553322")) == 2

    leads, error = db.apply_promo_to_contact("+79055553322", "START10")
    assert error is None
    assert sum(lead["discount_percent"] for lead in leads) == 10


def test_update_lead_keeps_untouched_fields(db: CourseDatabase) -> None:
    lead, _ = db.create_lead(
        "Ольга", "olga@mail.ru", course_id=3, preferred_time="вечером", comment="нужен вечер"
    )

    updated, error = db.update_lead(lead["id"], comment="скидка на бизнес-английский")

    assert error is None
    assert updated["comment"] == "скидка на бизнес-английский"
    assert updated["preferred_time"] == "вечером"
    assert updated["status"] == "new"
    assert "комментарий обновлён" in updated["history"]


def test_update_lead_changes_course_and_promo(db: CourseDatabase) -> None:
    """Просьбу «скидку на более дорогой курс» база исполняет реально."""
    lead, _ = db.create_lead("Ольга", "olga@mail.ru", course_id=5)

    updated, error = db.update_lead(lead["id"], promo_code="START10")
    assert error is None
    assert updated["discount_percent"] == 10
    assert updated["total_price"] == 16191

    moved, error = db.update_lead(lead["id"], course_id=3)
    assert error is None
    assert moved["course_id"] == 3
    assert moved["total_price"] == 26991
    assert "курс заменён" in moved["history"]

    cleared, error = db.update_lead(lead["id"], promo_code="")
    assert error is None
    assert cleared["promo_code"] == ""
    assert cleared["total_price"] == 29990


def test_update_lead_rejects_unknown_course_or_promo(db: CourseDatabase) -> None:
    lead, _ = db.create_lead("Ольга", "olga@mail.ru", course_id=5)

    _row, error = db.update_lead(lead["id"], course_id=999)
    assert error is not None and "курс" in error.lower()

    _row, error = db.update_lead(lead["id"], promo_code="NOPE")
    assert error is not None and "NOPE" in error


def test_cancel_lead_keeps_record_with_history(db: CourseDatabase) -> None:
    lead, _ = db.create_lead("Ольга", "olga@mail.ru", course_id=5)

    cancelled, error = db.cancel_lead(lead["id"])

    assert cancelled is True and error is None
    stored = db.get_lead(lead["id"])
    assert stored["status"] == "cancelled"
    assert "отменена" in stored["history"]


def test_apply_promo_moves_discount_to_one_lead(db: CourseDatabase) -> None:
    """Скидка не может «залипнуть» на нескольких заявках: база держит правило."""
    db.create_lead("Ольга", "olga@mail.ru", course_id=5, promo_code="START10")
    db.create_lead("Ольга", "olga@mail.ru", course_id=3)

    leads, error = db.apply_promo_to_contact("olga@mail.ru", "START10")

    assert error is None
    assert sum(lead["discount_percent"] for lead in leads) == 10
    discounted = [lead for lead in leads if lead["discount_percent"]]
    # По умолчанию скидка уходит на самый дорогой курс заказа.
    assert discounted[0]["course_id"] == 3
    assert sum(lead["total_price"] for lead in leads) == 44981


def test_apply_promo_to_chosen_course(db: CourseDatabase) -> None:
    db.create_lead("Ольга", "olga@mail.ru", course_id=5)
    db.create_lead("Ольга", "olga@mail.ru", course_id=3)

    leads, error = db.apply_promo_to_contact("olga@mail.ru", "START10", course_id=5)

    assert error is None
    assert [lead["discount_percent"] for lead in leads] == [10, 0]
    assert sum(lead["total_price"] for lead in leads) == 46181


def test_apply_promo_errors(db: CourseDatabase) -> None:
    db.create_lead("Ольга", "olga@mail.ru", course_id=3)

    leads, unknown_promo = db.apply_promo_to_contact("olga@mail.ru", "NOPE")
    assert leads == [] and "не найден" in unknown_promo

    leads, unknown_course = db.apply_promo_to_contact("olga@mail.ru", "START10", course_id=1)
    assert leads == [] and "не входит" in unknown_course

    leads, no_contact = db.apply_promo_to_contact("ghost@mail.ru", "START10")
    assert leads == [] and "Заявок" in no_contact
