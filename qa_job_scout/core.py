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

    Порядок принятия решения:

    1. TITLE
       Определяем профессию только по заголовку.

    2. ROLE
       Проверяем, является ли это подходящей QA-ролью.
       Automation/SDET в title сразу отклоняется.

    3. HARD FILTERS
       Проверяем:
       - обязательную automation;
       - remote;
       - обязательный английский.

    4. DATE
       Проверяем возраст вакансии.

    5. SKILLS
       Сопоставляем описание и title с профилем кандидата.

    6. SCORE
       Рассчитываем степень соответствия.

    7. STATUS
       recommended / needs_review / rejected
    """

    now = now or datetime.now(UTC)

    title = " ".join(vacancy.title.split())
    description = " ".join(vacancy.text.split())

    # Для поиска требований и навыков используем title + description.
    # Но профессию определяем ТОЛЬКО по title.
    haystack = f"{title}\n{description}"

    reasons: list[str] = []

    # ============================================================
    # 1. TITLE
    # ============================================================

    # Сначала исключаем явно нерелевантные профессии.
    #
    # Например:
    # DevOps Engineer
    # Backend Engineer
    # Data Analyst
    # System Analyst
    #
    # Даже если внутри description написано QA/testing,
    # такая вакансия не должна стать QA-вакансией.

    if NON_QA_TITLE_RE.search(title):
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            f"Название должности не относится к QA: {title}"
        ]
        return vacancy

    # Теперь QA должен быть непосредственно в TITLE.
    title_is_qa = bool(QA_TITLE_RE.search(title))

    if not title_is_qa:
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            f"В заголовке вакансии нет QA-позиции: {title}"
        ]
        return vacancy

    reasons.append(
        f"QA-позиция подтверждена по заголовку: {title}."
    )

    # ============================================================
    # 2. ROLE
    # ============================================================

    # Automation/SDET как отдельная роль
    # для текущего профиля не подходит.

    automation_role_match = (
        AUTOMATION_ROLE_RE.search(title)
        or AUTOMATION_ROLE_RE.search(description)
    )

    if automation_role_match:
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            *reasons,
            (
                "В вакансии указана Automation/SDET роль, "
                "что не соответствует целевому Manual/API/Backend QA профилю."
            ),
        ]
        return vacancy

    reasons.append(
        "Роль соответствует Manual/API/Backend QA."
    )

    # ============================================================
    # 3. HARD FILTERS
    # ============================================================

    # ------------------------------------------------------------
    # 3.1 Automation required
    # ------------------------------------------------------------

    if AUTOMATION_REQUIRED_RE.search(haystack):
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            *reasons,
            "Автоматизация указана как обязательное требование."
        ]
        return vacancy

    reasons.append(
        "Автоматизация не указана как обязательное требование."
    )

    # ------------------------------------------------------------
    # 3.2 Remote
    # ------------------------------------------------------------

    remote_is_confirmed = (
        vacancy.remote
        or bool(REMOTE_RE.search(haystack))
    )

    if not remote_is_confirmed:
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            *reasons,
            "Удалённый формат не подтверждён."
        ]
        return vacancy

    reasons.append(
        "Удалённый формат подтверждён."
    )

    # ------------------------------------------------------------
    # 3.3 English
    # ------------------------------------------------------------

    if EN_REQUIRED_RE.search(haystack):
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            *reasons,
            "Английский указан как обязательное требование."
        ]
        return vacancy

    reasons.append(
        "Английский не указан как обязательное требование."
    )

    # ============================================================
    # 4. DATE
    # ============================================================

    # В нормальном crawler-е дата должна находиться
    # в published_text.
    #
    # Но unit-тесты и некоторые сайты могут передавать дату
    # внутри текста вакансии.
    #
    # Поэтому используем fallback:
    #
    # published_text -> description -> title

    date_source = (
        vacancy.published_text
        or description
        or title
    )

    date = parse_age(
        date_source,
        now,
    )

    vacancy.published_at = (
        date.isoformat()
        if date
        else None
    )

    if date is None:
        vacancy.status = "needs_review"
        vacancy.score = 0
        vacancy.reasons = [
            *reasons,
            "Не удалось надёжно определить дату публикации."
        ]
        return vacancy

    age = now - date

    if age > timedelta(days=5):
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            *reasons,
            "Вакансии больше пяти дней."
        ]
        return vacancy

    reasons.append(
        "Дата публикации не старше 5 дней."
    )

    # ============================================================
    # 5. SKILLS
    # ============================================================

    normalized_haystack = haystack.lower()

    profile_skills = profile.get(
        "skills",
        [],
    )

    matches = [
        skill
        for skill in profile_skills
        if skill.lower() in normalized_haystack
    ]

    # ------------------------------------------------------------
    # Manual QA
    # ------------------------------------------------------------

    manual_re = re.compile(
        r"""
        (?:
            \bmanual\b
            |
            \bmanual\s+testing\b
            |
            \bmanual\s+qa\b
            |
            \bmanual\s+tester\b
            |
            \bfunctional\s+testing\b
            |
            \bregression\s+testing\b
            |
            \bsmoke\s+testing\b
            |
            \bтестировщик\b
            |
            \bручн(?:ое|ого|ым|ая)\s+тестирован
            |
            \bфункциональн(?:ое|ого)\s+тестирован
            |
            \bрегрессион(?:ное|ого)\s+тестирован
        )
        """,
        re.I | re.X,
    )

    # ------------------------------------------------------------
    # API / Backend
    # ------------------------------------------------------------

    backend_api_re = re.compile(
        r"""
        (?:
            \bapi\b
            |
            \brest\b
            |
            \bgraphql\b
            |
            \bbackend\b
            |
            \bback-end\b
            |
            \bserver-side\b
            |
            \bmicroservice\b
            |
            \bmicroservices\b
            |
            \bмикросервис
            |
            \bбэкенд\b
            |
            \bbackend\b
        )
        """,
        re.I | re.X,
    )

    manual_match = bool(
        manual_re.search(normalized_haystack)
    )

    backend_api_match = bool(
        backend_api_re.search(normalized_haystack)
    )

    # ============================================================
    # 6. SCORE
    # ============================================================

    # QA-вакансия, прошедшая hard filters,
    # уже является потенциально релевантной.

    score = 55

    # Если manual явно указан.
    if manual_match:
        score += 15

        reasons.append(
            "В вакансии подтверждено ручное тестирование."
        )

    # API/backend — один из основных профилей кандидата.
    if backend_api_match:
        score += 15

        reasons.append(
            "В вакансии есть API/backend-направление."
        )

    # Совпадение навыков.
    #
    # Максимум +10, чтобы количество технологий
    # не доминировало над ролью.

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

    # ------------------------------------------------------------
    # Дополнительный бонус за QA + API
    #
    # Это особенно важно для твоего профиля.
    # ------------------------------------------------------------

    if title_is_qa and backend_api_match:
        score += 5

        reasons.append(
            "QA + API является сильным совпадением "
            "с профилем кандидата."
        )

    vacancy.score = min(
        95,
        score,
    )

    # ============================================================
    # 7. FINAL STATUS
    # ============================================================

    if vacancy.score >= 75:
        vacancy.status = "recommended"
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