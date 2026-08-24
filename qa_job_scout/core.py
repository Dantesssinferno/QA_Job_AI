from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


# ============================================================
# ROLE DETECTION
# ============================================================
#
# Главное правило:
# 1. Сначала определяем профессию по TITLE.
# 2. Только если title явно QA — анализируем описание.
#
# Это предотвращает ситуации:
#   "DevOps / MLOps Engineer" + текст содержит "QA"
#   "Senior Product Engineer" + текст содержит "testing"
#   "Data Analyst" + текст содержит "QA/UAT"
#   "System Analyst" + текст содержит "quality assurance"
#
# Такие вакансии не должны становиться QA-вакансиями.
#

QA_TITLE_RE = re.compile(
    r"""
    (?:
        \bqa\b
        |
        \bq\s*/\s*a\b
        |
        \bqc\b
        |
        \bqa/qc\b
        |
        \bquality\s+assurance\b
        |
        \bquality\s+engineer\b
        |
        \bmanual\s+qa\b
        |
        \bmanual\s+tester\b
        |
        \bsoftware\s+tester\b
        |
        \bsoftware\s+testing\b
        |
        \btest\s+engineer\b
        |
        \btesting\s+engineer\b
        |
        \bqa\s+engineer\b
        |
        \bqa\s+specialist\b
        |
        \bqa\s+tester\b
        |
        \bqa\s+analyst\b
        |
        \bтестировщик\b
        |
        \bтестировщица\b
        |
        \bтест-инженер\b
        |
        \bтест\s+инженер\b
        |
        \bинженер\s+по\s+тестированию\b
        |
        \bспециалист\s+по\s+тестированию\b
        |
        \bинженер\s+по\s+качеству\b
    )
    """,
    re.I | re.X,
)


# Явные НЕ-QA роли.
#
# Они проверяются ДО QA_TITLE_RE.
# Это важно для названий вроде:
#
# "Penetration Testing Specialist"
# "QA Manager" в некоторых компаниях может быть QA,
# поэтому manager сюда специально НЕ добавляем.
#

NON_QA_TITLE_RE = re.compile(
    r"""
    (?:
        \bdevops\b
        |
        \bmlops\b
        |
        \bsre\b
        |
        \bsite\s+reliability\b
        |
        \bsoftware\s+engineer\b
        |
        \bsoftware\s+developer\b
        |
        \bbackend\s+engineer\b
        |
        \bbackend\s+developer\b
        |
        \bfrontend\s+engineer\b
        |
        \bfrontend\s+developer\b
        |
        \bfullstack\s+engineer\b
        |
        \bfullstack\s+developer\b
        |
        \bfull[-\s]?stack\s+engineer\b
        |
        \bfull[-\s]?stack\s+developer\b
        |
        \bproduct\s+engineer\b
        |
        \bdata\s+engineer\b
        |
        \bdata\s+scientist\b
        |
        \bdata\s+analyst\b
        |
        \bmachine\s+learning\s+engineer\b
        |
        \bml\s+engineer\b
        |
        \bai\s+engineer\b
        |
        \bsystem\s+analyst\b
        |
        \bsystems\s+analyst\b
        |
        \bbusiness\s+analyst\b
        |
        \bproduct\s+analyst\b
        |
        \bсистемный\s+аналитик\b
        |
        \bбизнес[-\s]?аналитик\b
        |
        \bпродуктовый\s+аналитик\b
        |
        \bаналитик\s+данных\b
        |
        \bразработчик\b
        |
        \bпрограммист\b
        |
        \bdevsecops\b
        |
        \bsecurity\s+engineer\b
        |
        \bsecurity\s+analyst\b
        |
        \bpenetration\s+testing\b
        |
        \bpenetration\s+tester\b
        |
        \bsecurity\s+tester\b
        |
        \bqa\s+automation\s+developer\b
    )
    """,
    re.I | re.X,
)


# ============================================================
# OTHER FILTERS
# ============================================================

REMOTE_RE = re.compile(
    r"\b(remote|удал[её]н|работа из дома|home office)\b",
    re.I,
)


EN_REQUIRED_RE = re.compile(
    r"""
    (?:
        (?:english|английск).{0,45}
        (?:required|обязател|must|необходим|fluent|b2|c1|c2)
        |
        \b(?:b2|c1|c2)\b
    )
    """,
    re.I | re.X,
)


AUTOMATION_REQUIRED_RE = re.compile(
    r"""
    (?:
        (?:automation|автоматизац).{0,45}
        (?:required|обязател|must|необходим)
        |
        (?:required|обязател|must|необходим).{0,45}
        (?:automation|автоматизац)
    )
    """,
    re.I | re.X,
)


# Automation/SDET как самостоятельная профессия.
AUTOMATION_ROLE_RE = re.compile(
    r"""
    (?:
        \bqa\s+automation\b
        |
        \bautomation\s+qa\b
        |
        \bautomation\s+engineer\b
        |
        \btest\s+automation\s+engineer\b
        |
        \bautomation\s+tester\b
        |
        \bsdet\b
        |
        \bавтоматизац(?:ия|ии|ию)\s+тестирован
        |
        \bинженер\s+по\s+автоматизации\s+тестирования
        |
        \bавтотест
    )
    """,
    re.I | re.X,
)


