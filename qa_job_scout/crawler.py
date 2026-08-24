from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from playwright.async_api import async_playwright

from .adapters import SourceRun, enabled_adapters
from .core import Vacancy


@dataclass
class CrawlResult:
    vacancies: list[Vacancy]
    runs: list[SourceRun]


async def crawl() -> CrawlResult:
    """Collect each site independently, including its vacancy detail pages."""
    headless = os.getenv("HEADLESS", "false").lower() == "true"
    all_vacancies: list[Vacancy] = []
    runs: list[SourceRun] = []
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            ".browser-profile", headless=headless, locale="ru-RU"
        )
        page = await context.new_page()
        for adapter in enabled_adapters():
            vacancies, run = await adapter.collect(page)
            all_vacancies.extend(vacancies)
            runs.append(run)
        await context.close()
    return CrawlResult(all_vacancies, runs)


def crawl_sync() -> CrawlResult:
    return asyncio.run(crawl())
