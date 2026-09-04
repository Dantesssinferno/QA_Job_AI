from datetime import UTC, datetime

from qa_job_scout.core import Vacancy, evaluate, parse_age

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



def test_date_parser_does_not_treat_skill_text_as_date():
    from qa_job_scout.core import parse_age

    now = NOW
    assert parse_age("1 PostgreSQL", now) is None
    assert parse_age("3 навыка", now) is None


def test_date_parser_supports_weeks_and_years():
    from qa_job_scout.core import parse_age

    assert parse_age("1 неделя", NOW) == NOW - __import__("datetime").timedelta(days=7)
    assert parse_age("2 года", NOW) == NOW - __import__("datetime").timedelta(days=730)


def test_parse_age_supports_hh_iso_timestamp():
    from datetime import UTC, datetime

    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    parsed = parse_age("2026-09-04T09:00:00+00:00", now=now)
    assert parsed == datetime(2026, 9, 4, 9, 0, tzinfo=UTC)
