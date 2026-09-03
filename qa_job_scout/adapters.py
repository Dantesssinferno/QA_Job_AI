"""Site-specific vacancy adapters with resilient extraction and source-aware fallbacks."""
from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass

from playwright.async_api import BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .core import Vacancy


@dataclass(frozen=True)
class AdapterSpec:
    key: str
    name: str
    url: str
    card_selector: str
    link_selector: str
    title_selectors: tuple[str, ...]
    detail_title_selectors: tuple[str, ...] = ("h1",)
    detail_body_selectors: tuple[str, ...] = (
        "main",
        "article",
        "body",
    )
    detail_exclude_selectors: tuple[str, ...] = ()
    detail_cut_markers: tuple[str, ...] = ()
    date_selectors: tuple[str, ...] = (
        "time",
        "[class*='date']",
        "[class*='publish']",
    )
    detail_wait_selector: str = "h1"
    requires_login: bool = False
    detail_pages: bool = True


@dataclass
class SourceRun:
    source_key: str
    source_name: str
    listed: int = 0
    detailed: int = 0
    collected: int = 0
    errors: list[str] | None = None
    status: str = "ok"

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class BaseAdapter:
    spec: AdapterSpec

    page_timeout_ms = int(
        os.getenv("PAGE_TIMEOUT_MS", "20000")
    )

    selector_timeout_ms = int(
        os.getenv("SELECTOR_TIMEOUT_MS", "7000")
    )

    def __init__(self) -> None:
        self.spec = self.get_spec()

        self.max_vacancies = max(
            1,
            int(
                os.getenv("MAX_VACANCIES", "25")
            ),
        )

    def get_spec(self) -> AdapterSpec:
        raise NotImplementedError

    async def _goto(
        self,
        page: Page,
        url: str,
    ) -> None:
        """
        Безопасная навигация.

        Некоторые агрегаторы долго ждут сторонние ресурсы,
        поэтому сначала пробуем domcontentloaded, а после timeout
        используем commit и продолжаем работу с DOM.
        """
        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.page_timeout_ms,
            )
            return
        except PlaywrightTimeoutError:
            pass

        await page.goto(
            url,
            wait_until="commit",
            timeout=self.page_timeout_ms,
        )

    async def _first_text(
        self,
        page: Page,
        selectors: Iterable[str],
    ) -> str:
        for selector in selectors:
            locator = page.locator(selector).first

            try:
                if await locator.count() and await locator.is_visible():
                    text = (
                        await locator.inner_text()
                    ).strip()

                    if text:
                        return text

            except PlaywrightTimeoutError:
                continue

        return ""

    def _cut_unrelated_sections(
        self,
        text: str,
    ) -> str:
        """
        Обрезает описание после блока похожих/рекомендованных вакансий
        и других агрегаторских разделов.
        """
        if not text:
            return ""

        result = text

        for marker in self.spec.detail_cut_markers:
            index = result.lower().find(
                marker.lower()
            )

            if index >= 0:
                result = result[:index].strip()

        return result

    async def _extract_detail_body(
        self,
        page: Page,
    ) -> str:
        """
        Извлекает текст основной вакансии и удаляет известные
        нерелевантные DOM-блоки.
        """
        for selector in self.spec.detail_body_selectors:
            locator = page.locator(selector).first

            try:
                if not await locator.count():
                    continue

                if not await locator.is_visible():
                    continue

                if self.spec.detail_exclude_selectors:
                    await locator.evaluate(
                        """
                        (root, selectors) => {
                            for (const selector of selectors) {
                                root.querySelectorAll(selector)
                                    .forEach(node => node.remove());
                            }
                        }
                        """,
                        list(
                            self.spec.detail_exclude_selectors
                        ),
                    )

                text = (
                    await locator.inner_text()
                ).strip()

                if text:
                    return self._cut_unrelated_sections(
                        text
                    )

            except PlaywrightTimeoutError:
                continue

        return ""

    async def _extract_date_from_text(
        self,
        text: str,
    ) -> str:
        """
        Fallback для сайтов без стабильного date selector.

        Поддерживает формы:
        - сегодня / yesterday
        - 3 дн. назад
        - 3 дня назад
        - 13 ч. назад
        - 20 часов назад
        - 10 мин. назад
        - 31 авг
        - 27 августа 2026
        """
        if not text:
            return ""

        normalized = " ".join(
            text.split()
        )

        patterns = (
            r"\b(?:сегодня|today|вчера|yesterday)\b",
            r"\b\d+\s*(?:д\.|дн\.|день|дня|дней|days?)\s*(?:назад|ago)\b",
            r"\b\d+\s*(?:ч\.|час|часа|часов|hours?)\s*(?:назад|ago)\b",
            r"\b\d+\s*(?:мин\.|минут|минуты|minutes?)\s*(?:назад|ago)\b",
            r"\b\d{1,2}\s+[а-яё]{3,}(?:\s+\d{4})?\b",
            r"\b\d{1,2}\s+[a-z]{3,}(?:\s+\d{4})?\b",
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                normalized,
                re.IGNORECASE,
            )

            if match:
                return match.group(0)

        return ""

    async def list_cards(
        self,
        page: Page,
    ) -> list[dict[str, str]]:
        await self._goto(
            page,
            self.spec.url,
        )

        cards = page.locator(
            self.spec.card_selector
        )

        # Если контейнер карточки поменялся,
        # пробуем стабильные ссылки вакансий.
        if await cards.count() == 0:
            cards = page.locator(
                self.spec.link_selector
            )

        await cards.first.wait_for(
            state="attached",
            timeout=self.selector_timeout_ms,
        )

        return await cards.evaluate_all(
            """
            (cards, spec) => cards
                .slice(0, spec.limit)
                .map(card => {
                    const link =
                        card.matches(spec.link)
                            ? card
                            : card.querySelector(spec.link);

                    const pickText = (selectors) => {
                        for (const selector of selectors) {
                            const node =
                                card.querySelector(selector);

                            if (
                                node &&
                                node.innerText.trim()
                            ) {
                                return node.innerText.trim();
                            }
                        }

                        return '';
                    };

                    return {
                        href: link?.href || '',

                        title:
                            pickText(spec.titles)
                            ||
                            link?.getAttribute("aria-label")
                            ||
                            link?.innerText.trim()
                            ||
                            '',

                        text:
                            card.innerText.trim()
                    };
                })
                .filter(
                    item => item.href && item.title
                )
            """,
            {
                "link": self.spec.link_selector,
                "titles": list(
                    self.spec.title_selectors
                ),
                "limit": self.max_vacancies,
            },
        )

    def card_to_vacancy(
        self,
        card: dict[str, str],
    ) -> Vacancy:
        text = " ".join(
            card["text"].split()
        )

        return Vacancy(
            source=self.spec.name,
            title=" ".join(
                card["title"].split()
            ),
            url=card["href"],
            text=text,
            published_text=text,
            remote=any(
                word in text.lower()
                for word in (
                    "remote",
                    "удал",
                    "home office",
                    "global",
                )
            ),
        )

    async def read_detail(
        self,
        page: Page,
        card: dict[str, str],
    ) -> Vacancy:
        await self._goto(
            page,
            card["href"],
        )

        try:
            await page.locator(
                self.spec.detail_wait_selector
            ).first.wait_for(
                state="attached",
                timeout=self.selector_timeout_ms,
            )
        except PlaywrightTimeoutError:
            pass

        title = (
            await self._first_text(
                page,
                self.spec.detail_title_selectors,
            )
            or card["title"]
        )

        body = await self._extract_detail_body(
            page
        )

        if not body:
            body = card["text"]

        date = await self._first_text(
            page,
            self.spec.date_selectors,
        )

        # CSS selector мог не сработать. Тогда ищем дату
        # в очищенном основном тексте, а затем в карточке.
        if not date:
            date = await self._extract_date_from_text(
                body
            )

        if not date:
            date = await self._extract_date_from_text(
                card["text"]
            )

        all_text = (
            f"{card['text']}\n{body}"
        )

        return Vacancy(
            source=self.spec.name,
            title=" ".join(
                title.split()
            ),
            url=card["href"],
            text=" ".join(
                all_text.split()
            ),
            published_text=" ".join(
                date.split()
            ) if date else "",
            remote=any(
                word in all_text.lower()
                for word in (
                    "remote",
                    "удал",
                    "home office",
                    "global",
                )
            ),
        )

    async def _read_detail_safe(
        self,
        context: BrowserContext,
        card: dict[str, str],
        detail_semaphore: asyncio.Semaphore,
    ) -> Vacancy:
        async with detail_semaphore:
            page = await context.new_page()

            try:
                return await self.read_detail(
                    page,
                    card,
                )
            finally:
                await page.close()

    async def collect(
        self,
        page: Page,
        *,
        context: BrowserContext | None = None,
        detail_semaphore: asyncio.Semaphore | None = None,
    ) -> tuple[list[Vacancy], SourceRun]:
        run = SourceRun(
            self.spec.key,
            self.spec.name,
        )

        try:
            cards = await self.list_cards(
                page
            )
            run.listed = len(cards)

        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.errors.append(
                f"Список: "
                f"{type(exc).__name__}: {exc}"
            )
            return [], run

        if not cards:
            run.status = "failed"
            run.errors.append(
                "Карточки вакансий не найдены."
            )
            return [], run

        if not self.spec.detail_pages:
            vacancies = [
                self.card_to_vacancy(card)
                for card in cards
            ]

            run.collected = len(vacancies)
            return vacancies, run

        if (
            context is None
            or detail_semaphore is None
        ):
            vacancies: list[Vacancy] = []

            for card in cards:
                try:
                    vacancy = await self.read_detail(
                        page,
                        card,
                    )

                    run.detailed += 1
                    vacancies.append(vacancy)

                except Exception as exc:  # noqa: BLE001
                    run.errors.append(
                        f"{card['href']}: "
                        f"{type(exc).__name__}: {exc}"
                    )
        else:

            async def one(
                card: dict[str, str],
            ):
                try:
                    return await self._read_detail_safe(
                        context,
                        card,
                        detail_semaphore,
                    )
                except Exception as exc:  # noqa: BLE001
                    return card, exc

            results = await asyncio.gather(
                *(
                    one(card)
                    for card in cards
                )
            )

            vacancies = []

            for card, result in zip(
                cards,
                results,
            ):
                if isinstance(result, tuple):
                    _, exc = result

                    run.errors.append(
                        f"{card['href']}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                else:
                    run.detailed += 1
                    vacancies.append(result)

        run.collected = len(vacancies)

        if run.errors and not vacancies:
            run.status = "failed"
        elif run.errors:
            run.status = "partial"

        return vacancies, run


# ============================================================
# COMMON CLEANUP MARKERS
# ============================================================


GENERIC_EXCLUDE_SELECTORS = (
    "[class*='similar']",
    "[class*='recommended']",
    "[class*='recommend']",
    "[class*='related']",
)


GENERIC_CUT_MARKERS = (
    "Похожие вакансии",
    "Рекомендуемые вакансии",
    "Рекомендованные вакансии",
    "Больше вакансий",
    "Ещё больше вакансий",
    "Еще больше вакансий",
    "Другие вакансии",
    "Similar jobs",
    "Similar vacancies",
    "Recommended jobs",
    "Recommended vacancies",
    "Related jobs",
    "Related vacancies",
)


# ============================================================
# HIREHI
# ============================================================


class HireHiAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="hirehi",
            name="HireHi",
            url=(
                "https://hirehi.ru/vacancies/"
                "manual-qa?"
                "format=%D1%83%D0%B4%D0%B0%D0%BB%D1%91%D0%BD%D0%BD%D0%BE"
                "&search=QA"
            ),
            card_selector="a.job-card[data-id]",
            link_selector="a.job-card[data-id]",
            title_selectors=(
                "[class*='title']",
                "h2",
                "h3",
            ),
            detail_body_selectors=(
                "main",
                "[class*='vacancy']",
                "article",
                "body",
            ),
            detail_exclude_selectors=GENERIC_EXCLUDE_SELECTORS,
            detail_cut_markers=(
                *GENERIC_CUT_MARKERS,
                "Мэтч & Сопровод",
                "Статьи для QA-инженеров",
                "статьи для QA-инженеров",
                "Про зарплаты",
            ),
            date_selectors=(
                ".vacancy-published-date",
                "[class*='vacancy-published-date']",
                "time",
                "[class*='date']",
                "[class*='publish']",
            ),
        )


