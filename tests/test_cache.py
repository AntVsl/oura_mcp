"""Кэш завершённых суток.

Кэш опасен ровно тем же, чем были пять багов проекта: он врёт молча. Неверно
закэшированный день выглядит как обычный ответ, и заметить подмену можно только
сверив с Oura вручную. Поэтому здесь проверяется не столько «работает ли
ускорение», сколько «не может ли оно соврать».
"""

from datetime import date
from pathlib import Path
import stat
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from my_oura_mcp.cache import DayCache, days_between
from my_oura_mcp.client import OuraClient
from my_oura_mcp.config import Settings

TODAY = date(2026, 7, 30)


def settings(tmp_path: Path, mode: str = "production") -> Settings:
    return Settings(
        mode=mode,
        tz=ZoneInfo("Europe/Moscow"),
        client_id="id",
        client_secret="secret",
        redirect_uri="http://localhost:8765/callback",
        token_store=tmp_path / "tokens.json",
        cache_db=tmp_path / "cache.db",
    )


def row(day: str, score: int = 80) -> dict:
    return {"day": day, "score": score}


@pytest.fixture
def cache(tmp_path):
    return DayCache(tmp_path / "cache.db", "production")


# --- что кэшируется, а что нет ----------------------------------------------


def test_completed_days_are_stored(cache):
    saved = cache.store("daily_sleep", [row("2026-07-28"), row("2026-07-29")], TODAY)
    assert saved == 2
    assert set(cache.lookup("daily_sleep", date(2026, 7, 28), date(2026, 7, 29))) == {
        "2026-07-28",
        "2026-07-29",
    }


def test_today_is_never_stored(cache):
    """Данные за текущие сутки Oura ещё дописывает."""
    assert cache.store("daily_sleep", [row("2026-07-30")], TODAY) == 0
    assert cache.lookup("daily_sleep", TODAY, TODAY) == {}


def test_future_days_are_never_stored(cache):
    """Часовые пояса и сдвиг часов могут подсунуть завтрашнюю дату."""
    assert cache.store("daily_sleep", [row("2026-07-31")], TODAY) == 0


def test_empty_days_are_not_stored(cache):
    """Пустота бывает «не носил кольцо» и «ещё не синхронизировалось».

    Второе лечится само через часы, а закэшированная пустота осталась бы
    навсегда — и день молча остался бы без данных.
    """
    assert cache.store("daily_sleep", [], TODAY) == 0
    assert cache.lookup("daily_sleep", date(2026, 7, 1), TODAY) == {}


def test_rows_without_day_are_skipped(cache):
    """Поминутный пульс по суткам здесь не раскладывается."""
    assert cache.store("heartrate", [{"timestamp": "2026-07-28T10:00:00+00:00"}], TODAY) == 0


def test_database_is_owner_readable_only(tmp_path):
    path = tmp_path / "cache.db"
    DayCache(path, "production")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --- изоляция ----------------------------------------------------------------


def test_sandbox_and_production_never_mix(tmp_path):
    """Иначе синтетика всплыла бы под видом настоящих данных."""
    path = tmp_path / "cache.db"
    DayCache(path, "sandbox").store("daily_sleep", [row("2026-07-28", 1)], TODAY)
    prod = DayCache(path, "production")
    assert prod.lookup("daily_sleep", date(2026, 7, 28), date(2026, 7, 28)) == {}


def test_endpoints_do_not_bleed_into_each_other(cache):
    cache.store("daily_sleep", [row("2026-07-28")], TODAY)
    assert cache.lookup("daily_readiness", date(2026, 7, 28), date(2026, 7, 28)) == {}


# --- устойчивость ------------------------------------------------------------


def test_broken_database_degrades_to_no_cache(tmp_path):
    """Кэш — ускорение, а не источник правды: сбой не должен ломать доступ."""
    path = tmp_path / "cache.db"
    path.write_text("это не база данных")
    broken = DayCache(path, "production")
    assert broken.lookup("daily_sleep", TODAY, TODAY) == {}
    assert broken.store("daily_sleep", [row("2026-07-28")], TODAY) == 0
    assert broken.clear() == 0


def test_unwritable_location_does_not_raise(tmp_path):
    blocked = tmp_path / "nope"
    blocked.write_text("файл вместо каталога")
    cache = DayCache(blocked / "sub" / "cache.db", "production")
    assert cache.lookup("daily_sleep", TODAY, TODAY) == {}


# --- обслуживание ------------------------------------------------------------


def test_clear_removes_everything_for_the_mode(cache):
    cache.store("daily_sleep", [row("2026-07-28")], TODAY)
    cache.store("daily_readiness", [row("2026-07-28")], TODAY)
    assert cache.clear() == 2
    assert cache.stats()["days"] == 0


def test_clear_one_endpoint_leaves_the_rest(cache):
    cache.store("daily_sleep", [row("2026-07-28")], TODAY)
    cache.store("daily_readiness", [row("2026-07-28")], TODAY)
    assert cache.clear("daily_sleep") == 1
    assert cache.stats()["by_endpoint"] == {"daily_readiness": 1}


