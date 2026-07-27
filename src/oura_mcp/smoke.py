"""Проверка сквозного пути: конфиг → HTTP → пагинация → сжатие.

Запуск: `uv run python -m oura_mcp.smoke`
В режиме sandbox работает без авторизации.
"""

from __future__ import annotations

import asyncio
import json
import sys

from . import shaping
from .client import OuraClient, OuraError
from .config import ConfigError, load_settings
from .dates import resolve_range

CHECKS = ["daily_sleep", "daily_readiness", "daily_activity", "sleep", "daily_stress"]


async def _run() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Конфигурация: {exc}")
        return 2

    print(f"режим: {settings.mode}  |  {settings.base_url}  |  TZ {settings.tz}\n")
    start, end = resolve_range(settings.tz, days_back=7)
    failures = 0

    async with OuraClient(settings) as client:
        for endpoint in CHECKS:
            try:
                rows = await client.fetch(endpoint, start, end)
            except OuraError as exc:
                print(f"[FAIL] {endpoint}: {exc}")
                failures += 1
                continue
            shaper = shaping.SHAPERS.get(endpoint)
            shaped = shaper(rows) if shaper else {"data": rows}
            raw_len = len(json.dumps(rows))
            shaped_len = len(json.dumps(shaped))
            ratio = f"{raw_len / shaped_len:.1f}x" if shaped_len else "—"
            print(
                f"[ ok ] {endpoint}: {len(rows)} записей, "
                f"{raw_len} → {shaped_len} байт (сжатие {ratio})"
            )

    if failures:
        print(f"\nНеудачных эндпоинтов: {failures} из {len(CHECKS)}")
        return 1
    print("\nВсе проверки прошли.")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