# ============================================================
# ROCKETHUNT
# ============================================================


class RocketHuntAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="rockethunt",
            name="RocketHunt",
            url="https://rockethunt.ai/ru?text=QA",
            card_selector="article.cv-card",
            link_selector=(
                "a[href*='/jobs/'], "
                "a[href*='/job/'], "
                "a[href*='/vacancies/'], "
                "a[href*='/vacancy/']"
            ),
            title_selectors=(
                "h2",
                "h3",
                "[class*='title']",
            ),
            detail_body_selectors=(
                "main",
                "article",
                "body",
            ),
            detail_exclude_selectors=GENERIC_EXCLUDE_SELECTORS,
            detail_cut_markers=GENERIC_CUT_MARKERS,
        )


# ============================================================
# DREAMJOB
# ============================================================


class DreamJobAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="dreamjob",
            name="DreamJob",
            url=(
                "https://dreamjob.ru/vakansii?"
                "jbfrp%5Btext%5D=QA"
                "&jbfrp%5Bsalary%5D="
                "&jbfrp%5BonlyWithSalary%5D=0"
                "&jbfrp%5BorderBy%5D=relevance"
            ),
            card_selector=(
                "div.vacancy-new.vacancy-new__item"
            ),
            link_selector=(
                "a[href*='/vacancy']"
            ),
            title_selectors=(
                "h2",
                "h3",
                "[class*='title']",
            ),
            detail_body_selectors=(
                "main",
                "[class*='vacancy']",
                "article",
                "body",
            ),
            detail_exclude_selectors=GENERIC_EXCLUDE_SELECTORS,
            detail_cut_markers=GENERIC_CUT_MARKERS,
        )


