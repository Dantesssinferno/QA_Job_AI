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
        \bспециалист\s+по\s+функциональн(?:ому|ой|ое|ого|ым|ыми)\s+тестирован(?:ие|ию|ием|ия|ии)\b
        |
        \bфункциональн(?:ый|ая|ое|ого|ой|ым|ыми)\s+тестирован(?:ие|ию|ием|ия|ии|ию)\b
        |
        \bфункциональн(?:ый|ая|ое|ого|ой|ым|ыми)\s+тестировщик(?:а|у|ом|и|ов|ами)?\b
        |
        \bтестировщик\s+функциональн(?:ый|ая|ое|ого|ой|ым|ыми)\b
        |
        \bинженер\s+по\s+качеству\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ============================================================
# HARD NEGATIVE ROLE KEYWORDS
# ============================================================

# Явно нерелевантные профессии.
#
# Они проверяются ДО QA_TITLE_RE.
# Это важно для названий вроде:
#   DevOps Engineer
#   Backend Engineer
#   Data Analyst
#   System Analyst
#   Fullstack Developer
#
# Даже если внутри description написано QA/testing,
# такая вакансия не должна становиться QA-вакансией.


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
    re.IGNORECASE | re.VERBOSE,
)


# ============================================================
# NON-MANUAL QA ROLE DETECTION
# ============================================================

# ВАЖНО:
#
# Здесь НЕ ищем просто:
#   automation
#   автоматизация
#   автотесты
#
# Потому что Manual QA вакансия может содержать:
#
#   "Automation is a plus"
#   "Automation may be introduced later"
#   "Automation can become a supporting tool"
#   "You will work with the Automation QA team"
#   "автоматизация может появиться позже"
#
# Это не означает, что сама вакансия является Automation QA.
#
# Поэтому найденное совпадение дополнительно проверяется
# по контексту.


