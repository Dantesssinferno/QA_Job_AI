"""Site-specific vacancy adapters.

Each adapter owns its listing-card selector, vacancy-link selection, and the
selectors used on its detail page.  No adapter silently falls back to a broad
``a[href]`` scan: an empty selector produces a logged source failure instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

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
    detail_body_selectors: tuple[str, ...] = ("main", "article", "body")
    date_selectors: tuple[str, ...] = ("time", "[class*='date']", "[class*='publish']")
    detail_wait_selector: str = "h1"
    requires_login: bool = False


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
    page_timeout_ms = 45_000
    selector_timeout_ms = 15_000
    max_vacancies = 75

    def __init__(self) -> None:
        self.spec = self.get_spec()

    def get_spec(self) -> AdapterSpec:
        raise NotImplementedError

    async def _first_text(self, page: Page, selectors: Iterable[str]) -> str:
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    text = (await locator.inner_text()).strip()
                    if text:
                        return text
            except PlaywrightTimeoutError:
                continue
        return ""

    async def list_cards(self, page: Page) -> list[dict[str, str]]:
        await page.goto(self.spec.url, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
        cards = page.locator(self.spec.card_selector)
        await cards.first.wait_for(state="attached", timeout=self.selector_timeout_ms)
        return await cards.evaluate_all(
            """(cards, spec) => cards.slice(0, spec.limit).map(card => {
                const link = card.matches(spec.link) ? card : card.querySelector(spec.link);
                const pickText = (selectors) => {
                  for (const selector of selectors) {
                    const node = card.querySelector(selector);
                    if (node && node.innerText.trim()) return node.innerText.trim();
                  }
                  return '';
                };
                return {
                  href: link?.href || '',
                  title: pickText(spec.titles) || link?.getAttribute('aria-label') || link?.innerText.trim() || '',
                  text: card.innerText.trim()
                };
            }).filter(item => item.href && item.title)""",
            {"link": self.spec.link_selector, "titles": list(self.spec.title_selectors), "limit": self.max_vacancies},
        )

    async def read_detail(self, page: Page, card: dict[str, str]) -> Vacancy:
        await page.goto(card["href"], wait_until="domcontentloaded", timeout=self.page_timeout_ms)
        try:
            await page.locator(self.spec.detail_wait_selector).first.wait_for(state="attached", timeout=self.selector_timeout_ms)
        except PlaywrightTimeoutError:
            # Some sites render an informative detail page without an H1.  The
            # page body is still kept and the run gets a diagnostic in collect().
            pass
        title = await self._first_text(page, self.spec.detail_title_selectors) or card["title"]
        body = await self._first_text(page, self.spec.detail_body_selectors) or card["text"]
        date = await self._first_text(page, self.spec.date_selectors)
        all_text = f"{card['text']}\n{body}"
        return Vacancy(
            source=self.spec.name,
            title=" ".join(title.split()),
            url=card["href"],
            text=" ".join(all_text.split()),
            published_text=" ".join((date or all_text).split()),
            remote=any(word in all_text.lower() for word in ("remote", "удал", "home office", "global")),
        )

    async def collect(self, page: Page) -> tuple[list[Vacancy], SourceRun]:
        run = SourceRun(self.spec.key, self.spec.name)
        try:
            cards = await self.list_cards(page)
            run.listed = len(cards)
        except Exception as exc:
            run.status = "failed"
            run.errors.append(f"Список: {type(exc).__name__}: {exc}")
            return [], run
        vacancies: list[Vacancy] = []
        for card in cards:
            try:
                vacancy = await self.read_detail(page, card)
                run.detailed += 1
                vacancies.append(vacancy)
            except Exception as exc:
                run.errors.append(f"{card['href']}: {type(exc).__name__}: {exc}")
        run.collected = len(vacancies)
        if run.errors and not vacancies:
            run.status = "failed"
        elif run.errors:
            run.status = "partial"
        return vacancies, run


class HireHiAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("hirehi", "HireHi", "https://hirehi.ru/vacancies/manual-qa?format=%D1%83%D0%B4%D0%B0%D0%BB%D1%91%D0%BD%D0%BD%D0%BE&search=QA", "a.job-card[data-id]", "a.job-card[data-id]", ("[class*='title']", "h2", "h3"), detail_body_selectors=("main", "[class*='vacancy']", "body"))


class RocketHuntAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("rockethunt", "RocketHunt", "https://rockethunt.ai/ru?text=QA", "article.cv-card", "a[href]", ("h2", "h3", "[class*='title']"), detail_body_selectors=("main", "article", "body"))


class DreamJobAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("dreamjob", "DreamJob", "https://dreamjob.ru/vakansii?jbfrp%5Btext%5D=QA&jbfrp%5Bsalary%5D=&jbfrp%5BonlyWithSalary%5D=0&jbfrp%5BorderBy%5D=relevance", "div.vacancy-new.vacancy-new__item", "a[href*='/vacancy']", ("h2", "h3", "[class*='title']"), detail_body_selectors=("main", "[class*='vacancy']", "body"))


class HirifyAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("hirify", "Hirify", "https://hirify.me/?countries=russia,serbia,ukraine,armenia,romania,cyprus,latvia,czech_republic,kazakhstan,europe,georgia,turkey,croatia,azerbaijan,uzbekistan,vietnam,belarus,bulgaria,thailand,moldova&regions=russia,europe&remote_type=global&skills_match_type=OR&specializations=qa_testing&work_format=remote", "div.vacancy-card[data-vacancy-id]", "a[href*='/jobs/']", ("h2", "h3", "[class*='title']"), detail_body_selectors=("main", "[class*='vacancy']", "body"))


class TaylorAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("taylor", "Taylor", "https://taylor.kz/jobs/stack/qa?q=&stack=qa&source=&city=&remote=&sort=recent&seniority=&format=&salary=&direct=&region=&hide_applied=&hide_viewed=&with_salary=&recent=", 'a[href*="/jobs/"][aria-label^="Открыть вакансию:"]', 'a[href*="/jobs/"][aria-label^="Открыть вакансию:"]', ("h2", "h3", "[class*='title']"), detail_body_selectors=("main", "article", "body"))


class JobRocketAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("jobrocket", "JobRocket", "https://jobrocket.ru/en?page=1&categories=qa", 'div[data-slot="card"]', "a[href*='/job']", ("h2", "h3", "[class*='title']"), detail_body_selectors=("main", "article", "body"))


class TalantoAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("talanto", "Talanto", "https://talanto.work/?offset=48&q=QA", 'a[aria-label][href^="/jobs/"]', 'a[aria-label][href^="/jobs/"]', ("h2", "h3", "[class*='title']"), detail_body_selectors=("main", "article", "body"))


class GetMatchAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("getmatch", "GetMatch", "https://getmatch.ru/vacancies?p=1&sa=any&pa=7d&l=remote&se=junior&se=middle&se=senior", "div.b-vacancy-card", "a[href*='/vacancies/']", ("h2", "h3", "[class*='title']"), detail_body_selectors=("main", "[class*='vacancy']", "body"))


class GeekJobAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("geekjob", "GeekJob", "https://geekjob.ru/vacancies?rm=1&t=2,32,276,277,278,279,45", "li.collection-item.avatar", "a[href*='/vacancy']", ("h2", "h3", ".title", "[class*='title']"), detail_body_selectors=("main", "article", "body"))


class RVCAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("rvc", "RVC", "https://app.rvc.global/jobs?salaryFloor=off&keyword=QA&jobFunction=QA&keyCompetency=Other+%2F+Unspecified&workArrangement=REMOTE_IN_COUNTRY&includeLanguages=RU%2CEN%2CGB&employmentType=FULL_TIME&countries=Cyprus%2CCzech+Republic%2CGeorgia%2CGreece%2CLatvia%2CPoland%2CPortugal%2CRomania%2CSlovakia%2CSerbia%2CUkraine%2CArmenia%2CAzerbaijan%2CBelarus%2CKazakhstan%2CKyrgyzstan%2CMoldova%2CRussia%2CTajikistan%2CTurkmenistan%2CUzbekistan", "article, [class*='job-card'], [class*='vacancy-card']", "a[href*='/jobs/']", ("h2", "h3", "[class*='title']"), detail_body_selectors=("main", "article", "body"))


class LinkedInAdapter(BaseAdapter):
    def get_spec(self) -> AdapterSpec:
        return AdapterSpec("linkedin", "LinkedIn", "https://www.linkedin.com/feed/", 'p[data-testid="expandable-text-box"]', "a[href]", ("[data-testid='expandable-text-box']",), detail_body_selectors=("main", "body"), requires_login=True)


def enabled_adapters() -> tuple[BaseAdapter, ...]:
    return (HireHiAdapter(), RocketHuntAdapter(), DreamJobAdapter(), HirifyAdapter(), TaylorAdapter(), JobRocketAdapter(), TalantoAdapter(), GetMatchAdapter(), GeekJobAdapter(), RVCAdapter(), LinkedInAdapter())
