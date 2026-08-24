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


async def _block_heavy_resources(route) -> None:
    """Skip resources that are not needed for vacancy text extraction."""
    if route.request.resource_type in {"image", "font", "media"}:
        await route.abort()
    else:
        await route.continue_()


async def crawl() -> CrawlResult:
    """Collect sources in parallel using a headless persistent browser context."""
    headless = os.getenv("HEADLESS", "true").strip().lower() in {"1", "true", "yes", "on"}
    source_concurrency = max(1, int(os.getenv("SOURCE_CONCURRENCY", "6")))
    detail_concurrency = max(1, int(os.getenv("DETAIL_CONCURRENCY", "12")))

    all_vacancies: list[Vacancy] = []
    runs: list[SourceRun] = []

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            ".browser-profile",
            headless=headless,
            locale="ru-RU",
            service_workers="block",
        )
        await context.route("**/*", _block_heavy_resources)

        source_sem = asyncio.Semaphore(source_concurrency)
        detail_sem = asyncio.Semaphore(detail_concurrency)

        async def run_adapter(adapter):
            async with source_sem:
                page = await context.new_page()
                try:
                    return await adapter.collect(
                        page,
                        context=context,
                        detail_semaphore=detail_sem,
                    )
                finally:
                    await page.close()

        adapters = enabled_adapters()
        results = await asyncio.gather(
            *(run_adapter(adapter) for adapter in adapters),
            return_exceptions=True,
        )

        for adapter, result in zip(adapters, results):
            if isinstance(result, Exception):
                run = SourceRun(adapter.spec.key, adapter.spec.name, status="failed")
                run.errors.append(f"{type(result).__name__}: {result}")
                runs.append(run)
                continue
            vacancies, run = result
            all_vacancies.extend(vacancies)
            runs.append(run)

        await context.close()

    return CrawlResult(all_vacancies, runs)


def crawl_sync() -> CrawlResult:
    return asyncio.run(crawl())