# ============================================================
# HIRIFY
# ============================================================


class HirifyAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="hirify",
            name="Hirify",
            url=(
                "https://hirify.me/?"
                "countries=russia,serbia,ukraine,"
                "armenia,romania,cyprus,latvia,"
                "czech_republic,kazakhstan,europe,"
                "georgia,turkey,croatia,azerbaijan,"
                "uzbekistan,vietnam,belarus,bulgaria,"
                "thailand,moldova"
                "&regions=russia,europe"
                "&remote_type=global"
                "&skills_match_type=OR"
                "&specializations=qa_testing"
                "&work_format=remote"
            ),
            card_selector=(
                "div.vacancy-card[data-vacancy-id]"
            ),
            link_selector=(
                "a[href*='/jobs/']"
            ),
            title_selectors=(
                "h2",
                "h3",
                "[class*='title']",
            ),
            detail_body_selectors=(
                "main",
                "[class*='vacancy']",
                "article",
                "body",
            ),
            detail_exclude_selectors=GENERIC_EXCLUDE_SELECTORS,
            detail_cut_markers=(
                *GENERIC_CUT_MARKERS,
                "Мэтч & Сопровод",
                "Реклама",
            ),
            date_selectors=(
                "div.font-light.text-tertiary",
                "div[class*='text-tertiary']",
                "time",
                "[class*='date']",
                "[class*='publish']",
            ),
        )


