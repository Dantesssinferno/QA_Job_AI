"""Official HH.ru API client with OAuth2 user authorization and PKCE."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx


class HHApiError(RuntimeError):
    """Raised when HH.ru API returns an unrecoverable error."""


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

    async def _request_application_token(self, client: httpx.AsyncClient) -> str:
        if not self.client_id or not self.client_secret:
            raise HHApiError(
                "HH API: задайте user OAuth token или HH_CLIENT_ID + HH_CLIENT_SECRET."
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
            raise HHApiError(
                f"HH application token error {response.status_code}: {response.text[:700]}"
            )
        payload = response.json()
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise HHApiError("HH application token response не содержит access_token.")
        return token

    async def _ensure_token(self, client: httpx.AsyncClient) -> str:
        if self.access_token:
            return self.access_token
        if self.auth_mode == "auto":
            return await self._request_application_token(client)
        raise HHApiError(
            "HH OAuth token не найден. Сначала выполните `python -m qa_job_scout hh-auth`."
        )

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
                    raise HHApiError(
                        "HH API вернул 403 Forbidden. Проверьте OAuth-доступ приложения, "
                        "User-Agent и настройки приложения HH.ru."
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
        token = await self._ensure_token(client)
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
        token = await self._ensure_token(client)
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
