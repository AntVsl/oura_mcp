"""HTTP-клиент Oura API v2.

Три вещи, которых не даёт «просто requests.get»:
  * обход next_token — иначе длинные диапазоны молча обрезаются на первой странице;
  * ретраи с бэкоффом — соединение до api.ouraring.com рвётся регулярно;
  * перевод кодов ответа в сообщения, по которым понятно, что чинить.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

import httpx

from .cache import DayCache, days_between
from .config import Settings
from .dates import to_datetime_bounds, today

# Эндпоинты, принимающие start_datetime/end_datetime вместо start_date/end_date.
DATETIME_ENDPOINTS = frozenset({"heartrate"})

# Эндпоинты, которые Oura фильтрует по внутренней метке времени в UTC, а не по
# полю day, которое сама же возвращает. В поясе +03 запись «за сегодня» уезжает
# в предыдущие сутки UTC и в окно не попадает: запрос 28..28 отдаёт пусто, хотя
# запись с day=28 существует и видна в 27..28.
#
# Проверено перебором на реальных данных: sleep и daily_activity теряют записи
# в 8 случаях из 8, остальные эндпоинты (daily_sleep, daily_readiness,
# daily_spo2, daily_stress, daily_resilience, daily_cardiovascular_age)
# отвечают одинаково на узкое и расширенное окно.
#
# Лечим расширением окна с последующей фильтрацией по day на нашей стороне.
UTC_WINDOW_FILTERED = frozenset({"sleep", "daily_activity"})
UTC_WINDOW_PAD_DAYS = 1

# Какой скоуп нужен эндпоинту — чтобы 403 объяснял себя сам.
ENDPOINT_SCOPES = {
    "daily_sleep": "daily",
    "daily_readiness": "daily",
    "daily_activity": "daily",
    "daily_resilience": "daily",
    "sleep": "daily",
    "sleep_time": "daily",
    "heartrate": "heartrate",
    "daily_spo2": "spo2",
    "daily_stress": "stress",
    "daily_cardiovascular_age": "heart_health",
    "vO2_max": "heart_health",
    "enhanced_tag": "tag",
    "tag": "tag",
}

MAX_ATTEMPTS = 4
MAX_PAGES = 50
TIMEOUT = httpx.Timeout(30.0, connect=15.0)

TokenProvider = Callable[[], Awaitable[str]]


class OuraError(RuntimeError):
    """Ошибка обращения к Oura, пригодная для показа человеку и модели."""


def _trim_to_days(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    """Отбрасывает записи, попавшие только из-за расширения окна."""
    lo, hi = start.isoformat(), end.isoformat()
    return [r for r in rows if r.get("day") and lo <= r["day"] <= hi]


class OuraClient:
    def __init__(
        self,
        settings: Settings,
        token_provider: TokenProvider | None = None,
        http: httpx.AsyncClient | None = None,
        cache: DayCache | None = None,
    ) -> None:
        self._settings = settings
        self._token_provider = token_provider
        self._http = http
        self._owns_http = http is None
        self._cache = cache

    async def __aenter__(self) -> OuraClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=TIMEOUT)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _token(self) -> str:
        if self._settings.is_sandbox:
            # Песочница принимает любую непустую строку.
            return "sandbox"
        if self._token_provider is None:
            raise OuraError(
                "Режим production требует авторизации, но провайдер токена не задан. "
                "Пройди OAuth: `uv run my-oura-mcp auth`"
            )
        return await self._token_provider()

    async def fetch(
        self,
        endpoint: str,
        start: date,
        end: date,
        extra_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Все записи эндпоинта за диапазон, со всех страниц.

        При наличии кэша завершённые сутки берутся из него, а в сеть уходит
        только недостающий отрезок.
        """
        if self._cache is not None and not extra_params and endpoint not in DATETIME_ENDPOINTS:
            return await self._fetch_cached(endpoint, start, end)
        return await self._fetch_remote(endpoint, start, end, extra_params)

    async def _fetch_cached(
        self, endpoint: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        assert self._cache is not None
        cached = self._cache.lookup(endpoint, start, end)
        missing = [d for d in days_between(start, end) if d.isoformat() not in cached]

        if not missing:
            # Ни одного обращения к сети: весь диапазон уже лежит в базе.
            return [row for day in sorted(cached) for row in cached[day]]

        # Запрашиваем одним отрезком от первого недостающего дня до последнего.
        # Дырка посередине приведёт к перезапросу уже известных суток — это
        # дешевле, чем дробить диапазон на куски и слать несколько запросов.
        fresh = await self._fetch_remote(endpoint, min(missing), max(missing), None)
        self._cache.store(endpoint, fresh, today(self._settings.tz))

        fresh_by_day: dict[str, list[dict[str, Any]]] = {}
        for row in fresh:
            day = row.get("day")
            if isinstance(day, str) and start.isoformat() <= day <= end.isoformat():
                fresh_by_day.setdefault(day, []).append(row)

        # Свежие сутки заменяют закэшированные целиком, а не дополняются к ним:
        # перезапрошенный день иначе удвоился бы в ответе.
        merged = {**cached, **fresh_by_day}
        return [row for day in sorted(merged) for row in merged[day]]

    async def _fetch_remote(
        self,
        endpoint: str,
        start: date,
        end: date,
        extra_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        pad = UTC_WINDOW_PAD_DAYS if endpoint in UTC_WINDOW_FILTERED else 0

        if endpoint in DATETIME_ENDPOINTS:
            lo, hi = to_datetime_bounds(start, end, self._settings.tz)
            params: dict[str, Any] = {"start_datetime": lo, "end_datetime": hi}
        else:
            params = {
                "start_date": (start - timedelta(days=pad)).isoformat(),
                "end_date": (end + timedelta(days=pad)).isoformat(),
            }
        if extra_params:
            params.update(extra_params)

        token = await self._token()
        rows: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()

        for _ in range(MAX_PAGES):
            payload = await self._request(endpoint, params, token)
            rows.extend(payload.get("data") or [])
            nxt = payload.get("next_token")
            if not nxt or nxt in seen_tokens:
                return _trim_to_days(rows, start, end) if pad else rows
            seen_tokens.add(nxt)
            params = {**params, "next_token": nxt}

        raise OuraError(
            f"{endpoint}: превышен лимит в {MAX_PAGES} страниц — сузь диапазон дат"
        )

    async def _request(
        self, endpoint: str, params: dict[str, Any], token: str
    ) -> dict[str, Any]:
        if self._http is None:
            raise OuraError("Клиент используется вне контекстного менеджера")

        url = f"{self._settings.base_url}/{endpoint}"
        headers = {"Authorization": f"Bearer {token}"}
        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await self._http.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                # Обрыв соединения — единственный класс ошибок, который здесь
                # действительно стоит перепроверять молча.
                last_error = exc
                if attempt == MAX_ATTEMPTS:
                    break
                await self._sleep(attempt, None)
                continue

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise OuraError(
                        f"{endpoint}: ответ не разобрался как JSON "
                        f"(HTTP 200, {len(resp.content)} байт)"
                    ) from exc

            if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS:
                await self._sleep(attempt, resp.headers.get("Retry-After"))
                continue

            raise self._explain(endpoint, resp)

        raise OuraError(
            f"{endpoint}: соединение с Oura не установилось за {MAX_ATTEMPTS} попытки "
            f"({type(last_error).__name__}). Проверь сеть или VPN."
        ) from last_error

    @staticmethod
    async def _sleep(attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                await asyncio.sleep(min(float(retry_after), 30.0))
                return
            except ValueError:
                pass
        await asyncio.sleep(min(2**attempt, 16) * (0.5 + random.random() / 2))

    def _explain(self, endpoint: str, resp: httpx.Response) -> OuraError:
        detail = ""
        try:
            body = resp.json()
            detail = str(body.get("detail") or body)[:300]
        except ValueError:
            detail = resp.text[:200]

        code = resp.status_code
        if code == 401:
            hint = (
                "токен истёк или отозван — пройди авторизацию заново: "
                "`uv run my-oura-mcp auth`"
            )
        elif code == 403:
            scope = ENDPOINT_SCOPES.get(endpoint)
            hint = (
                f"нет доступа к данным. Проверь, что скоуп '{scope}' включён "
                "в приложении на developer.ouraring.com и что авторизация "
                "проходилась уже после его включения"
                if scope
                else "нет доступа к данным — проверь скоупы приложения"
            )
        elif code == 404:
            hint = f"эндпоинт '{endpoint}' не найден — возможно, переименован в API"
        elif code == 422:
            hint = "Oura отверг параметры запроса"
        elif code == 429:
            hint = "превышен лимит запросов (5000 за 5 минут), попытки исчерпаны"
        else:
            hint = "неожиданный ответ Oura"

        return OuraError(f"{endpoint}: HTTP {code} — {hint}. Ответ: {detail}")