# ============================================================
# TAYLOR
# ============================================================


class TaylorAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="taylor",
            name="Taylor",
            url=(
                "https://taylor.kz/jobs/stack/qa"
                "?q="
                "&stack=qa"
                "&source="
                "&city="
                "&remote="
                "&sort=recent"
                "&seniority="
                "&format="
                "&salary="
                "&direct="
                "&region="
                "&hide_applied="
                "&hide_viewed="
                "&with_salary="
                "&recent="
            ),
            card_selector=(
                'a[href*="/jobs/"]'
                '[aria-label^="Открыть вакансию:"]'
            ),
            link_selector=(
                'a[href*="/jobs/"]'
                '[aria-label^="Открыть вакансию:"]'
            ),
            title_selectors=(
                "h2",
                "h3",
                "[class*='title']",
            ),
            detail_body_selectors=(
                "main",
                "article",
                "body",
            ),
            detail_exclude_selectors=(
                *GENERIC_EXCLUDE_SELECTORS,
                "[class*='salary']",
            ),
            detail_cut_markers=(
                *GENERIC_CUT_MARKERS,
                "Смотреть подборки",
                "Вакансии в Telegram-канале",
            ),
            date_selectors=(
                "time",
                "[class*='published']",
                "[class*='date']",
                "[class*='publish']",
            ),
        )


# ============================================================
# JOBROCKET
# ============================================================


class JobRocketAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="jobrocket",
            name="JobRocket",
            url=(
                "https://jobrocket.ru/en?"
                "page=1&categories=qa"
            ),
            card_selector='div[data-slot="card"]',
            link_selector=(
                "a[href*='/job']"
            ),
            title_selectors=(
                "h2",
                "h3",
                "[class*='title']",
            ),
            detail_body_selectors=(
                "main",
                "article",
                "body",
            ),
            detail_exclude_selectors=GENERIC_EXCLUDE_SELECTORS,
            detail_cut_markers=GENERIC_CUT_MARKERS,
        )


# ============================================================
# TALANTO
# ============================================================


class TalantoAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="talanto",
            name="Talanto",
            url=(
                "https://talanto.work/"
                "?offset=0&q=QA"
            ),
            card_selector=(
                'a[aria-label][href^="/jobs/"]'
            ),
            link_selector=(
                'a[aria-label][href^="/jobs/"]'
            ),
            title_selectors=(
                "h2",
                "h3",
                "[class*='title']",
            ),
            detail_body_selectors=(
                "main",
                "article",
                "body",
            ),
            detail_exclude_selectors=(
                *GENERIC_EXCLUDE_SELECTORS,
                "[class*='article']",
            ),
            detail_cut_markers=(
                *GENERIC_CUT_MARKERS,
            ),
            date_selectors=(
                "time",
                "[class*='date']",
                "[class*='published']",
                "[class*='publish']",
            ),
        )


