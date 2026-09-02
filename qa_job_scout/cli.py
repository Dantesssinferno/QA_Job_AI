from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .core import evaluate, load_profile
from .storage import Store


def write_report(store: Store) -> Path:
    """
    Создаёт Markdown-отчёт только из актуальных вакансий.

    Store.recommended() дополнительно проверяет published_at,
    поэтому старые вакансии не попадают в отчёт даже в том случае,
    если раньше у них был статус recommended.
    """

    vacancies = store.recommended(
        now=datetime.now(UTC),
        max_age_days=5,
    )

    lines = [
        "# Подходящие QA-вакансии",
        "",
        "Письма являются черновиками: перед откликом проверьте их и требования вакансии.",
        "",
    ]

    if not vacancies:
        lines.append(
            "Подходящих вакансий пока нет. "
            "Запустите `python -m qa_job_scout scan`."
        )

    for v in vacancies:
        lines.extend(
            [
                f"## {v.title} ({v.score}/95)",
                f"- Источник: {v.source}",
                f"- Ссылка: {v.url}",
                f"- ID для review: `{v.id}`",
                f"- Почему: {' '.join(v.reasons or [])}",
                "",
                "### Черновик письма",
                "",
                v.cover_letter,
                "",
            ]
        )

    out = Path("out")
    out.mkdir(exist_ok=True)

    report = out / "report.md"

    report.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    return report


def print_source_run(
    run,
    statuses: Counter,
) -> None:
    print()
    print("=" * 80)
    print(f"[{run.source_name}]")
    print("=" * 80)

    print(
        f"  Карточек:       {run.listed}"
    )
    print(
        f"  Деталей:        {run.detailed}"
    )
    print(
        f"  Сохранено:      {run.collected}"
    )
    print(
        f"  Подходит:       {statuses['recommended']}"
    )
    print(
        f"  На проверку:    {statuses['needs_review']}"
    )
    print(
        f"  Отклонено:      {statuses['rejected']}"
    )
    print(
        f"  Статус:         {run.status}"
    )

    if run.errors:
        print()
        print("  ОШИБКИ ИСТОЧНИКА:")

        for error in run.errors:
            print(
                f"    - {error}"
            )


def print_rejection_reasons(
    source_name: str,
    vacancies: list,
) -> None:
    """
    Показывает причины, по которым вакансии данного источника
    получили rejected / needs_review.

    Сначала выводится агрегированная статистика,
    затем список конкретных вакансий.
    """

    problematic = [
        vacancy
        for vacancy in vacancies
        if vacancy.status in (
            "rejected",
            "needs_review",
        )
    ]

    if not problematic:
        return

    print()
    print("  ПРИЧИНЫ ОТКЛОНЕНИЯ:")

    reason_counter: Counter = Counter()

    for vacancy in problematic:
        reasons = vacancy.reasons or [
            "Причина не указана"
        ]

        for reason in reasons:
            reason_counter[reason] += 1

    for reason, count in reason_counter.most_common():
        print(
            f"    {count:>3} × {reason}"
        )

    print()
    print("  КАРТОЧКИ:")

    for vacancy in problematic:
        print()
        print(
            f"    [{vacancy.status.upper()}] "
            f"{vacancy.title}"
        )

        print(
            f"    URL: {vacancy.url}"
        )

        if vacancy.published_text:
            print(
                f"    Дата из crawler: "
                f"{vacancy.published_text}"
            )

        if vacancy.published_at:
            print(
                f"    Parsed date: "
                f"{vacancy.published_at}"
            )

        for reason in (
            vacancy.reasons
            or ["Причина не указана"]
        ):
            print(
                f"    Причина: {reason}"
            )