NON_MANUAL_ROLE_RE = re.compile(
    r"""
    (?:
        # Fullstack / Full-stack / русские варианты
        \bfull\s*[-/]?\s*stack\b
        |
        \bfullstack\b
        |
        \bфул[\s-]?ст(?:э|е)к\b
        |
        \bфул[\s-]?стак\b
        |
        \bфул[\s-]?л[\s-]?ст(?:э|е)к\b

        |

        # AQA / SDET как отдельная роль
        \baqa\b
        |
        \bsdet\b
        |
        \bsoftware\s+development\s+engineer\s+in\s+test\b

        |

        # Automation только как явная должность / направление
        \bautomation\s+qa\b
        |
        \bqa\s+automation\b
        |
        \bautomation\s+tester\b
        |
        \bautomation\s+engineer\b
        |
        \btest\s+automation\s+engineer\b
        |
        \bqa\s+automation\s+engineer\b
        |
        \bautomation\s+specialist\b
        |
        \bautomation\s+developer\b

        |

        # Русские названия самостоятельной automation-роли
        \bавтоматизатор\b
        |
        \bинженер\s+по\s+автоматизации\s+тестирования\b
        |
        \bспециалист\s+по\s+автоматизации\s+тестирования\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ============================================================
# BENIGN AUTOMATION CONTEXT
# ============================================================

BENIGN_AUTOMATION_CONTEXT_RE = re.compile(
    r"""
    (?:
        (?:automation|автоматизац|автотест)
        .{0,80}
        (?:plus|nice\s+to\s+have|would\s+be\s+a\s+plus|будет\s+плюсом|является\s+плюсом|приветствуется|желательно)
        |
        (?:позже|потом|в\s+будущем|со\s+временем|планируется|может\s+появиться|вспомогательн|дополнительн|как\s+инструмент)
        .{0,80}
        (?:automation|автоматизац|автотест)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ============================================================
# AUTOMATION REQUIRED / AUTOMATION AS CORE RESPONSIBILITY
# ============================================================

AUTOMATION_REQUIRED_RE = re.compile(
    r"""
    (?:
        (?:automation|автоматизац|автотест)
        .{0,60}
        (?:required|обязател|must|необходим|нужен|нужно)
        |
        (?:required|обязател|must|необходим|нужен|нужно)
        .{0,60}
        (?:automation|автоматизац|автотест)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# Прямая обязанность писать/поддерживать автотесты.
# Это уже не просто упоминание automation, а существенная
# часть роли Manual QA.
AUTOMATION_RESPONSIBILITY_RE = re.compile(
    r"""
    (?:
        (?:писать|разрабатывать|создавать|поддерживать|развивать|покрывать)
        .{0,50}
        (?:автотест|автоматизированн|automation|automated\s+tests?)
        |
        (?:автотест|автоматизированн|automation|automated\s+tests?)
        .{0,50}
        (?:писать|разрабатывать|создавать|поддерживать|развивать|покрывать)
        |
        (?:automated\s+testing|автоматизированное\s+тестирование)
        .{0,50}
        (?:responsibil|обязанност|задач|development|разработ)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


REMOTE_RE = re.compile(
    r"\b(remote|удал[её]н|работа из дома|home office)\b",
    re.IGNORECASE,
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
    re.IGNORECASE | re.VERBOSE,
)


# ============================================================
# AUTOMATION REQUIRED
# ============================================================

# Здесь automation действительно считается hard filter,
# только если она указана как ОБЯЗАТЕЛЬНАЯ.
#
# Примеры, которые будут отклонены:
#   Automation experience required
#   Must have automation experience
#   Автоматизация обязательна
#
# Примеры, которые НЕ будут отклонены:
#   Automation is a plus
#   Automation experience would be nice
#   Work with automation team


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
    re.IGNORECASE | re.VERBOSE,
)


# ============================================================
# DATE PARSING
# ============================================================


DATE_PATTERNS = (
    (
        re.compile(
            r"(сегодня|today)",
            re.IGNORECASE,
        ),
        0,
    ),
    (
        re.compile(
            r"(вчера|yesterday)",
            re.IGNORECASE,
        ),
        1,
    ),
    (
        re.compile(
            r"(\d+)\s*(?:мин(?:ут[ыа]?)?|мин\.?|minutes?)\s*(?:назад|ago)?",
            re.IGNORECASE,
        ),
        "minutes",
    ),
    (
        re.compile(
            r"(\d+)\s*(?:ч(?:ас(?:а|ов)?)?|ч\.?|hours?)\s*(?:назад|ago)?",
            re.IGNORECASE,
        ),
        "hours",
    ),
    (
        re.compile(
            r"(\d+)\s*(?:д(?:ень|ня|ней)?|дн\.?|д\.?|days?)\s*(?:назад|ago)?",
            re.IGNORECASE,
        ),
        "days",
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


# ============================================================
# DATA MODEL
# ============================================================


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
        return hashlib.sha256(
            self.url.encode()
        ).hexdigest()[:12]


# ============================================================
# DATE PARSER
# ============================================================


def parse_age(
    text: str,
    now: datetime | None = None,
) -> datetime | None:
    now = now or datetime.now(UTC)

    clean = " ".join(text.split())

    # HH.ru publishes exact ISO-8601 timestamps such as:
    # 2026-09-04T09:00:00+03:00
    # Parse those first because an API adapter should not convert
    # an exact timestamp into an approximate age phrase.
    try:
        iso_match = re.search(
            r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})\b",
            clean,
        )
        if iso_match:
            parsed = datetime.fromisoformat(
                iso_match.group(0).replace("Z", "+00:00")
            )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    except ValueError:
        pass

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

            if delta == "days":
                return now - timedelta(
                    days=int(match.group(1))
                )

            return now - timedelta(
                days=delta
            )

    month_match = re.search(
        r"(?:^|\s)(\d{1,2})\s+"
        r"(янв(?:аря)?|фев(?:раля)?|мар(?:та)?|апр(?:еля)?|"
        r"ма[йя]|июн(?:я)?|июл(?:я)?|авг(?:уста)?|"
        r"сен(?:тября)?|окт(?:ября)?|ноя(?:бря)?|дек(?:абря)?|"
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
        r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"(?:\s+(\d{4}))?(?=\s|$)",
        clean,
        re.IGNORECASE,
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

    # Некоторые агрегаторы выводят возраст публикации без слова
    # "назад": "5 лет", "2 года", "3 weeks".
    # Поддерживаем только явные единицы возраста.
    age_match = re.search(
        r"\b(\d+)\s*(?:лет|года|год|years?|недел(?:я|и|ь)|weeks?)\b",
        clean,
        re.IGNORECASE,
    )

    if age_match:
        value = int(age_match.group(1))
        unit = age_match.group(0).lower()

        if "week" in unit or "недел" in unit:
            return now - timedelta(days=value * 7)

        return now - timedelta(days=value * 365)

    age_match = re.search(
        r"\b(\d+)\s*(?:лет|года|год|years?|недел(?:я|и|ь)|weeks?)\b",
        clean,
        re.IGNORECASE,
    )

    if age_match:
        value = int(age_match.group(1))
        unit = age_match.group(0).lower()

        if "week" in unit or "недел" in unit:
            return now - timedelta(days=value * 7)

        return now - timedelta(days=value * 365)

    return None


# ============================================================
# CONTEXT-AWARE ROLE CHECK
# ============================================================


def _is_benign_role_match(
    text: str,
    match: re.Match[str],
) -> bool:
    """
    Определяет, является ли найденное automation/fullstack/AQA
    упоминание контекстом, а не основной ролью вакансии.

    Пример benign:

        "Автоматизация может появиться позже как вспомогательный инструмент."

    Пример NOT benign:

        "Ищем Senior Automation QA Engineer."
    """
    start = max(0, match.start() - 120)
    end = min(len(text), match.end() + 120)

    context = text[start:end]

    return bool(
        BENIGN_AUTOMATION_CONTEXT_RE.search(
            context
        )
    )


def _find_non_manual_role_match(
    title: str,
    description: str,
) -> re.Match[str] | None:
    """
    Ищет признаки Automation/AQA/SDET/Fullstack.

    В TITLE совпадение считается жёстким:
        Automation QA
        AQA Engineer
        SDET
        Fullstack QA

    В DESCRIPTION совпадение проверяется по контексту.
    Это позволяет не отклонять Manual QA вакансии, где
    automation упоминается как будущее направление,
    дополнительный навык или взаимодействие с другой командой.
    """
    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title_match = NON_MANUAL_ROLE_RE.search(title)

    if title_match:
        return title_match

    # --------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------

    for match in NON_MANUAL_ROLE_RE.finditer(
        description
    ):
        if not _is_benign_role_match(
            description,
            match,
        ):
            return match

    return None


# ============================================================
# EVALUATION
# ============================================================


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
       Fullstack / AQA / Automation / SDET отклоняются,
       если они обозначают отдельную роль.

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

    title = " ".join(
        vacancy.title.split()
    )

    description = " ".join(
        vacancy.text.split()
    )

    # Для поиска требований и навыков используем title + description.
    # Но профессию определяем ТОЛЬКО по title.
    haystack = (
        f"{title}\n{description}"
    )

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
    # Fullstack Developer
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
    title_is_qa = bool(
        QA_TITLE_RE.search(title)
    )

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

    # ------------------------------------------------------------
    # 2.1 Fullstack / AQA / Automation / SDET
    # ------------------------------------------------------------

    # В TITLE такие роли считаются жёстким отрицанием.
    #
    # В DESCRIPTION дополнительно учитываем контекст.
    #
    # Поэтому:
    #
    #   "Automation QA" в title
    #       -> rejected
    #
    #   "Automation QA team" в description
    #       -> может быть пропущено
    #
    #   "Automation may appear later"
    #       -> пропускается
    #
    #   "Automation is a plus"
    #       -> пропускается
    #
    #   "Main responsibility is writing automated tests"
    #       -> rejected

    non_manual_role_match = (
        _find_non_manual_role_match(
            title=title,
            description=description,
        )
    )

    if non_manual_role_match:
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            *reasons,
            (
                "В вакансии обнаружена нерелевантная "
                f"роль/направление: "
                f"{non_manual_role_match.group(0)}."
            ),
        ]
        return vacancy

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
            "Автоматизация указана как обязательное требование.",
        ]
        return vacancy

    if AUTOMATION_RESPONSIBILITY_RE.search(description):
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            *reasons,
            "Автоматизация является существенной частью обязанностей вакансии.",
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
        or bool(
            REMOTE_RE.search(
                haystack
            )
        )
    )

    if not remote_is_confirmed:
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            *reasons,
            "Удалённый формат не подтверждён.",
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
            "Английский указан как обязательное требование.",
        ]
        return vacancy

    reasons.append(
        "Английский не указан как обязательное требование."
    )

    # ============================================================
    # 4. DATE
    # ============================================================

    # Дата должна приходить от crawler-а из отдельного поля.
    #
    # Не пытаемся угадывать дату по всему description:
    # в описании могут встречаться даты, сроки, числа опыта,
    # даты обновлений и данные из блоков рекомендаций.
    date_source = " ".join(
        vacancy.published_text.split()
    )

    date = parse_age(
        date_source,
        now,
    ) if date_source else None

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
            "Не удалось надёжно определить дату публикации.",
        ]
        return vacancy

    age = now - date

    if age > timedelta(days=5):
        vacancy.status = "rejected"
        vacancy.score = 0
        vacancy.reasons = [
            *reasons,
            "Вакансии больше пяти дней.",
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
        re.IGNORECASE | re.VERBOSE,
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
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    manual_match = bool(
        manual_re.search(
            normalized_haystack
        )
    )

    backend_api_match = bool(
        backend_api_re.search(
            normalized_haystack
        )
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


# ============================================================
# DETERMINISTIC COVER LETTER
# ============================================================


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


# ============================================================
# PROFILE
# ============================================================


def load_profile(
    path: str = "candidate_profile.json",
) -> dict:
    return json.loads(
        Path(path).read_text(
            encoding="utf-8"
        )
    )


# ============================================================
# JSON
# ============================================================


def vacancy_json(
    v: Vacancy,
) -> str:
    return json.dumps(
        asdict(v),
        ensure_ascii=False,
    )
