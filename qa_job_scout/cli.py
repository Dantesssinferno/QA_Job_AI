from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import threading
import webbrowser
from collections import Counter, defaultdict
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

from .core import evaluate, load_profile
from .storage import Store


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    result: ClassVar[dict[str, str]] = {}
    expected_path = "/oauth/callback"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != self.expected_path:
            self.send_response(404)
            self.end_headers()
            return

        query = parse_qs(parsed.query)
        for key in ("code", "state", "error", "error_description"):
            values = query.get(key)
            if values:
                self.result[key] = values[0]

        body = "<html><body><h2>HH.ru авторизация получена.</h2><p>Можно вернуться в терминал.</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        return


def hh_auth() -> None:
    import httpx

    from .hh_api import HHApiClient, HHApiError, create_pkce_pair

    api = HHApiClient()
    if not api.client_id or not api.client_secret:
        raise SystemExit("Заполните HH_CLIENT_ID и HH_CLIENT_SECRET в .env")

    parsed_redirect = urlparse(api.redirect_uri)
    if parsed_redirect.scheme != "http" or parsed_redirect.hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit(
            "Для локальной hh-auth сейчас поддерживается redirect URI на localhost/127.0.0.1."
        )
    if parsed_redirect.port not in {None, 80, 8000}:
        raise SystemExit("Запустите hh-auth с redirect URI на порту 8000, как зарегистрировано в HH.ru.")

    host = parsed_redirect.hostname
    port = parsed_redirect.port or 80
    handler = _OAuthCallbackHandler
    handler.result = {}
    handler.expected_path = parsed_redirect.path or "/oauth/callback"
    server = HTTPServer((host, port), handler)

    state = secrets.token_urlsafe(32)
    pkce = create_pkce_pair()
    force_login = os.getenv("HH_FORCE_LOGIN", "false").strip().lower() in {"1", "true", "yes", "on"}
    authorization_url = api.build_authorization_url(
        state=state,
        code_challenge=pkce.challenge,
        force_login=force_login,
    )

    print("Открываю HH.ru для авторизации...")
    print(f"Callback: {api.redirect_uri}")
    print("Если браузер не открылся, скопируйте URL ниже:")
    print(authorization_url)
    print()

    threading.Thread(target=server.handle_request, daemon=True).start()
    webbrowser.open(authorization_url)

    try:
        for _ in range(300):
            if handler.result:
                break
            import time
            time.sleep(0.2)
    finally:
        server.server_close()

    if not handler.result:
        raise SystemExit("Не получен callback от HH.ru за 60 секунд.")

    if handler.result.get("error"):
        raise SystemExit(
            f"HH OAuth отменён/завершился ошибкой: {handler.result.get('error_description') or handler.result['error']}"
        )
    if handler.result.get("state") != state:
        raise SystemExit("Ошибка OAuth: state не совпадает.")
    code = handler.result.get("code")
    if not code:
        raise SystemExit("HH callback не содержит authorization code.")

    async def exchange():
        timeout = httpx.Timeout(api.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await api.exchange_code(
                client,
                code=code,
                code_verifier=pkce.verifier,
            )

    try:
        payload = asyncio.run(exchange())
    except HHApiError as exc:
        raise SystemExit(str(exc)) from exc

    expires = payload.get("expires_in")
    print(f"HH.ru OAuth успешно завершён. Токен сохранён в: {api.token_file}")
    if expires:
        print(f"Access token expires_in: {expires} seconds")


def hh_check() -> None:
    """Diagnose the saved HH OAuth token without exposing the token itself."""
    import httpx

    from .hh_api import HHApiClient

    api = HHApiClient()
    print("HH OAuth diagnostic")
    print(f"  token file: {api.token_file}")
    print(f"  user token loaded: {'yes' if api.access_token else 'no'}")
    if api.expires_at:
        remaining = int(api.expires_at - __import__('time').time())
        print(f"  access token remaining: {remaining} sec")
    print(f"  HH_AUTH_MODE: {api.auth_mode}")
    print(f"  HH_VACANCY_AUTH_MODE: {api.vacancy_auth_mode}")

    async def run() -> None:
        timeout = httpx.Timeout(api.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            me = await api.check_user_token(client)
            print(f"\nGET /me: HTTP {me.get('status_code')}")
            if me.get("ok"):
                payload = me.get("payload") or {}
                print("  OAuth token accepted by HH: YES")
                print(f"  auth_type: {payload.get('auth_type', 'unknown')}")
                print(f"  is_applicant: {payload.get('is_applicant', 'unknown')}")
                print(f"  is_application: {payload.get('is_application', 'unknown')}")
            else:
                print("  OAuth token accepted by HH: NO")
                print(f"  HH response: {me.get('payload') or me.get('error')}")
                return

            vacancies = await api.check_vacancy_search(client)
            print(f"\nGET /vacancies (1 result): HTTP {vacancies.get('status_code')}")
            if vacancies.get("ok"):
                payload = vacancies.get("payload") or {}
                print("  Vacancy API: OK")
                print(f"  found: {payload.get('found', 'unknown')}")
            else:
                print("  Vacancy API: FAILED")
                print(f"  HH response: {vacancies.get('payload') or vacancies.get('error')}")

    try:
        asyncio.run(run())
    except Exception as exc:
        raise SystemExit(f"HH check failed: {exc}") from exc


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
                f"- Дата публикации: {v.published_at or 'не определена'}",
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

        # В evaluate() причины добавляются последовательно:
        # контекстные положительные признаки -> финальная причина reject/review.
        # Для статистики нужна именно последняя причина, а не все промежуточные.
        final_reason = reasons[-1]

        reason_counter[final_reason] += 1

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
        "hh-auth",
        help="авторизовать приложение в HH.ru через OAuth2 + PKCE",
    )

    sub.add_parser(
        "hh-check",
        help="проверить сохранённый HH OAuth token через /me и /vacancies",
    )

    scan = sub.add_parser(
        "scan",
        help="собрать, отфильтровать и подготовить черновики",
    )
    scan.add_argument(
        "--sources",
        nargs="+",
        help="ключи источников через пробел или запятую; без параметра сканируются все включённые источники",
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

    if args.command == "hh-auth":
        hh_auth()
        return

    if args.command == "hh-check":
        hh_check()
        return

    if args.command == "scan":
        from .ai import enrich
        from .crawler import crawl_sync

        profile = load_profile()

        source_keys = None
        if args.sources:
            source_keys = {
                key.strip().lower()
                for value in args.sources
                for key in value.split(",")
                if key.strip()
            }
        crawl_result = crawl_sync(source_keys)

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