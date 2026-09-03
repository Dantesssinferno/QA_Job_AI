from datetime import UTC, datetime

from qa_job_scout.core import Vacancy, evaluate

PROFILE = {"skills": ["REST API", "Postman"], "evidence": ["x", "y"], "headline": "QA", "location": "Молдова", "name": "Максим"}
NOW = datetime(2026, 8, 24, tzinfo=UTC)


def make(text: str) -> Vacancy:
    return Vacancy("s", "Manual QA Engineer", "https://example.test/1", text, text, remote=True)


def test_remote_fresh_manual_qa_is_recommended():
    result = evaluate(make("Remote QA, REST API, Postman, опубликовано 2 дня назад"), PROFILE, NOW)
    assert result.status == "recommended"


def test_required_english_is_rejected():
    result = evaluate(make("remote English B2 required, 1 день назад"), PROFILE, NOW)
    assert result.status == "rejected"


def test_required_automation_is_rejected():
    result = evaluate(make("remote automation обязательно, 1 день назад"), PROFILE, NOW)
    assert result.status == "rejected"


def test_automation_role_is_rejected_without_required_word():
    result = evaluate(make("remote QA Automation Python, 1 день назад"), PROFILE, NOW)
    assert result.status == "rejected"


def test_old_vacancy_is_rejected():
    result = evaluate(make("remote, 6 дней назад"), PROFILE, NOW)
    assert result.status == "rejected"


def test_hours_and_russian_month_dates_are_parsed():
    assert evaluate(make("remote REST API Postman, 20 часов назад"), PROFILE, NOW).status == "recommended"
    assert evaluate(make("remote, обновлено 21 авг"), PROFILE, NOW).status == "needs_review"
    assert evaluate(make("remote REST API, 13 ч. назад"), PROFILE, NOW).status == "recommended"
    assert evaluate(make("remote REST API, 1 д. назад"), PROFILE, NOW).status == "recommended"
    assert evaluate(make("remote REST API, 30 мин. назад"), PROFILE, NOW).status == "recommended"