def test_stats_report_coverage(cache):
    cache.store("daily_sleep", [row("2026-07-20"), row("2026-07-28")], TODAY)
    s = cache.stats()
    assert s["available"] is True
    assert s["days"] == 2
    assert s["range"] == ["2026-07-20", "2026-07-28"]


def test_days_between_is_inclusive():
    assert days_between(date(2026, 7, 28), date(2026, 7, 30)) == [
        date(2026, 7, 28),
        date(2026, 7, 29),
        date(2026, 7, 30),
    ]


# --- поведение клиента -------------------------------------------------------


@respx.mock
async def test_fully_cached_range_makes_no_request(tmp_path):
    """Ради этого всё и затевалось."""
    cache = DayCache(tmp_path / "c.db", "production")
    cache.store("daily_sleep", [row("2026-07-20"), row("2026-07-21")], TODAY)

    route = respx.get(url__regex=r".*daily_sleep.*").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    async with httpx.AsyncClient() as http:
        client = OuraClient(settings(tmp_path), lambda: _token(), http=http, cache=cache)
        got = await client.fetch("daily_sleep", date(2026, 7, 20), date(2026, 7, 21))

    assert not route.called, "диапазон был в кэше целиком, сеть не нужна"
    assert [r["day"] for r in got] == ["2026-07-20", "2026-07-21"]


@respx.mock
async def test_only_missing_days_are_requested(tmp_path):
    cache = DayCache(tmp_path / "c.db", "production")
    cache.store("daily_sleep", [row("2026-07-20")], TODAY)

    route = respx.get(url__regex=r".*daily_sleep.*").mock(
        return_value=httpx.Response(200, json={"data": [row("2026-07-21")]})
    )
    async with httpx.AsyncClient() as http:
        client = OuraClient(settings(tmp_path), lambda: _token(), http=http, cache=cache)
        got = await client.fetch("daily_sleep", date(2026, 7, 20), date(2026, 7, 21))

    # Запрошенное окно — только недостающий день. В сеть оно уходит расширенным
    # на сутки в каждую сторону (Oura теряет края узкого окна), поэтому
    # сравниваем с поправкой на это расширение.
    asked = route.calls[0].request.url.params
    assert asked["start_date"] == "2026-07-20", "07-21 минус сутки расширения"
    assert asked["end_date"] == "2026-07-22", "07-21 плюс сутки расширения"
    assert [r["day"] for r in got] == ["2026-07-20", "2026-07-21"]


@respx.mock
async def test_refetched_day_does_not_double(tmp_path):
    """Свежие сутки заменяют закэшированные, а не складываются с ними."""
    cache = DayCache(tmp_path / "c.db", "production")
    # В кэше только середина: недостающие края дадут запрос 20..22, и
    # закэшированный 21-й окажется внутри — вот где день мог бы удвоиться.
    cache.store("daily_sleep", [row("2026-07-21", 50)], TODAY)

    respx.get(url__regex=r".*daily_sleep.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [row("2026-07-20"), row("2026-07-21", 99), row("2026-07-22")]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = OuraClient(settings(tmp_path), lambda: _token(), http=http, cache=cache)
        got = await client.fetch("daily_sleep", date(2026, 7, 20), date(2026, 7, 22))

    days = [r["day"] for r in got]
    assert days == sorted(days), "ответ должен остаться упорядоченным по дням"
    assert days.count("2026-07-21") == 1, "день не должен удвоиться"
    assert [r for r in got if r["day"] == "2026-07-21"][0]["score"] == 99, "свежее важнее"


@respx.mock
async def test_recent_completed_day_is_revalidated(tmp_path, monkeypatch):
    """Вчерашняя частичная синхронизация не должна остаться в кэше навсегда."""
    cache = DayCache(tmp_path / "c.db", "production")
    cache.store("daily_sleep", [row("2026-07-29", 50)], TODAY)
    route = respx.get(url__regex=r".*daily_sleep.*").mock(
        return_value=httpx.Response(200, json={"data": [row("2026-07-29", 90)]})
    )
    monkeypatch.setattr("my_oura_mcp.client.today", lambda _tz: TODAY)
    async with httpx.AsyncClient() as http:
        client = OuraClient(settings(tmp_path), lambda: _token(), http=http, cache=cache)
        got = await client.fetch("daily_sleep", date(2026, 7, 29), date(2026, 7, 29))

    assert route.called
    assert got == [row("2026-07-29", 90)]


@respx.mock
async def test_heartrate_bypasses_the_cache(tmp_path):
    """У поминутного пульса нет поля day — раскладывать его по суткам здесь нечем."""
    cache = DayCache(tmp_path / "c.db", "production")
    route = respx.get(url__regex=r".*heartrate.*").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    async with httpx.AsyncClient() as http:
        client = OuraClient(settings(tmp_path), lambda: _token(), http=http, cache=cache)
        await client.fetch("heartrate", date(2026, 7, 20), date(2026, 7, 21))
        await client.fetch("heartrate", date(2026, 7, 20), date(2026, 7, 21))

    assert route.call_count == 2, "кэш не должен вмешиваться"


async def _token() -> str:
    return "token"
