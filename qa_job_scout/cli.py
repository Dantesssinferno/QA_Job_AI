from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from pathlib import Path

from .core import evaluate, load_profile
from .storage import Store


def write_report(store: Store) -> Path:
    vacancies = store.recommended()
    lines = ["# Подходящие QA-вакансии", "", "Письма являются черновиками: перед откликом проверьте их и требования вакансии.", ""]
    if not vacancies:
        lines.append("Подходящих вакансий пока нет. Запустите `python -m qa_job_scout scan`.")
    for v in vacancies:
        lines.extend([f"## {v.title} ({v.score}/95)", f"- Источник: {v.source}", f"- Ссылка: {v.url}", f"- ID для review: `{v.id}`", f"- Почему: {' '.join(v.reasons or [])}", "", "### Черновик письма", "", v.cover_letter, ""])
    out = Path("out")
    out.mkdir(exist_ok=True)
    report = out / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def print_source_run(run, statuses: Counter) -> None:
    print(f"[{run.source_name}] карточек: {run.listed}; деталей: {run.detailed}; сохранено: {run.collected}; "
          f"подходит: {statuses['recommended']}; на проверку: {statuses['needs_review']}; отклонено: {statuses['rejected']}; статус: {run.status}")
    for error in run.errors:
        print(f"  ошибка: {error}")


async def open_for_review(url: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(".browser-profile", headless=False, locale="ru-RU")
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        print("Вакансия открыта. Проверьте письмо, приложите CV и отправьте отклик вручную.")
        await asyncio.to_thread(input, "Нажмите Enter после завершения, чтобы закрыть браузер: ")
        await context.close()


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ModuleNotFoundError:
        pass
    parser = argparse.ArgumentParser(description="Поиск подходящих удалённых QA-вакансий")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="собрать, отфильтровать и подготовить черновики")
    sub.add_parser("report", help="пересоздать Markdown-отчёт из базы")
    review = sub.add_parser("review", help="открыть вакансию для ручного отклика")
    review.add_argument("vacancy_id")
    reject = sub.add_parser("reject", help="исключить вакансию вручную и сохранить решение")
    reject.add_argument("vacancy_id")
    reject.add_argument("reason", nargs="?", default="Не подходит кандидату")
    args = parser.parse_args()
    store = Store()

    if args.command == "scan":
        from .ai import enrich
        from .crawler import crawl_sync

        profile = load_profile()
        crawl_result = crawl_sync()
        statuses_by_source: dict[str, Counter] = defaultdict(Counter)
        by_source: dict[str, list] = defaultdict(list)
        for vacancy in crawl_result.vacancies:
            vacancy = enrich(evaluate(vacancy, profile), profile)
            store.save(vacancy)
            statuses_by_source[vacancy.source][vacancy.status] += 1
            by_source[vacancy.source].append(vacancy)
        for run in crawl_result.runs:
            statuses = statuses_by_source[run.source_name]
            store.record_source_run(run, statuses)
            print_source_run(run, statuses)
            for vacancy in by_source[run.source_name]:
                if vacancy.status == "recommended":
                    print(f"  подходит ({vacancy.score}/95): {vacancy.title}\n  {vacancy.url}")
        report = write_report(store)
        print(f"Собрано: {len(crawl_result.vacancies)}. Отчёт: {report}")
    elif args.command == "report":
        print(f"Отчёт: {write_report(store)}")
    elif args.command == "reject":
        if not store.reject(args.vacancy_id, args.reason):
            raise SystemExit("Вакансия не найдена.")
        print(f"Вакансия {args.vacancy_id} исключена. {write_report(store)} обновлён.")
    else:
        vacancy = store.get(args.vacancy_id)
        if vacancy is None:
            raise SystemExit("Вакансия не найдена.")
        print("\n--- Черновик письма ---\n" + vacancy.cover_letter + "\n---\n")
        asyncio.run(open_for_review(vacancy.url))
