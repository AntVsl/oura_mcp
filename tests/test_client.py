from datetime import date
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from ouraring_mcp.client import OuraClient, OuraError
from ouraring_mcp.config import SANDBOX_BASE, Settings

START, END = date(2026, 1, 1), date(2026, 1, 7)


def settings() -> Settings:
    return Settings(
        mode="sandbox",
        tz=ZoneInfo("Europe/Moscow"),
        client_id=None,
        client_secret=None,
        redirect_uri="http://localhost:8765/callback",
        token_store=None,  # type: ignore[arg-type]
        cache_db=None,  # type: ignore[arg-type]
    )


async def fetch(endpoint: str = "daily_sleep"):
    async with OuraClient(settings()) as client:
        return await client.fetch(endpoint, START, END)


@respx.mock
async def test_follows_pagination():
    route = respx.get(f"{SANDBOX_BASE}/daily_sleep")
    route.side_effect = [
        httpx.Response(200, json={"data": [{"day": "2026-01-01"}], "next_token": "t1"}),
        httpx.Response(200, json={"data": [{"day": "2026-01-02"}], "next_token": None}),
    ]
    rows = await fetch()
    assert len(rows) == 2, "вторая страница должна быть подтянута"
    assert route.call_count == 2


@respx.mock
async def test_repeated_next_token_does_not_loop():
    """Если API вернёт тот же токен, клиент обязан остановиться, а не зациклиться."""
    respx.get(f"{SANDBOX_BASE}/daily_sleep").mock(
        return_value=httpx.Response(200, json={"data": [{"day": "x"}], "next_token": "same"})
    )
    rows = await fetch()
    assert len(rows) == 2


@respx.mock
async def test_retries_on_transport_error(monkeypatch):
    monkeypatch.setattr("ouraring_mcp.client.OuraClient._sleep", _no_sleep)
    route = respx.get(f"{SANDBOX_BASE}/daily_sleep")
    route.side_effect = [
        httpx.ConnectError("оборвалось"),
        httpx.Response(200, json={"data": [{"day": "2026-01-01"}]}),
    ]
    assert len(await fetch()) == 1


@respx.mock
async def test_retries_on_server_error(monkeypatch):
    monkeypatch.setattr("ouraring_mcp.client.OuraClient._sleep", _no_sleep)
    route = respx.get(f"{SANDBOX_BASE}/daily_sleep")
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(200, json={"data": []}),
    ]
    assert await fetch() == []


@respx.mock
async def test_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr("ouraring_mcp.client.OuraClient._sleep", _no_sleep)
    respx.get(f"{SANDBOX_BASE}/daily_sleep").mock(side_effect=httpx.ConnectError("нет"))
    with pytest.raises(OuraError, match="соединение с Oura не установилось"):
        await fetch()


@respx.mock
async def test_403_names_the_missing_scope():
    respx.get(f"{SANDBOX_BASE}/daily_spo2").mock(
        return_value=httpx.Response(403, json={"detail": "forbidden"})
    )
    with pytest.raises(OuraError, match="spo2"):
        await fetch("daily_spo2")


@respx.mock
async def test_401_suggests_reauth():
    respx.get(f"{SANDBOX_BASE}/daily_sleep").mock(
        return_value=httpx.Response(401, json={"detail": "expired"})
    )
    with pytest.raises(OuraError, match="авторизацию заново"):
        await fetch()


@respx.mock
async def test_sleep_window_is_padded_for_bedtime_filtering():
    """Oura фильтрует sleep по bedtime_start в UTC, а не по полю day. При отбое
    после полуночи в поясе +03 запрос 28..28 отдаёт пусто, хотя запись с day=28
    есть. Поэтому окно расширяется на сутки."""
    route = respx.get(f"{SANDBOX_BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    async with OuraClient(settings()) as client:
        await client.fetch("sleep", date(2026, 7, 28), date(2026, 7, 28))
    params = route.calls[0].request.url.params
    assert params["start_date"] == "2026-07-27"
    assert params["end_date"] == "2026-07-29"


@respx.mock
async def test_sleep_trims_rows_pulled_in_by_padding():
    """Расширенное окно тянет лишние дни — наружу они уходить не должны."""
    respx.get(f"{SANDBOX_BASE}/sleep").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"day": "2026-07-27"},
                    {"day": "2026-07-28"},
                    {"day": "2026-07-29"},
                ]
            },
        )
    )
    async with OuraClient(settings()) as client:
        rows = await client.fetch("sleep", date(2026, 7, 28), date(2026, 7, 28))
    assert [r["day"] for r in rows] == ["2026-07-28"]


@respx.mock
async def test_daily_endpoints_are_not_padded():
    """Расширение нужно только sleep — daily_* фильтруют по day корректно."""
    route = respx.get(f"{SANDBOX_BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    async with OuraClient(settings()) as client:
        await client.fetch("daily_readiness", date(2026, 7, 28), date(2026, 7, 28))
    params = route.calls[0].request.url.params
    assert params["start_date"] == "2026-07-28"
    assert params["end_date"] == "2026-07-28"


@respx.mock
async def test_heartrate_uses_datetime_params():
    route = respx.get(f"{SANDBOX_BASE}/heartrate").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await fetch("heartrate")
    params = route.calls[0].request.url.params
    assert "start_datetime" in params and "start_date" not in params


async def test_production_without_token_provider_explains_itself():
    prod = Settings(**{**settings().__dict__, "mode": "production"})
    async with OuraClient(prod) as client:
        with pytest.raises(OuraError, match="ouraring-mcp auth"):
            await client.fetch("daily_sleep", START, END)


async def _no_sleep(*args):  # подменяет staticmethod — приходит лишний self
    return None