DATE_PATTERNS = (
    (re.compile(r"(сегодня|today)", re.I), 0),
    (re.compile(r"(вчера|yesterday)", re.I), 1),
    (
        re.compile(
            r"(\d+)\s*(?:мин(?:ут[ыа]?|\.)?|minutes?)\s*(?:назад|ago)?",
            re.I,
        ),
        "minutes",
    ),
    (
        re.compile(
            r"(\d+)\s*(?:час(?:а|ов)?|hours?)\s*(?:назад|ago)?",
            re.I,
        ),
        "hours",
    ),
    (
        re.compile(
            r"(\d+)\s*(?:д(?:ень|ня|ней)|days?)\s*(?:назад|ago)",
            re.I,
        ),
        None,
    ),
)


MONTHS = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
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


def parse_age(
    text: str,
    now: datetime | None = None,
) -> datetime | None:
    now = now or datetime.now(UTC)

    clean = " ".join(text.split())

    for pattern, delta in DATE_PATTERNS:
        match = pattern.search(clean)

        if match:
            if delta == "minutes":
                return now - timedelta(
                    minutes=int(match.group(1))
                )

            if delta == "hours":
                return now - timedelta(
                    hours=int(match.group(1))
                )

            return now - timedelta(
                days=(
                    delta
                    if delta is not None
                    else int(match.group(1))
                )
            )

    month_match = re.search(
        r"\b(\d{1,2})\s+([а-яё]{3,}|[a-z]{3,})(?:\s+(\d{4}))?\b",
        clean,
        re.I,
    )

    if month_match:
        month = MONTHS.get(
            month_match.group(2).lower()[:3]
        )

        if month:
            year = int(
                month_match.group(3) or now.year
            )

            parsed = datetime(
                year,
                month,
                int(month_match.group(1)),
                tzinfo=UTC,
            )

            return (
                parsed
                if parsed <= now
                else parsed.replace(year=year - 1)
            )

    # Common absolute formats, interpreted in UTC
    # for a conservative cutoff.
    for match in re.finditer(
        r"\b(?:\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})\b",
        clean,
    ):
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(
                    match.group(0),
                    fmt,
                ).replace(tzinfo=UTC)

            except ValueError:
                pass

    return None


