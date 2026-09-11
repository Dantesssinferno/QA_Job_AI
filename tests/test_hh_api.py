import asyncio

import httpx
import pytest

from qa_job_scout.hh_api import HHApiAuthError, HHApiCaptchaError, HHApiClient


def test_vacancy_search_does_not_require_oauth_token(monkeypatch, tmp_path):
    monkeypatch.setenv("HH_TOKEN_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("HH_AUTH_MODE", "user")
    api = HHApiClient()

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"items": []}

    class FakeClient:
        async def request(self, *args, **kwargs):
            return FakeResponse()

    async def run():
        payload = await api.search_vacancies(
            FakeClient(),
            text="QA Engineer",
            period_days=5,
            page=0,
            per_page=1,
        )
        assert payload == {"items": []}

    asyncio.run(run())


def test_oauth_bad_authorization_is_classified(monkeypatch, tmp_path):
    monkeypatch.setenv("HH_TOKEN_FILE", str(tmp_path / "tokens.json"))
    api = HHApiClient()
    api.access_token = "test-token"

    def handler(request):
        return httpx.Response(
            403,
            json={
                "request_id": "req-1",
                "errors": [{"type": "oauth", "value": "bad_authorization"}],
            },
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(HHApiAuthError, match="токен недействителен"):
                await api.search_vacancies(
                    client,
                    text="QA Engineer",
                    period_days=5,
                    page=0,
                    per_page=1,
                )

    asyncio.run(run())
    assert api.access_token == ""


def test_captcha_response_is_classified_and_keeps_urls(monkeypatch, tmp_path):
    monkeypatch.setenv("HH_TOKEN_FILE", str(tmp_path / "tokens.json"))
    api = HHApiClient()
    api.access_token = "test-token"

    def handler(request):
        return httpx.Response(
            403,
            json={
                "request_id": "req-2",
                "errors": [
                    {
                        "type": "captcha_required",
                        "value": "captcha_required",
                        "captcha_url": "https://hh.ru/account/captcha?state=abc",
                        "fallback_url": "https://hh.ru/search/vacancy?text=QA",
                    }
                ],
            },
        )

    captured = {}

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(HHApiCaptchaError) as exc_info:
                await api.search_vacancies(
                    client,
                    text="QA Engineer",
                    period_days=5,
                    page=0,
                    per_page=1,
                )
            captured["exc"] = exc_info.value

    asyncio.run(run())
    exc = captured["exc"]
    assert exc.request_id == "req-2"
    assert exc.captcha_url.endswith("state=abc")
    assert exc.fallback_url.startswith("https://hh.ru/")


def test_build_captcha_url_adds_backurl():
    url = HHApiClient.build_captcha_url(
        "https://hh.ru/account/captcha?state=abc",
        "https://hh.ru/",
    )
    assert "state=abc" in url
    assert "backurl=https%3A%2F%2Fhh.ru%2F" in url
