from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable


ROLE_RE = re.compile(r"\b(qa|qc|quality assurance|manual.{0,12}test|тестировщик|тест-инженер)\b", re.I)
REMOTE_RE = re.compile(r"\b(remote|удал[её]н|работа из дома|home office)\b", re.I)
EN_REQUIRED_RE = re.compile(r"(english|английск).{0,45}(required|обязател|must|b2|c1|fluent)|\b(b2|c1|c2)\b", re.I)
AUTOMATION_REQUIRED_RE = re.compile(r"(automation|автоматизац).{0,45}(required|обязател|must|необходим)", re.I)
AUTOMATION_ROLE_RE = re.compile(r"\b(?:qa\s+)?automation\b|автоматизац(?:ия|ии|ию)|автотест|sdet", re.I)
DATE_PATTERNS = (
    (re.compile(r"(сегодня|today)", re.I), 0),
    (re.compile(r"(вчера|yesterday)", re.I), 1),
    (re.compile(r"(\d+)\s*(?:мин(?:ут[ыа]?|\.)?|minutes?)\s*(?:назад|ago)?", re.I), "minutes"),
    (re.compile(r"(\d+)\s*(?:час(?:а|ов)?|hours?)\s*(?:назад|ago)?", re.I), "hours"),
    (re.compile(r"(\d+)\s*(?:д(?:ень|ня|ней)|days?)\s*(?:назад|ago)", re.I), None),
)
MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


@dataclass
class Vacancy:
    source: str
    title: str
    url: str
    text: str
    published_text: str = ""
    published_at: str | None = None
    remote: bool = False
    score: int = 0
    status: str = "new"
    reasons: list[str] | None = None
    cover_letter: str = ""

    @property
    def id(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()[:12]


def parse_age(text: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(UTC)
    clean = " ".join(text.split())
    for pattern, delta in DATE_PATTERNS:
        match = pattern.search(clean)
        if match:
            if delta == "minutes":
                return now - timedelta(minutes=int(match.group(1)))
            if delta == "hours":
                return now - timedelta(hours=int(match.group(1)))
            return now - timedelta(days=delta if delta is not None else int(match.group(1)))
    month_match = re.search(r"\b(\d{1,2})\s+([а-яё]{3,}|[a-z]{3,})(?:\s+(\d{4}))?\b", clean, re.I)
    if month_match:
        month = MONTHS.get(month_match.group(2).lower()[:3])
        if month:
            year = int(month_match.group(3) or now.year)
            parsed = datetime(year, month, int(month_match.group(1)), tzinfo=UTC)
            return parsed if parsed <= now else parsed.replace(year=year - 1)
    # Common absolute formats, interpreted in UTC for a conservative cutoff.
    for match in re.finditer(r"\b(?:\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})\b", clean):
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(match.group(0), fmt).replace(tzinfo=UTC)
            except ValueError:
                pass
    return None


def evaluate(vacancy: Vacancy, profile: dict, now: datetime | None = None) -> Vacancy:
    haystack = f"{vacancy.title}\n{vacancy.text}"
    reasons: list[str] = []
    date = parse_age(vacancy.published_text, now)
    vacancy.published_at = date.isoformat() if date else None

    if not ROLE_RE.search(haystack):
        vacancy.status, vacancy.reasons = "rejected", ["Должность не относится к QA."]
        return vacancy
    if not (vacancy.remote or REMOTE_RE.search(haystack)):
        vacancy.status, vacancy.reasons = "rejected", ["Удалённый формат не подтверждён."]
        return vacancy
    if EN_REQUIRED_RE.search(haystack):
        vacancy.status, vacancy.reasons = "rejected", ["Английский указан как обязательный."]
        return vacancy
    # A dedicated automation role is unsuitable even when the card does not use
    # words such as "required" (for example: "QA Automation (Python)").
    if AUTOMATION_ROLE_RE.search(vacancy.title) or AUTOMATION_REQUIRED_RE.search(haystack):
        vacancy.status, vacancy.reasons = "rejected", ["Это роль с обязательной автоматизацией, а не manual/API QA."]
        return vacancy
    if date is None:
        vacancy.status, vacancy.reasons = "needs_review", ["Не удалось надёжно прочитать дату публикации."]
        return vacancy
    if (now or datetime.now(UTC)) - date > timedelta(days=5):
        vacancy.status, vacancy.reasons = "rejected", ["Вакансии больше пяти дней."]
        return vacancy

    normalized = haystack.lower()
    matches = [skill for skill in profile["skills"] if skill.lower() in normalized]
    vacancy.score = min(95, 55 + len(matches) * 5)
    reasons.extend(["Удалённый формат подтверждён.", "Дата публикации не старше 5 дней."])
    if matches:
        reasons.append("Совпадения: " + ", ".join(matches[:6]) + ".")
    else:
        reasons.append("Роль совпадает; требований с прямым совпадением навыков мало.")
    vacancy.status, vacancy.reasons = ("recommended" if vacancy.score >= 65 else "needs_review"), reasons
    return vacancy


def deterministic_letter(vacancy: Vacancy, profile: dict) -> str:
    evidence = profile["evidence"]
    return (
        f"Здравствуйте!\n\nЗаинтересовала вакансия {vacancy.title}. "
        f"Я {profile['headline']} из {profile['location']}; мой основной фокус - "
        "ручное тестирование backend/API, интеграций и бизнес-логики.\n\n"
        f"{evidence[1]} {evidence[0]}\n\n"
        "Буду рад обсудить, как мой опыт может быть полезен вашей команде.\n\n"
        f"С уважением,\n{profile['name']}"
    )


def load_profile(path: str = "candidate_profile.json") -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def vacancy_json(v: Vacancy) -> str:
    return json.dumps(asdict(v), ensure_ascii=False)