def evaluate(
    vacancy: Vacancy,
    profile: dict,
    now: datetime | None = None,
) -> Vacancy:
    """
    Основной deterministic-фильтр вакансии.

    Приоритеты:

    1. TITLE — определяет профессию.
    2. TITLE — исключает явно нерелевантные профессии.
    3. DESCRIPTION — уточняет требования.
    4. DESCRIPTION — используется для skill matching.
    5. Score считается только после прохождения
       всех обязательных фильтров.
    """

    title = " ".join(vacancy.title.split())
    description = " ".join(vacancy.text.split())
    haystack = f"{title}\n{description}"

    reasons: list[str] = []

    # --------------------------------------------------------
    # DATE
    # --------------------------------------------------------

    date = parse_age(
        vacancy.published_text,
        now,
    )

    vacancy.published_at = (
        date.isoformat()
        if date
        else None
    )

    # --------------------------------------------------------
    # 1. TITLE — ОСНОВНОЙ ФИЛЬТР ПРОФЕССИИ
    # --------------------------------------------------------

    # Сначала исключаем явно нерелевантные роли.
    #
    # Например:
    # DevOps / MLOps Engineer
    # Senior Product Engineer
    # Data Analyst
    # System Analyst
    # Penetration Testing Specialist
    #
    # даже если description содержит:
    # QA, testing, quality assurance и т.д.

    if NON_QA_TITLE_RE.search(title):
        vacancy.status = "rejected"
        vacancy.reasons = [
            f"Название должности не относится к QA: {title}"
        ]
        return vacancy

    # Затем ищем QA именно в TITLE.
    #
    # ВАЖНО:
    # QA в description больше НЕ может сделать
    # нерелевантную профессию QA-вакансией.

    title_is_qa = bool(QA_TITLE_RE.search(title))

    if not title_is_qa:
        vacancy.status = "rejected"
        vacancy.reasons = [
            f"В заголовке вакансии нет QA-позиции: {title}"
        ]
        return vacancy

    reasons.append(
        f"QA-позиция подтверждена по заголовку: {title}."
    )

    # --------------------------------------------------------
    # 2. AUTOMATION
    # --------------------------------------------------------

    # Automation/SDET в TITLE = сразу reject.

    if AUTOMATION_ROLE_RE.search(title):
        vacancy.status = "rejected"
        vacancy.reasons = [
            "В заголовке указана automation/SDET роль, "
            "а целевой профиль — manual/API/backend QA."
        ]
        return vacancy

    # Если automation обязательна в описании.
    if AUTOMATION_REQUIRED_RE.search(haystack):
        vacancy.status = "rejected"
        vacancy.reasons = [
            "Автоматизация указана как обязательное требование."
        ]
        return vacancy

    # --------------------------------------------------------
    # 3. REMOTE
    # --------------------------------------------------------

    if not (
        vacancy.remote
        or REMOTE_RE.search(haystack)
    ):
        vacancy.status = "rejected"
        vacancy.reasons = [
            "Удалённый формат не подтверждён."
        ]
        return vacancy

    reasons.append(
        "Удалённый формат подтверждён."
    )

    # --------------------------------------------------------
    # 4. ENGLISH
    # --------------------------------------------------------

    if EN_REQUIRED_RE.search(haystack):
        vacancy.status = "rejected"
        vacancy.reasons = [
            "Английский указан как обязательный."
        ]
        return vacancy

    # --------------------------------------------------------
    # 5. DATE
    # --------------------------------------------------------

    if date is None:
        vacancy.status = "needs_review"
        vacancy.reasons = [
            *reasons,
            "Не удалось надёжно прочитать дату публикации."
        ]
        return vacancy

    if (
        now or datetime.now(UTC)
    ) - date > timedelta(days=5):
        vacancy.status = "rejected"
        vacancy.reasons = [
            *reasons,
            "Вакансии больше пяти дней."
        ]
        return vacancy

    reasons.append(
        "Дата публикации не старше 5 дней."
    )

    # --------------------------------------------------------
    # 6. DESCRIPTION / SKILLS
    # --------------------------------------------------------
    #
    # Здесь описание уже НЕ определяет профессию.
    #
    # Оно только помогает понять:
    # - насколько вакансия подходит;
    # - какие технологии совпадают;
    # - manual/API/backend ли это.
    #

    normalized_description = description.lower()

    profile_skills = profile.get(
        "skills",
        [],
    )

    matches = [
        skill
        for skill in profile_skills
        if skill.lower() in normalized_description
    ]

    # --------------------------------------------------------
    # 7. QA SPECIALIZATION MATCH
    # --------------------------------------------------------

    backend_api_re = re.compile(
        r"""
        \b(
            api
            |rest
            |graphql
            |backend
            |back-end
            |server-side
            |микросервис
            |микросервисы
            |microservice
            |microservices
        )\b
        """,
        re.I | re.X,
    )

    manual_re = re.compile(
        r"""
        \b(
            manual
            |manual\s+testing
            |manual\s+qa
            |ручн(?:ое|ого)\s+тестирован
            |functional\s+testing
            |регрессион
            |regрессион
        )\b
        """,
        re.I | re.X,
    )

    backend_api_match = bool(
        backend_api_re.search(normalized_description)
    )

    manual_match = bool(
        manual_re.search(normalized_description)
    )

    # --------------------------------------------------------
    # 8. SCORE
    # --------------------------------------------------------
    #
    # Теперь score НЕ зависит просто от количества
    # случайных технических навыков.
    #

    score = 55

    # Manual QA — сильное совпадение.
    if manual_match:
        score += 15
        reasons.append(
            "В описании подтверждено ручное тестирование."
        )

    # API/backend — сильное совпадение с профилем.
    if backend_api_match:
        score += 15
        reasons.append(
            "В описании есть API/backend-направление."
        )

    # Совпадения навыков.
    #
    # Ограничиваем вклад навыков, чтобы 20 технологий
    # не превращали любую инженерную вакансию
    # в 95/95.
    if matches:
        skill_bonus = min(
            10,
            len(matches) * 2,
        )

        score += skill_bonus

        reasons.append(
            "Совпадения навыков: "
            + ", ".join(matches[:8])
            + "."
        )
    else:
        reasons.append(
            "Прямых совпадений навыков с профилем немного."
        )

    vacancy.score = min(
        95,
        score,
    )

    # --------------------------------------------------------
    # 9. FINAL STATUS
    # --------------------------------------------------------

    if vacancy.score >= 75:
        vacancy.status = "recommended"

    elif vacancy.score >= 65:
        vacancy.status = "needs_review"

    else:
        vacancy.status = "needs_review"

    vacancy.reasons = reasons

    return vacancy


def deterministic_letter(
    vacancy: Vacancy,
    profile: dict,
) -> str:
    evidence = profile["evidence"]

    return (
        f"Здравствуйте!\n\n"
        f"Заинтересовала вакансия {vacancy.title}. "
        f"Я {profile['headline']} из "
        f"{profile['location']}; мой основной фокус - "
        "ручное тестирование backend/API, интеграций "
        "и бизнес-логики.\n\n"
        f"{evidence[1]} {evidence[0]}\n\n"
        "Буду рад обсудить, как мой опыт может быть "
        "полезен вашей команде.\n\n"
        f"С уважением,\n{profile['name']}"
    )


def load_profile(
    path: str = "candidate_profile.json",
) -> dict:
    return json.loads(
        Path(path).read_text(
            encoding="utf-8"
        )
    )


def vacancy_json(
    v: Vacancy,
) -> str:
    return json.dumps(
        asdict(v),
        ensure_ascii=False,
    )