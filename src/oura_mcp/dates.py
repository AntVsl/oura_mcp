"""Разрешение диапазонов дат.

Вынесено отдельно, потому что «сегодня» зависит от часового пояса: на VPS в UTC
локальный date.today() отдаёт не тот день, и «сон за вчера» молча съезжает.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MAX_DAYS = 400


class DateRangeError(ValueError):
    """Некорректный диапазон. Сообщение уходит модели как есть."""


def today(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()


def resolve_range(
    tz: ZoneInfo,
    days_back: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[date, date]:
    """Возвращает (start, end) включительно.

    Либо days_back (N последних дней, считая сегодня), либо явная пара дат.
    """
    if start_date or end_date:
        if days_back is not None:
            raise DateRangeError(
                "Укажи либо days_back, либо пару start_date/end_date, но не оба сразу"
            )
        end = _parse(end_date, "end_date") if end_date else today(tz)
        start = _parse(start_date, "start_date") if start_date else end - timedelta(days=6)
    else:
        n = 7 if days_back is None else days_back
        if n < 1:
            raise DateRangeError(f"days_back={n} — должно быть не меньше 1")
        end = today(tz)
        start = end - timedelta(days=n - 1)

    if start > end:
        raise DateRangeError(f"start_date ({start}) позже end_date ({end})")
    span = (end - start).days + 1
    if span > MAX_DAYS:
        raise DateRangeError(
            f"Запрошено {span} дней, максимум {MAX_DAYS}. "
            "Для длинных периодов используй агрегирующие инструменты."
        )
    return start, end


def _parse(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError) as exc:
        raise DateRangeError(
            f"{field}={value!r} — ожидается формат YYYY-MM-DD"
        ) from exc


def to_datetime_bounds(start: date, end: date, tz: ZoneInfo) -> tuple[str, str]:
    """Границы для эндпоинтов, принимающих datetime (heartrate).

    end берётся концом дня, иначе последние сутки диапазона теряются.
    """
    lo = datetime.combine(start, datetime.min.time(), tzinfo=tz)
    hi = datetime.combine(end, datetime.max.time().replace(microsecond=0), tzinfo=tz)
    return lo.isoformat(), hi.isoformat()