# ============================================================
# GETMATCH
# ============================================================


class GetMatchAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="getmatch",
            name="GetMatch",
            url=(
                "https://getmatch.ru/vacancies"
                "?p=1"
                "&sa=any"
                "&pa=all"
                "&l=remote"
                "&sp=qa_manual"
            ),
            card_selector=(
                "div.b-vacancy-card"
            ),
            link_selector=(
                "a[href*='/vacancies/']"
            ),
            title_selectors=(
                "h2",
                "h3",
                "[class*='title']",
            ),
            detail_body_selectors=(
                "main",
                "[class*='vacancy']",
                "article",
                "body",
            ),
            detail_exclude_selectors=GENERIC_EXCLUDE_SELECTORS,
            detail_cut_markers=(
                *GENERIC_CUT_MARKERS,
                "Больше вакансий",
                "Похожие вакансии",
                "Ещё 17 похожих вакансий",
                "Ещё вакансии",
            ),
        )


# ============================================================
# GEEKJOB
# ============================================================


class GeekJobAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="geekjob",
            name="GeekJob",
            url=(
                "https://geekjob.ru/vacancies"
                "?rm=1"
                "&t=2,32,276,277,278,279,45"
            ),
            card_selector=(
                "li.collection-item.avatar"
            ),
            link_selector=(
                "a[href*='/vacancy']"
            ),
            title_selectors=(
                "h2",
                "h3",
                ".title",
                "[class*='title']",
            ),
            detail_body_selectors=(
                "main",
                "article",
                "body",
            ),
            detail_exclude_selectors=GENERIC_EXCLUDE_SELECTORS,
            detail_cut_markers=(
                *GENERIC_CUT_MARKERS,
                "Еще интересные вакансии",
                "Ещё интересные вакансии",
                "Другие вакансии",
            ),
        )


# ============================================================
# RVC
# ============================================================


class RVCAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="rvc",
            name="RVC",
            url=(
                "https://app.rvc.global/jobs?"
                "salaryFloor=off"
                "&keyword=QA"
                "&jobFunction=QA"
                "&keyCompetency=Other+%2F+Unspecified"
                "&workArrangement=REMOTE_IN_COUNTRY"
                "&includeLanguages=RU%2CEN%2CGB"
                "&employmentType=FULL_TIME"
                "&countries=Cyprus%2CCzech+Republic%2CGeorgia"
                "%2CGreece%2CLatvia%2CPoland%2CPortugal"
                "%2CRomania%2CSlovakia%2CSerbia%2CUkraine"
                "%2CArmenia%2CAzerbaijan%2CBelarus"
                "%2CKazakhstan%2CKyrgyzstan%2CMoldova"
                "%2CRussia%2CTajikistan%2CTurkmenistan"
                "%2CUzbekistan"
            ),
            card_selector=(
                "article, "
                "[class*='job-card'], "
                "[class*='vacancy-card']"
            ),
            link_selector=(
                "a[href*='/jobs/']"
            ),
            title_selectors=(
                "h2",
                "h3",
                "[class*='title']",
            ),
            detail_body_selectors=(
                "main",
                "article",
                "body",
            ),
            detail_exclude_selectors=GENERIC_EXCLUDE_SELECTORS,
            detail_cut_markers=GENERIC_CUT_MARKERS,
        )


# ============================================================
# LINKEDIN
# ============================================================


class LinkedInAdapter(BaseAdapter):

    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="linkedin",
            name="LinkedIn",
            url="https://www.linkedin.com/feed/",
            card_selector=(
                'p[data-testid="expandable-text-box"]'
            ),
            link_selector="a[href]",
            title_selectors=(
                "[data-testid='expandable-text-box']",
            ),
            detail_body_selectors=(
                "main",
                "body",
            ),
            requires_login=True,
            detail_pages=False,
        )


def enabled_adapters() -> tuple[BaseAdapter, ...]:
    return (
        HireHiAdapter(),
        RocketHuntAdapter(),
        DreamJobAdapter(),
        HirifyAdapter(),
        TaylorAdapter(),
        JobRocketAdapter(),
        TalantoAdapter(),
        GetMatchAdapter(),
        GeekJobAdapter(),
        RVCAdapter(),
        LinkedInAdapter(),
    )
