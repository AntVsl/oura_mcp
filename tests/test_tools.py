"""Инструменты через настоящий MCP-слой, а не прямым вызовом функции.

Это принципиально: баг с days_back=7 воспроизводился только при вызове через
mcp.call_tool(), потому что клиент MCP подставляет объявленный по умолчанию
параметр как явный аргумент. Прямой вызов Python-функции его бы не поймал.
"""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from oura_mcp.config import SANDBOX_BASE, Settings
from oura_mcp.server import build

MSK = ZoneInfo("Europe/Moscow")


def settings() -> Settings:
    return Settings(
        mode="sandbox",
        tz=MSK,
        client_id=None,
        client_secret=None,
        redirect_uri="http://localhost:8765/callback",
        token_store=None,  # type: ignore[arg-type]
        cache_db=None,  # type: ignore[arg-type]
    )


def stub(endpoint: str, day: str = "2026-07-28"):
    respx.get(f"{SANDBOX_BASE}/{endpoint}").mock(
        return_value=httpx.Response(200, json={"data": [{"day": day, "score": 78}]})
    )


async def call(name: str, args: dict):
    mcp = build(settings())
    res = await mcp.call_tool(name, args)
    return res[1] if isinstance(res, tuple) else res


# --- регрессия: MCP-клиент подставляет объявленный default как явный аргумент


@respx.mock
async def test_explicit_dates_work_when_client_also_sends_declared_default():
    """Воспроизводит реальный вызов: days_back=7 (default) приходит ВМЕСТЕ
    с осознанно заданными start_date/end_date — так делает MCP-клиент."""
    stub("sleep")
    out = await call(
        "get_sleep",
        {"days_back": 7, "start_date": "2026-07-28", "end_date": "2026-07-28"},
    )
    assert "error" not in out, out


@respx.mock
async def test_dates_only_no_days_back_key_at_all():
    stub("sleep")
    out = await call("get_sleep", {"start_date": "2026-07-28", "end_date": "2026-07-28"})
    assert "error" not in out, out


@respx.mock
async def test_days_back_only_still_works():
    stub("sleep")
    out = await call("get_sleep", {"days_back": 3})
    assert "error" not in out, out


@respx.mock
async def test_explicit_dates_take_precedence_over_days_back():
    """При конфликте выигрывают даты: спорить с явно заданным диапазоном не за что.

    Берём daily_sleep, а не sleep: у последнего окно намеренно расширяется
    из-за фильтрации по bedtime_start, и это проверяется отдельно.
    """
    route = respx.get(f"{SANDBOX_BASE}/daily_sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await call(
        "get_sleep_score",
        {"days_back": 14, "start_date": "2026-07-01", "end_date": "2026-07-10"},
    )
    params = route.calls[0].request.url.params
    assert params["start_date"] == "2026-07-01"
    assert params["end_date"] == "2026-07-10"


@respx.mock
async def test_no_arguments_falls_back_to_tool_default():
    route = respx.get(f"{SANDBOX_BASE}/daily_sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await call("get_sleep_score", {})
    params = route.calls[0].request.url.params
    start = date.fromisoformat(params["start_date"])
    end = date.fromisoformat(params["end_date"])
    assert (end - start).days == 6, "по умолчанию 7 дней — это сегодня плюс 6"


@respx.mock
async def test_heartrate_default_is_three_days_not_seven():
    respx.get(f"{SANDBOX_BASE}/heartrate").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    route = respx.get(f"{SANDBOX_BASE}/heartrate")
    await call("get_heartrate", {})
    params = route.calls[-1].request.url.params
    start = date.fromisoformat(params["start_datetime"][:10])
    end = date.fromisoformat(params["end_datetime"][:10])
    assert (end - start).days == 2, "get_heartrate по умолчанию — 3 дня, не 7"


@respx.mock
async def test_tags_default_is_thirty_days():
    route = respx.get(f"{SANDBOX_BASE}/enhanced_tag").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await call("get_tags", {})
    params = route.calls[0].request.url.params
    start = date.fromisoformat(params["start_date"])
    end = date.fromisoformat(params["end_date"])
    assert (end - start).days == 29, "get_tags по умолчанию — 30 дней"


# --- daily_summary и heart_health пробрасывают даты во все под-вызовы -------


@respx.mock
async def test_daily_summary_accepts_explicit_range():
    for ep in ("daily_sleep", "daily_readiness", "daily_activity"):
        stub(ep)
    out = await call(
        "get_daily_summary",
        {"days_back": 7, "start_date": "2026-07-20", "end_date": "2026-07-26"},
    )
    assert all("error" not in out[k] for k in ("sleep", "readiness", "activity")), out


@respx.mock
async def test_heart_health_accepts_explicit_range():
    for ep in ("daily_cardiovascular_age", "vO2_max"):
        respx.get(f"{SANDBOX_BASE}/{ep}").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
    out = await call(
        "get_heart_health",
        {"days_back": 30, "start_date": "2026-06-01", "end_date": "2026-06-30"},
    )
    assert "error" not in out["cardiovascular_age"]
    assert "error" not in out["vo2_max"]


# --- today выглядит правдоподобно: запись «сегодня» доступна без конфликта -


@respx.mock
async def test_today_only_range_is_reachable():
    """Ровно тот сценарий, что уронил get_sleep(days_back=1) на практике:
    узнать «сегодня» с явными одинаковыми start/end."""
    today = date.today().isoformat()
    stub("sleep", day=today)
    out = await call(
        "get_sleep", {"days_back": 7, "start_date": today, "end_date": today}
    )
    assert "error" not in out, out
    assert out["daily"], "запись за сегодня должна попасть в ответ"
