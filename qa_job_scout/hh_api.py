"""HH.ru API client used by the vacancy adapter."""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx


class HHApiError(RuntimeError):
    """Raised when HH.ru API returns an unrecoverable error."""


class HHApiClient:
    """Small async HH.ru API client with application-token authentication."""

    BASE_URL = os.getenv("HH_API_BASE_URL", "https://api.hh.ru")
    TOKEN_URL = os.getenv("HH_TOKEN_URL", "https://api.hh.ru/token")

    def __init__(self) -> None:
        self.client_id = os.getenv("HH_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("HH_CLIENT_SECRET", "").strip()
        self.access_token = os.getenv("HH_ACCESS_TOKEN", "").strip()
        self.user_agent = os.getenv(
            "HH_USER_AGENT",
            "QA_Job_AI/0.1 (contact: replace-with-your-email@example.com)",
        ).strip()
        self.host = os.getenv("HH_HOST", "hh.ru").strip() or "hh.ru"
        self.locale = os.getenv("HH_LOCALE", "RU").strip() or "RU"
        self.timeout_seconds = float(
            os.getenv("HH_API_TIMEOUT_SECONDS", "30")
        )
        self.retry_count = max(
            1,
            int(os.getenv("HH_API_RETRIES", "3")),
        )
        self._token_lock = asyncio.Lock()

    async def _request_token(self, client: httpx.AsyncClient) -> str:
        if not self.client_id or not self.client_secret:
            raise HHApiError(
                "HH API: задайте HH_ACCESS_TOKEN или HH_CLIENT_ID + HH_CLIENT_SECRET."
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
                f"HH API token error {response.status_code}: {response.text[:500]}"
            )

        payload = response.json()
        token = str(payload.get("access_token") or "").strip()

        if not token:
            raise HHApiError("HH API token response не содержит access_token.")

        return token

    async def _ensure_token(self, client: httpx.AsyncClient) -> str:
        if self.access_token:
            return self.access_token

        async with self._token_lock:
            if self.access_token:
                return self.access_token
            self.access_token = await self._request_token(client)
            return self.access_token

    def _headers(self, token: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "HH-User-Agent": self.user_agent,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str | int]],
        token: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.BASE_URL.rstrip('/')}/{path.lstrip('/')}"

        last_error: Exception | None = None
        current_token = token

        for attempt in range(1, self.retry_count + 1):
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=self._headers(current_token),
                )

                if response.status_code in {401, 403} and current_token and self.client_id:
                    async with self._token_lock:
                        self.access_token = ""
                    current_token = await self._ensure_token(client)
                    response = await client.request(
                        method,
                        url,
                        params=params,
                        headers=self._headers(current_token),
                    )

                if response.status_code == 429:
                    if attempt == self.retry_count:
                        raise HHApiError(
                            f"HH API rate limit (429): {response.text[:500]}"
                        )
                    await asyncio.sleep(min(2 ** attempt, 10))
                    continue

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

        raise HHApiError(
            f"HH API request failed: {path}: {last_error!r}"
        )

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
            client,
            "GET",
            "/vacancies",
            params=params,
            token=token,
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
            params=[
                ("host", self.host),
                ("locale", self.locale),
            ],
            token=token,
        )


def parse_hh_datetime(value: str | None) -> datetime | None:
    """Parse HH ISO-8601 timestamps into UTC-aware datetimes."""
    if not value:
        return None

    raw = value.strip()
    if not raw:
        return None

    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def build_hh_search_url(
    *,
    text: str,
    period_days: int,
    work_format: str | None = "REMOTE",
) -> str:
    """Build a human-readable URL useful for logs/debugging."""
    params: list[tuple[str, str | int]] = [
        ("text", text),
        ("period", period_days),
        ("order_by", "publication_time"),
    ]
    if work_format:
        params.append(("work_format", work_format))
    return f"https://hh.ru/search/vacancy?{urlencode(params)}"
