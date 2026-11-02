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
