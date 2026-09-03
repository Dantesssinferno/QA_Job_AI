"""Site-specific vacancy adapters with resilient extraction and source-aware validation."""
from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

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
        "[datetime]",
    )
    date_fallback_to_detail_text: bool = True
    date_fallback_to_card_text: bool = True
    vacancy_url_pattern: str | None = None
    excluded_url_patterns: tuple[str, ...] = ()
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

    detail_retries = max(
        1,
        int(os.getenv("DETAIL_RETRIES", "3")),
    )

    def __init__(self) -> None:
        self.spec = self.get_spec()

        self.max_vacancies = max(
            1,
            int(
                os.getenv(
                    "MAX_VACANCIES",
                    "25",
                )
            ),
        )

    def get_spec(self) -> AdapterSpec:
        raise NotImplementedError

    async def _goto(
        self,
        page: Page,
        url: str,
    ):
        """Open a page and return the HTTP response when available."""
        try:
            return await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.page_timeout_ms,
            )
        except PlaywrightTimeoutError:
            return await page.goto(
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
        """Extract only the main vacancy content and remove known noise."""
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
                        list(self.spec.detail_exclude_selectors),
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
        """Extract a recognizable publication-age/date phrase from text."""
        if not text:
            return ""

        normalized = " ".join(
            text.split()
        )

        patterns = (
            r"\b(?:сегодня|today|вчера|yesterday)\b",
            r"\b\d+\s*(?:дн?\.|дн|день|дня|дней|days?)\s*(?:назад|ago)\b",
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

    def is_valid_vacancy_url(
        self,
        url: str,
    ) -> bool:
        """Return True only for URLs matching this source's vacancy route."""
        if not url:
            return False

        normalized = url.split("#", 1)[0]
        parsed = urlparse(normalized)

        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            return False

        if any(
            pattern.lower() in normalized.lower()
            for pattern in self.spec.excluded_url_patterns
        ):
            return False

        if self.spec.vacancy_url_pattern:
            return bool(
                re.search(
                    self.spec.vacancy_url_pattern,
                    normalized,
                    re.IGNORECASE,
                )
            )

        return True

    @staticmethod
    def _is_http_error_response(response) -> bool:
        if response is None:
            return False

        try:
            return response.status >= 400
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _looks_like_error_page(title: str) -> bool:
        normalized = " ".join(
            title.split()
        ).lower()

        return any(
            marker in normalized
            for marker in (
                "429 too many requests",
                "too many requests",
                "403 forbidden",
                "404 not found",
                "502 bad gateway",
                "503 service unavailable",
                "gateway timeout",
            )
        )

    async def _extract_cards_from_locator(
        self,
        cards,
    ) -> list[dict[str, str]]:
        return await cards.evaluate_all(
            """
            (cards, spec) => cards
                .map(card => {
                    const link = card.matches(spec.link)
                        ? card
                        : card.querySelector(spec.link);

                    const pickText = (selectors) => {
                        for (const selector of selectors) {
                            const node = card.querySelector(selector);

                            if (node && node.innerText.trim()) {
                                return node.innerText.trim();
                            }
                        }

                        return '';
                    };

                    return {
                        href: link?.href || '',
                        title:
                            pickText(spec.titles)
                            || link?.getAttribute('aria-label')
                            || link?.innerText.trim()
                            || '',
                        text: card.innerText.trim(),
                    };
                })
                .filter(item => item.href && item.title)
            """,
            {
                "link": self.spec.link_selector,
                "titles": list(
                    self.spec.title_selectors
                ),
            },
        )

    def _clean_card_results(
        self,
        cards: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Validate links, remove obvious error pages, and deduplicate URLs."""
        clean: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for card in cards:
            href = card.get("href", "").strip()
            title = " ".join(
                card.get("title", "").split()
            )

            if not self.is_valid_vacancy_url(href):
                continue

            if self._looks_like_error_page(title):
                continue

            canonical = href.split("#", 1)[0]

            if canonical in seen_urls:
                continue

            seen_urls.add(canonical)

            clean.append(
                {
                    "href": canonical,
                    "title": title,
                    "text": card.get("text", "").strip(),
                }
            )

            if len(clean) >= self.max_vacancies:
                break

        return clean

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

        if await cards.count():
            raw_cards = await self._extract_cards_from_locator(
                cards
            )

            clean_cards = self._clean_card_results(
                raw_cards
            )

            # Selector may match navigation/category blocks.
            # In that case retry through the more explicit vacancy links.
            if clean_cards:
                return clean_cards

        links = page.locator(
            self.spec.link_selector
        )

        if await links.count() == 0:
            return []

        raw_links = await self._extract_cards_from_locator(
            links
        )

        return self._clean_card_results(
            raw_links
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
        response = await self._goto(
            page,
            card["href"],
        )

        if self._is_http_error_response(response):
            raise RuntimeError(
                f"HTTP {response.status} при открытии вакансии"
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

        title = await self._first_text(
            page,
            self.spec.detail_title_selectors,
        ) or card["title"]

        if self._looks_like_error_page(title):
            raise RuntimeError(
                f"Страница вернула ошибку: {title}"
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

        if not date and self.spec.date_fallback_to_detail_text:
            date = await self._extract_date_from_text(
                body
            )

        if not date and self.spec.date_fallback_to_card_text:
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
        last_error: Exception | None = None

        for attempt in range(1, self.detail_retries + 1):
            async with detail_semaphore:
                page = await context.new_page()

                try:
                    return await self.read_detail(
                        page,
                        card,
                    )

                except Exception as exc:  # noqa: BLE001
                    last_error = exc

                finally:
                    await page.close()

            if attempt < self.detail_retries:
                await asyncio.sleep(
                    min(2 * attempt, 5)
                )

        assert last_error is not None
        raise last_error

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
            cards = await self.list_cards(page)
            run.listed = len(cards)

        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.errors.append(
                f"Список: {type(exc).__name__}: {exc}"
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

        if context is None or detail_semaphore is None:
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
            async def one(card: dict[str, str]):
                try:
                    return await self._read_detail_safe(
                        context,
                        card,
                        detail_semaphore,
                    )
                except Exception as exc:  # noqa: BLE001
                    return card, exc

            results = await asyncio.gather(
                *(one(card) for card in cards)
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
    "[class*='seo-links']",
    "[class*='more-vacancies']",
)

GENERIC_CUT_MARKERS = (
    "Похожие вакансии",
    "Рекомендуемые вакансии",
    "Рекомендованные вакансии",
    "Больше вакансий",
    "Ещё больше вакансий",
    "Еще больше вакансий",
    "Другие вакансии",
    "Еще интересные вакансии",
    "Ещё интересные вакансии",
    "Similar jobs",
    "Similar vacancies",
    "Recommended jobs",
    "Recommended vacancies",
    "Related jobs",
    "Related vacancies",
    "More jobs",
    "More vacancies",
)


class HireHiAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="hirehi",
            name="HireHi",
            url="https://hirehi.ru/vacancies/manual-qa",
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
                "[datetime]",
            ),
        )


class RocketHuntAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="rockethunt",
            name="RocketHunt",
            url="https://rockethunt.ai/ru?text=QA",
            card_selector="article.cv-card",
            link_selector="a[href]",
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
            excluded_url_patterns=(
                "/vakansii/vacancy-testirovschik",
            ),
            date_selectors=(
                "time",
                "[datetime]",
            ),
        )


class DreamJobAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="dreamjob",
            name="DreamJob",
            url="https://dreamjob.ru/vakansii/vacancy-testirovschik?jbfrp%5Btext%5D=QA",
            card_selector="div.vacancy-new.vacancy-new__item",
            link_selector=(
                "a[href*='/vakansii/'], a[href*='/vacancy/'],"
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
            excluded_url_patterns=(
                "/vakansii/vacancy-",
            ),
            date_selectors=(
                "time",
                "[datetime]",
                "[class*='published']",
            ),
        )


class HirifyAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="hirify",
            name="Hirify",
            url=(
                "https://hirify.me/?countries=russia,ukraine,poland,armenia,spain,romania,cyprus,"
                "czech_republic,latvia,kazakhstan,portugal,netherlands,georgia,turkey,croatia,germany,"
                "azerbaijan,bulgaria,belarus,vietnam,uzbekistan,greece,slovakia,lithuania,thailand,"
                "kyrgyzstan,moldova,turkmenistan,tajikistan,limassol,yerevan,tbilisi,belgrade,astana,"
                "slovenia,riga&domains=fintech,gamedev&excluded_skills=manual%20qa&regions=russia,cis&"
                "skills=qa,python&specializations=qa_testing"
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
                "[datetime]",
            ),
            vacancy_url_pattern=(
                r"^https?://(?:www\.)?hirify\.me/jobs/"
                r"\d+-[^/?#]+(?:[?#].*)?$"
            ),
        )


class TaylorAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="taylor",
            name="Taylor",
            url=(
                "https://taylor.kz/jobs/stack/qa?q=&stack=qa&source=&city=&remote="
                "&sort=recent&seniority=&format=&salary=&direct=&region=&hide_applied="
                "&hide_viewed=&with_salary=&recent="
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
                "[datetime]",
            ),
            vacancy_url_pattern=(
                r"^https?://(?:www\.)?taylor\.kz/jobs/"
                r"[^/?#]+(?:[?#].*)?$"
            ),
        )


class JobRocketAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="jobrocket",
            name="JobRocket",
            url="https://jobrocket.ru/en?page=1&categories=qa",
            card_selector='div[data-slot="card"]',
            link_selector=(
                'a[href*="/job/"]'
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
            date_selectors=(
                "time",
                "[datetime]",
            ),
        )


class TalantoAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="talanto",
            name="Talanto",
            url=(
                "https://talanto.work/?q=QA&vacancy_langs=Ru"
                "&regions=Russia&regions=Europe&regions=Belarus"
                "&regions=Kazakhstan&regions=Uzbekistan&regions=Ukraine"
                "&regions=Georgia&regions=Armenia&regions=Azerbaijan"
                "&regions=Kyrgyzstan&regions=Moldova&regions=Tajikistan"
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
            detail_cut_markers=GENERIC_CUT_MARKERS,
            date_selectors=(
                "time",
                "[datetime]",
            ),
        )


class GetMatchAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="getmatch",
            name="GetMatch",
            url=(
                "https://getmatch.ru/vacancies"
                "?p=1&sa=any&pa=7d&l=remote"
                "&se=junior&se=middle&se=senior&sp=qa_manual"
            ),
            card_selector=(
                "div.b-vacancy-card"
            ),
            link_selector=(
                "div.b-vacancy-card a[href*='/vacancies/']"
            ),
            excluded_url_patterns=(
                "?s=vacancies_seo_links_",
                "?s=vacancies_seo_links_more_vacancies",
                "?s=vacancies_seo_links_similar_vacancies",
                "/vacancies/qa_manual/",
                "/vacancies/moscow",
                "/vacancies/spb",
                "/vacancies/junior",
                "/vacancies/middle",
                "/vacancies/senior",
                "/vacancies/remote",
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
            # GetMatch does not expose a reliable publication-date
            # field on these vacancy pages. Do NOT scan the whole page
            # for date-like text: blocks such as "Требуемый опыт: 3 года"
            # would be incorrectly interpreted as the publication date.
            date_selectors=(),
            date_fallback_to_detail_text=False,
            date_fallback_to_card_text=False,
        )


class GeekJobAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="geekjob",
            name="GeekJob",
            url="https://geekjob.ru/vacancies?rm=1&qs=QA",
            card_selector=(
                "li.collection-item.avatar"
            ),
            link_selector=(
                "a[href*='/vacancy/']"
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
            vacancy_url_pattern=(
                r"^https?://(?:www\.)?geekjob\.ru/vacancy/"
                r"[0-9a-f]{24}(?:[?#].*)?$"
            ),
            # На GeekJob на странице вакансии есть блок "Еще интересные вакансии"
            # с собственными датами. Нельзя искать дату по всей странице:
            # Playwright может взять дату из article[4] вместо основной вакансии
            # (article[1]). Используем селектор именно из header основной карточки.
            date_selectors=(
                "xpath=//*[@id=\"body\"]/section/article[1]/section/header/div[6]",
            ),
        )


class RVCAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="rvc",
            name="RVC",
            url=(
                "https://app.rvc.global/jobs?"
                "jobFunction=QA"
                "&keyCompetency=Manual+QA"
                "&workArrangement=FULLY_REMOTE%2CREMOTE_IN_COUNTRY"
                "&includeLanguages=EN%2CGB"
                "&countries=Czech+Republic%2CGeorgia%2CSerbia%2CUkraine"
                "%2CKazakhstan%2CBelarus%2CAzerbaijan%2CKyrgyzstan"
                "%2CMoldova%2CRussia%2CTajikistan%2CTurkmenistan"
                "&keyword=QA"
            ),
            card_selector=(
                "article, [class*='job-card'], [class*='vacancy-card']"
            ),
            link_selector=(
                "a[href*='?job='], a[href*='&job=']"
            ),
            excluded_url_patterns=(
                "/jobs/python-remote",
                "/jobs/fully-remote-",
                "/jobs/remote-jobs",
                "/jobs/senior",
                "/jobs/middle",
                "/jobs/junior",
                "/jobs/us-emea",
                "/jobs/remote-csharp-dotnet",
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
            date_selectors=(
                "time",
                "[datetime]",
            ),
        )


class LinkedInAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec(
            key="linkedin",
            name="LinkedIn",
            url=(
                "https://www.linkedin.com/search/results/all/?"
                "keywords=%23hiring%20%22QA%22&origin=HISTORY&position=0"
            ),
            card_selector=(
                "div.feed-shared-update-v2"
            ),
            link_selector=(
                "div.feed-shared-update-v2 a[href*='/posts/'],"
                "div.feed-shared-update-v2 a[href*='/feed/update/urn:li:activity:']"
            ),
            title_selectors=(
                "[data-testid='expandable-text-box']",
                ".feed-shared-update-v2__description",
                ".update-components-text",
            ),
            detail_body_selectors=(
                "main",
                "body",
            ),
            detail_exclude_selectors=GENERIC_EXCLUDE_SELECTORS,
            detail_cut_markers=GENERIC_CUT_MARKERS,
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
        # LinkedIn пока оставляем в реестре, но его extraction
        # ограничен только post-URL и не собирает footer/navigation.
        LinkedInAdapter(),
    )
