"""Official HH.ru API client with OAuth2 user authorization and PKCE."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


class HHApiError(RuntimeError):
    """Raised when HH.ru API returns an unrecoverable error."""


class HHApiAuthError(HHApiError):
    """Raised when the HH OAuth authorization is missing or invalid."""


class HHApiAccessError(HHApiError):
    """Raised when HH rejects an API request for a non-OAuth, non-CAPTCHA reason."""


class HHApiCaptchaError(HHApiError):
    """Raised when HH requires a CAPTCHA before continuing an API operation."""

    def __init__(
        self,
        message: str,
        *,
        captcha_url: str | None = None,
        fallback_url: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.captcha_url = captcha_url
        self.fallback_url = fallback_url
        self.request_id = request_id


@dataclass(frozen=True)
class PKCEPair:
    verifier: str
    challenge: str


def create_pkce_pair() -> PKCEPair:
    verifier = secrets.token_urlsafe(64)
    challenge = hashlib.sha256(verifier.encode("ascii")).digest()
    import base64

    encoded = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode("ascii")
    return PKCEPair(verifier=verifier, challenge=encoded)


class HHApiClient:
    """Async HH.ru API client.

    Default mode is user OAuth2 authorization. ``auto`` may fall back to
    application credentials, while ``user`` always requires a saved user token.
    """

    BASE_URL = "https://api.hh.ru"
    AUTH_URL = "https://hh.ru/oauth/authorize"
    TOKEN_URL = "https://api.hh.ru/token"

    def __init__(self) -> None:
        self.client_id = os.getenv("HH_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("HH_CLIENT_SECRET", "").strip()
        self.redirect_uri = os.getenv(
            "HH_REDIRECT_URI", "http://localhost:8000/oauth/callback"
        ).strip()
        self.auth_mode = os.getenv("HH_AUTH_MODE", "user").strip().lower() or "user"
        self.vacancy_auth_mode = (
            os.getenv("HH_VACANCY_AUTH_MODE", self.auth_mode).strip().lower()
            or self.auth_mode
        )
        if self.vacancy_auth_mode not in {"user", "application", "auto", "anonymous"}:
            raise HHApiError(
                "HH_VACANCY_AUTH_MODE должен быть одним из: user, application, auto, anonymous."
            )
        # HH application token is long-lived and must not be regenerated on every run.
        # Prefer an explicitly configured token, then a local cache file, and only
        # as a last resort request a new one (HH allows that no more than once/5 min).
        self.application_access_token = os.getenv("HH_APPLICATION_TOKEN", "").strip()
        self.application_token_file = Path(
            os.getenv("HH_APPLICATION_TOKEN_FILE", ".hh_app_token.json").strip()
            or ".hh_app_token.json"
        )
        self._application_access_token = ""
        self.token_file = Path(
            os.getenv("HH_TOKEN_FILE", ".hh_tokens.json").strip() or ".hh_tokens.json"
        )
        self.user_agent = os.getenv(
            "HH_USER_AGENT",
            "QA_Job_AI/0.2 (contact: your-email@example.com)",
        ).strip()
        self.host = os.getenv("HH_HOST", "hh.ru").strip() or "hh.ru"
        self.locale = os.getenv("HH_LOCALE", "RU").strip() or "RU"
        self.timeout_seconds = float(os.getenv("HH_API_TIMEOUT_SECONDS", "30"))
        self.retry_count = max(1, int(os.getenv("HH_API_RETRIES", "3")))
        self.access_token = ""
        self.refresh_token = ""
        self.expires_at = 0.0
        self.token_type = "Bearer"
        self._token_lock = asyncio.Lock()
        self._load_tokens()

    @property
    def has_user_token(self) -> bool:
        return bool(self.access_token)

    def _load_tokens(self) -> None:
        try:
            payload = json.loads(self.token_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return

        self.access_token = str(payload.get("access_token") or "").strip()
        self.refresh_token = str(payload.get("refresh_token") or "").strip()
        self.expires_at = float(payload.get("expires_at") or 0)
        self.token_type = str(payload.get("token_type") or "Bearer")

    def save_tokens(self, payload: dict[str, Any]) -> None:
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise HHApiError("HH OAuth response не содержит access_token.")

        expires_in = int(payload.get("expires_in") or 0)
        refresh_token = str(payload.get("refresh_token") or "").strip()
        if not refresh_token:
            refresh_token = self.refresh_token

        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = time.time() + max(0, expires_in)
        self.token_type = str(payload.get("token_type") or "Bearer")

        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
        }
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.token_file.with_suffix(self.token_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.token_file)

    def clear_tokens(self) -> None:
        self.access_token = ""
        self.refresh_token = ""
        self.expires_at = 0.0
        try:
            self.token_file.unlink()
        except FileNotFoundError:
            pass

    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        force_login: bool = False,
    ) -> str:
        if not self.client_id:
            raise HHApiError("HH_CLIENT_ID не задан в .env.")

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "state": state,
            "redirect_uri": self.redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if force_login:
            params["force_login"] = "yes"
        return f"{self.AUTH_URL}?{urlencode(params)}"

    async def exchange_code(
        self,
        client: httpx.AsyncClient,
        *,
        code: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        if not self.client_id or not self.client_secret:
            raise HHApiError("Для OAuth2 задайте HH_CLIENT_ID и HH_CLIENT_SECRET в .env.")

        response = await client.post(
            self.TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            },
            headers={"HH-User-Agent": self.user_agent},
        )
        if response.status_code >= 400:
            raise HHApiError(
                f"HH OAuth token error {response.status_code}: {response.text[:700]}"
            )
        payload = response.json()
        self.save_tokens(payload)
        return payload

    async def refresh_user_token(self, client: httpx.AsyncClient) -> dict[str, Any]:
        if not self.refresh_token:
            raise HHApiError(
                "Нет refresh token. Запустите `python -m qa_job_scout hh-auth`."
            )
        if not self.client_id or not self.client_secret:
            raise HHApiError("Для refresh token нужны HH_CLIENT_ID и HH_CLIENT_SECRET.")

        response = await client.post(
            self.TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"HH-User-Agent": self.user_agent},
        )
        if response.status_code >= 400:
            raise HHApiError(
                f"HH refresh token error {response.status_code}: {response.text[:700]}"
            )
        payload = response.json()
        self.save_tokens(payload)
        return payload

    def _load_application_token(self) -> str:
        if self.application_access_token:
            return self.application_access_token
        try:
            payload = json.loads(
                self.application_token_file.read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return ""
        token = str(payload.get("access_token") or "").strip()
        if token:
            self.application_access_token = token
        return token

    def _save_application_token(self, token: str) -> None:
        data = {"access_token": token}
        self.application_token_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.application_token_file.with_suffix(
            self.application_token_file.suffix + ".tmp"
        )
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.application_token_file)
        self.application_access_token = token

    async def _request_application_token(self, client: httpx.AsyncClient) -> str:
        cached = self._load_application_token()
        if cached:
            return cached
        if not self.client_id or not self.client_secret:
            raise HHApiError(
                "HH API: задайте HH_APPLICATION_TOKEN или HH_CLIENT_ID + HH_CLIENT_SECRET."
            )
        response = await client.post(
            self.TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"HH-User-Agent": self.user_agent},
        )
        if response.status_code >= 400:
            body = response.text[:1000]
            if response.status_code == 403 and "app token refresh too early" in body:
                raise HHApiError(
                    "HH application token нельзя запрашивать чаще одного раза в 5 минут. "
                    "Укажите уже выданный application access token в HH_APPLICATION_TOKEN "
                    "или подождите 5 минут перед первой генерацией. "
                    f"Ответ HH: {body}"
                )
            raise HHApiError(
                f"HH application token error {response.status_code}: {body}"
            )
        payload = response.json()
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise HHApiError("HH application token response не содержит access_token.")
        self._save_application_token(token)
        return token

    async def _get_application_token(self, client: httpx.AsyncClient) -> str:
        return await self._request_application_token(client)

    async def _ensure_token(self, client: httpx.AsyncClient) -> str:
        if self.access_token:
            return self.access_token
        if self.auth_mode == "auto":
            return await self._request_application_token(client)
        raise HHApiAuthError(
            "HH OAuth token не найден. Сначала выполните `python -m qa_job_scout hh-auth`."
        )

    @staticmethod
    def build_captcha_url(captcha_url: str, backurl: str = "https://hh.ru/") -> str:
        """Add the required HH backurl parameter to an API-provided CAPTCHA URL."""
        parts = urlsplit(captcha_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["backurl"] = backurl
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    async def solve_captcha_interactively(self, error: HHApiCaptchaError) -> bool:
        """Open HH's official CAPTCHA page and wait for the user to complete it."""
        captcha_url = error.captcha_url
        fallback_url = error.fallback_url
        if not captcha_url and not fallback_url:
            return False

        interactive = os.getenv("HH_CAPTCHA_INTERACTIVE", "true").strip().lower() in {"1", "true", "yes", "on"}
        if not interactive:
            return False
        backurl = os.getenv("HH_CAPTCHA_BACKURL", "https://hh.ru/").strip() or "https://hh.ru/"
        url = self.build_captcha_url(captcha_url, backurl=backurl) if captcha_url else fallback_url
        print()
        print("[HH.ru] Требуется CAPTCHA / ручная проверка HH.")
        print("[HH.ru] Открываю страницу HH в браузере.")
        print("[HH.ru] После успешного прохождения CAPTCHA вернитесь в терминал.")
        print(f"[HH.ru] CAPTCHA URL: {url}")
        webbrowser.open(url)
        await asyncio.to_thread(input, "[HH.ru] Нажмите Enter после прохождения CAPTCHA... ")
        return True

    def _headers(self, token: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "HH-User-Agent": self.user_agent,
        }
        if token:
            headers["Authorization"] = f"{self.token_type} {token}"
        return headers

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str | int]],
        token: str,
    ) -> dict[str, Any]:
        url = f"{self.BASE_URL.rstrip('/')}/{path.lstrip('/')}"
        current_token = token
        last_error: Exception | None = None
        refreshed_after_401 = False

        for attempt in range(1, self.retry_count + 1):
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=self._headers(current_token),
                )

                if response.status_code == 401 and not refreshed_after_401:
                    refreshed_after_401 = True
                    if self.refresh_token:
                        async with self._token_lock:
                            await self.refresh_user_token(client)
                            current_token = self.access_token
                        continue
                    if self.auth_mode == "auto":
                        current_token = await self._request_application_token(client)
                        continue

                if response.status_code == 429:
                    if attempt == self.retry_count:
                        raise HHApiError(
                            f"HH API rate limit (429): {response.text[:500]}"
                        )
                    await asyncio.sleep(min(2 ** attempt, 10))
                    continue

                if response.status_code == 403:
                    payload: dict[str, Any] = {}
                    try:
                        decoded = response.json()
                        if isinstance(decoded, dict):
                            payload = decoded
                    except ValueError:
                        payload = {}

                    request_id = str(payload.get("request_id") or "").strip() or None
                    errors = payload.get("errors") or []
                    first_error = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
                    error_type = str(first_error.get("type") or "").strip()
                    error_value = str(first_error.get("value") or "").strip()

                    if error_type == "oauth":
                        if error_value == "token_expired" and not refreshed_after_401 and self.refresh_token:
                            refreshed_after_401 = True
                            async with self._token_lock:
                                await self.refresh_user_token(client)
                                current_token = self.access_token
                            continue

                        if error_value in {
                            "bad_authorization",
                            "token_revoked",
                            "application_not_found",
                            "user_auth_expected",
                        }:
                            if error_value in {"bad_authorization", "token_revoked"}:
                                self.clear_tokens()
                            details = {
                                "bad_authorization": "токен недействителен или не существует",
                                "token_revoked": "токен отозван",
                                "application_not_found": "приложение HH.ru удалено",
                                "user_auth_expected": "для этого метода требуется OAuth пользователя",
                            }.get(error_value, error_value)
                            suffix = f" Request ID: {request_id}." if request_id else ""
                            raise HHApiAuthError(
                                f"HH AUTH ERROR (403): {details}. "
                                f"Запустите `python -m qa_job_scout hh-auth` и авторизуйтесь заново.{suffix}"
                            )

                    if error_type == "captcha_required" or error_value == "captcha_required":
                        captcha_url = str(first_error.get("captcha_url") or "").strip() or None
                        fallback_url = str(first_error.get("fallback_url") or "").strip() or None
                        suffix = f" Request ID: {request_id}." if request_id else ""
                        raise HHApiCaptchaError(
                            "HH CAPTCHA REQUIRED (403): HH.ru требует пройти CAPTCHA перед продолжением API-запроса."
                            + suffix,
                            captcha_url=captcha_url,
                            fallback_url=fallback_url,
                            request_id=request_id,
                        )

                    # Do not invent a CAPTCHA URL. HH's documented CAPTCHA
                    # contract explicitly uses type/value == captcha_required and
                    # supplies captcha_url and/or fallback_url. A bare
                    # {"type": "forbidden"} is neither an OAuth error nor sufficient
                    # evidence to claim that a CAPTCHA is available.
                    body = response.text[:1500].strip()
                    suffix = f" Request ID: {request_id}." if request_id else ""
                    raise HHApiAccessError(
                        f"HH API ACCESS ERROR (403) для {path}: HH.ru отклонил запрос."
                        f"{suffix} Ответ: {body}"
                    )

                if response.status_code >= 400:
                    raise HHApiError(
                        f"HH API {response.status_code} for {path}: {response.text[:700]}"
                    )

                payload = response.json()
                if not isinstance(payload, dict):
                    raise HHApiError(
                        f"HH API {path}: ожидался JSON-object, получен {type(payload).__name__}."
                    )
                return payload

            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if attempt == self.retry_count:
                    break
                await asyncio.sleep(min(2 ** (attempt - 1), 5))

        raise HHApiError(f"HH API request failed: {path}: {last_error!r}")

    async def _vacancy_token(self, client: httpx.AsyncClient) -> str:
        """Select the authorization mode for vacancy search/detail endpoints.

        HH's current API documentation states that the vacancy list depends on the
        authorization type and that an unauthenticated request may trigger CAPTCHA.
        Prefer the saved user OAuth token; in auto mode fall back to an application
        token if no user token exists. Anonymous mode is kept only as an explicit
        diagnostic/fallback option.
        """
        mode = self.vacancy_auth_mode
        if mode == "anonymous":
            return ""
        if mode == "application":
            return await self._get_application_token(client)
        if mode == "user" and self.access_token:
            return await self._ensure_token(client)
        if mode == "user":
            raise HHApiAuthError(
                "HH OAuth token не найден для поиска вакансий. "
                "Сначала выполните `python -m qa_job_scout hh-auth`."
            )
        if mode == "auto":
            # For public vacancy search, a long-lived application token is more
            # appropriate than tying the collector to a user's personal OAuth
            # session. Fall back to user OAuth only when no app token is configured.
            if self.application_access_token or self.application_token_file.exists():
                return await self._get_application_token(client)
            return await self._get_application_token(client)
        raise HHApiError(f"Неподдерживаемый HH_VACANCY_AUTH_MODE: {mode}")

    async def search_vacancies(
        self,
        client: httpx.AsyncClient,
        *,
        text: str,
        period_days: int,
        page: int,
        per_page: int,
        work_format: str | None = "REMOTE",
        order_by: str = "publication_time",
    ) -> dict[str, Any]:
        token = await self._vacancy_token(client)
        params: list[tuple[str, str | int]] = [
            ("text", text),
            ("search_field", "name"),
            ("period", max(1, min(period_days, 30))),
            ("page", page),
            ("per_page", max(1, min(per_page, 100))),
            ("order_by", order_by),
            ("host", self.host),
            ("locale", self.locale),
        ]
        if work_format:
            params.append(("work_format", work_format))
        return await self._request_json(
            client, "GET", "/vacancies", params=params, token=token
        )

    async def get_vacancy(
        self,
        client: httpx.AsyncClient,
        vacancy_id: str,
    ) -> dict[str, Any]:
        token = await self._vacancy_token(client)
        return await self._request_json(
            client,
            "GET",
            f"/vacancies/{vacancy_id}",
            params=[("host", self.host), ("locale", self.locale)],
            token=token,
        )


def parse_hh_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_hh_search_url(
    *, text: str, period_days: int, work_format: str | None = "REMOTE"
) -> str:
    params: list[tuple[str, str | int]] = [
        ("text", text),
        ("period", period_days),
        ("order_by", "publication_time"),
    ]
    if work_format:
        params.append(("work_format", work_format))
    return f"https://hh.ru/search/vacancy?{urlencode(params)}"
