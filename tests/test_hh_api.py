import asyncio

import httpx
import pytest

from qa_job_scout.hh_api import HHApiAccessError, HHApiAuthError, HHApiCaptchaError, HHApiClient


def test_vacancy_search_uses_saved_user_oauth(monkeypatch, tmp_path):
    monkeypatch.setenv("HH_TOKEN_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("HH_AUTH_MODE", "user")
    monkeypatch.setenv("HH_VACANCY_AUTH_MODE", "user")
    api = HHApiClient()
    api.access_token = "user-token"

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"items": []}

    class FakeClient:
        async def request(self, *args, **kwargs):
            captured["headers"] = kwargs["headers"]
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
    assert captured["headers"]["Authorization"] == "Bearer user-token"


def test_anonymous_vacancy_mode_sends_no_authorization(monkeypatch, tmp_path):
    monkeypatch.setenv("HH_TOKEN_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("HH_VACANCY_AUTH_MODE", "anonymous")
    api = HHApiClient()
    api.access_token = "user-token"
    captured = {}

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"items": []}

    class FakeClient:
        async def request(self, *args, **kwargs):
            captured["headers"] = kwargs["headers"]
            return FakeResponse()

    async def run():
        await api.search_vacancies(
            FakeClient(), text="QA Engineer", period_days=5, page=0, per_page=1
        )

    asyncio.run(run())
    assert "Authorization" not in captured["headers"]


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
                await api._request_json(
                    client,
                    "GET",
                    "/some-authenticated-endpoint",
                    params=[],
                    token="test-token",
                )

    asyncio.run(run())
    assert api.access_token == ""


def test_generic_forbidden_is_not_misclassified_as_captcha(monkeypatch, tmp_path):
    monkeypatch.setenv("HH_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("HH_VACANCY_AUTH_MODE", "anonymous")
    api = HHApiClient()

    def handler(request):
        return httpx.Response(
            403,
            json={
                "request_id": "req-forbidden",
                "errors": [{"type": "forbidden"}],
            },
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(HHApiAccessError, match="HH.ru отклонил запрос"):
                await api.search_vacancies(
                    client,
                    text="QA Engineer",
                    period_days=5,
                    page=0,
                    per_page=1,
                )

    asyncio.run(run())


def test_captcha_response_is_classified_and_keeps_urls(monkeypatch, tmp_path):
    monkeypatch.setenv("HH_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("HH_VACANCY_AUTH_MODE", "anonymous")
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
