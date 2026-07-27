"""Определения MCP-инструментов.

Инструменты нарезаны по вопросам, а не по эндпоинтам Oura: типичный запрос —
«как я спал на прошлой неделе», а не «отдай daily_sleep». Поэтому есть
get_daily_summary, собирающий три эндпоинта в один ответ.

У каждого инструмента есть raw: по умолчанию возвращается сжатая сводка,
raw=True отдаёт нетронутый ответ Oura.
"""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from . import shaping
from .client import TokenProvider, OuraClient, OuraError, TIMEOUT
from .config import Settings
from .dates import DateRangeError, resolve_range

def register(
    mcp: FastMCP, settings: Settings, token_provider: TokenProvider | None = None
) -> None:
    http: httpx.AsyncClient | None = None

    async def fetch(
        endpoint: str,
        days_back: int | None,
        start_date: str | None,
        end_date: str | None,
    ) -> list[dict[str, Any]]:
        nonlocal http
        if http is None:
            http = httpx.AsyncClient(timeout=TIMEOUT)
        start, end = resolve_range(settings.tz, days_back, start_date, end_date)
        client = OuraClient(settings, token_provider, http=http)
        return await client.fetch(endpoint, start, end)

    async def serve(
        endpoint: str,
        days_back: int | None,
        start_date: str | None,
        end_date: str | None,
        raw: bool,
        shaper: Any = None,
    ) -> dict[str, Any]:
        """Общий путь: достать, при необходимости сжать, ошибки — текстом."""
        try:
            rows = await fetch(endpoint, days_back, start_date, end_date)
        except (OuraError, DateRangeError) as exc:
            return {"error": str(exc)}

        if raw:
            return {"endpoint": endpoint, "count": len(rows), "data": rows}
        fn = shaper or shaping.SHAPERS.get(endpoint)
        return fn(rows) if fn else {"endpoint": endpoint, "data": rows}

    # --- сводка --------------------------------------------------------------

    @mcp.tool()
    async def get_daily_summary(
        days_back: int = 7,
        start_date: str | None = None,
        end_date: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Общая картина по дням: оценки сна, готовности и активности сразу.

        Самый частый запрос — начинай с него, а за деталями иди в get_sleep
        и остальные инструменты.
        """
        out: dict[str, Any] = {}
        for name, endpoint in (
            ("sleep", "daily_sleep"),
            ("readiness", "daily_readiness"),
            ("activity", "daily_activity"),
        ):
            out[name] = await serve(endpoint, days_back, start_date, end_date, raw)
        return out

    # --- сон ------------------------------------------------------------------

    @mcp.tool()
    async def get_sleep(
        days_back: int = 7,
        start_date: str | None = None,
        end_date: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Детальный сон: стадии, эффективность, HRV, пульс покоя, дыхание,
        отклонение температуры тела. Здесь же лежат ночные HRV и lowest_hr."""
        return await serve("sleep", days_back, start_date, end_date, raw)

    @mcp.tool()
    async def get_sleep_score(
        days_back: int = 7,
        start_date: str | None = None,
        end_date: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Только дневная оценка сна и её вклады. Легче, чем get_sleep."""
        return await serve("daily_sleep", days_back, start_date, end_date, raw)

    # --- готовность и активность ---------------------------------------------

    @mcp.tool()
    async def get_readiness(
        days_back: int = 7,
        start_date: str | None = None,
        end_date: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Готовность (readiness): оценка, баланс HRV, отклонение температуры."""
        return await serve("daily_readiness", days_back, start_date, end_date, raw)

    @mcp.tool()
    async def get_activity(
        days_back: int = 7,
        start_date: str | None = None,
        end_date: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Активность: оценка, шаги, активные и общие калории."""
        return await serve("daily_activity", days_back, start_date, end_date, raw)

    # --- сердце и дыхание -----------------------------------------------------

    @mcp.tool()
    async def get_heartrate(
        days_back: int = 3,
        start_date: str | None = None,
        end_date: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Поминутный пульс, свёрнутый посуточно (среднее, минимум, максимум).

        raw=True отдаёт весь ряд — это тысячи точек в сутки, бери узкий диапазон.
        """
        return await serve("heartrate", days_back, start_date, end_date, raw)

    @mcp.tool()
    async def get_spo2(
        days_back: int = 7,
        start_date: str | None = None,
        end_date: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Насыщение крови кислородом во сне и индекс нарушений дыхания."""
        return await serve("daily_spo2", days_back, start_date, end_date, raw)

    @mcp.tool()
    async def get_stress(
        days_back: int = 7,
        start_date: str | None = None,
        end_date: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Дневной стресс: время под нагрузкой и в восстановлении, оценка дня."""
        return await serve("daily_stress", days_back, start_date, end_date, raw)

    @mcp.tool()
    async def get_heart_health(
        days_back: int = 30,
        start_date: str | None = None,
        end_date: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Сосудистый возраст и VO2max. Обновляются редко — бери период пошире."""
        out: dict[str, Any] = {}
        for name, endpoint in (
            ("cardiovascular_age", "daily_cardiovascular_age"),
            ("vo2_max", "vO2_max"),
        ):
            out[name] = await serve(
                endpoint,
                days_back,
                start_date,
                end_date,
                raw,
                shaper=lambda rows, m=name: shaping.heart_health(rows, m),
            )
        return out

    # --- теги ------------------------------------------------------------------

    @mcp.tool()
    async def get_tags(
        days_back: int = 30,
        start_date: str | None = None,
        end_date: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any]:
        """Отметки, проставленные вручную в приложении Oura."""
        return await serve("enhanced_tag", days_back, start_date, end_date, raw)

    # --- служебное -------------------------------------------------------------

    @mcp.tool()
    async def get_status() -> dict[str, Any]:
        """Режим работы сервера и состояние авторизации. Полезно при отладке."""
        return {
            "mode": settings.mode,
            "base_url": settings.base_url,
            "timezone": str(settings.tz),
            "authorized": settings.is_sandbox or token_provider is not None,
            "note": (
                "Режим sandbox: данные тестовые, не твои. "
                "Переключи OURA_API_MODE=production в .env после OAuth."
                if settings.is_sandbox
                else "Режим production: возвращаются твои реальные данные."
            ),
        }