async def open_for_review(
    url: str,
) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            ".browser-profile",
            headless=False,
            locale="ru-RU",
        )

        page = await context.new_page()

        await page.goto(
            url,
            wait_until="domcontentloaded",
        )

        print(
            "Вакансия открыта. "
            "Проверьте письмо, приложите CV и отправьте отклик вручную."
        )

        await asyncio.to_thread(
            input,
            "Нажмите Enter после завершения, чтобы закрыть браузер: ",
        )

        await context.close()


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()

    except ModuleNotFoundError:
        pass

    parser = argparse.ArgumentParser(
        description="Поиск подходящих удалённых QA-вакансий"
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sub.add_parser(
        "scan",
        help="собрать, отфильтровать и подготовить черновики",
    )

    sub.add_parser(
        "report",
        help="пересоздать Markdown-отчёт из базы",
    )

    review = sub.add_parser(
        "review",
        help="открыть вакансию для ручного отклика",
    )

    review.add_argument(
        "vacancy_id"
    )

    reject = sub.add_parser(
        "reject",
        help="исключить вакансию вручную и сохранить решение",
    )

    reject.add_argument(
        "vacancy_id"
    )

    reject.add_argument(
        "reason",
        nargs="?",
        default="Не подходит кандидату",
    )

    args = parser.parse_args()

    store = Store()

    if args.command == "scan":
        from .ai import enrich
        from .crawler import crawl_sync

        profile = load_profile()

        crawl_result = crawl_sync()

        statuses_by_source: dict[str, Counter] = defaultdict(
            Counter
        )

        by_source: dict[str, list] = defaultdict(
            list
        )

        for vacancy in crawl_result.vacancies:
            vacancy = evaluate(
                vacancy,
                profile,
            )

            # ВАЖНО:
            # AI enrichment выполняем только для вакансий,
            # которые прошли deterministic-фильтры.
            #
            # Это уменьшает количество ненужных AI-вызовов.
            if vacancy.status in (
                "recommended",
                "needs_review",
            ):
                vacancy = enrich(
                    vacancy,
                    profile,
                )

            store.save(vacancy)

            statuses_by_source[
                vacancy.source
            ][
                vacancy.status
            ] += 1

            by_source[
                vacancy.source
            ].append(
                vacancy
            )

        for run in crawl_result.runs:
            statuses = statuses_by_source[
                run.source_name
            ]

            store.record_source_run(
                run,
                statuses,
            )

            print_source_run(
                run,
                statuses,
            )

            source_vacancies = by_source[
                run.source_name
            ]

            # ----------------------------------------------------
            # Recommended
            # ----------------------------------------------------

            recommended = [
                vacancy
                for vacancy in source_vacancies
                if vacancy.status == "recommended"
            ]

            if recommended:
                print()
                print("  ПОДХОДЯЩИЕ:")

                for vacancy in recommended:
                    print(
                        f"    ✓ "
                        f"({vacancy.score}/95) "
                        f"{vacancy.title}"
                    )

                    print(
                        f"      {vacancy.url}"
                    )

                    if vacancy.published_text:
                        print(
                            f"      Дата: "
                            f"{vacancy.published_text}"
                        )

                    if vacancy.published_at:
                        print(
                            f"      Parsed date: "
                            f"{vacancy.published_at}"
                        )

            # ----------------------------------------------------
            # Rejected / needs_review
            # ----------------------------------------------------

            print_rejection_reasons(
                run.source_name,
                source_vacancies,
            )

        report = write_report(
            store
        )

        print()
        print("=" * 80)
        print(
            f"Собрано вакансий: "
            f"{len(crawl_result.vacancies)}"
        )
        print(
            f"Отчёт: {report}"
        )
        print("=" * 80)

    elif args.command == "report":
        report = write_report(
            store
        )

        print(
            f"Отчёт: {report}"
        )

    elif args.command == "reject":
        if not store.reject(
            args.vacancy_id,
            args.reason,
        ):
            raise SystemExit(
                "Вакансия не найдена."
            )

        print(
            f"Вакансия {args.vacancy_id} исключена. "
            f"{write_report(store)} обновлён."
        )

    else:
        vacancy = store.get(
            args.vacancy_id
        )

        if vacancy is None:
            raise SystemExit(
                "Вакансия не найдена."
            )

        print(
            "\n--- Черновик письма ---\n"
            + vacancy.cover_letter
            + "\n---\n"
        )

        asyncio.run(
            open_for_review(
                vacancy.url
            )
        )


if __name__ == "__main__":
    main()