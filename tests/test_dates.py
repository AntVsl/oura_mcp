from datetime import date
from zoneinfo import ZoneInfo

import pytest

from ouraring_mcp.dates import DateRangeError, resolve_range, to_datetime_bounds

MSK = ZoneInfo("Europe/Moscow")


def test_days_back_includes_today():
    start, end = resolve_range(MSK, days_back=7)
    assert (end - start).days == 6, "7 дней — это сегодня плюс шесть предыдущих"


def test_days_back_one_is_single_day():
    start, end = resolve_range(MSK, days_back=1)
    assert start == end


def test_explicit_range_is_respected():
    start, end = resolve_range(MSK, start_date="2026-01-01", end_date="2026-01-10")
    assert (start, end) == (date(2026, 1, 1), date(2026, 1, 10))


def test_explicit_dates_win_over_days_back():
    """MCP-клиенты досылают объявленный default days_back вместе с датами.
    Ошибка здесь ломала любой запрос с диапазоном, поэтому даты приоритетнее."""
    start, end = resolve_range(
        MSK, days_back=7, start_date="2026-01-01", end_date="2026-01-10"
    )
    assert (start, end) == (date(2026, 1, 1), date(2026, 1, 10))


def test_days_back_ignored_when_only_start_date_given():
    start, _ = resolve_range(MSK, days_back=90, start_date="2026-01-01")
    assert start == date(2026, 1, 1)


def test_reversed_range_rejected():
    with pytest.raises(DateRangeError, match="позже"):
        resolve_range(MSK, start_date="2026-02-01", end_date="2026-01-01")


def test_absurd_range_rejected():
    with pytest.raises(DateRangeError, match="максимум"):
        resolve_range(MSK, start_date="2020-01-01", end_date="2026-01-01")


def test_bad_format_rejected():
    with pytest.raises(DateRangeError, match="YYYY-MM-DD"):
        resolve_range(MSK, start_date="01.02.2026", end_date="2026-02-05")


def test_zero_days_back_rejected():
    with pytest.raises(DateRangeError, match="не меньше 1"):
        resolve_range(MSK, days_back=0)


def test_timezone_shifts_today():
    """Смысл существования модуля: 'сегодня' зависит от пояса."""
    _, end_msk = resolve_range(MSK, days_back=1)
    _, end_utc = resolve_range(ZoneInfo("UTC"), days_back=1)
    assert (end_msk - end_utc).days in (0, 1)


def test_datetime_bounds_cover_last_day():
    lo, hi = to_datetime_bounds(date(2026, 1, 1), date(2026, 1, 2), MSK)
    assert lo.startswith("2026-01-01T00:00:00")
    assert hi.startswith("2026-01-02T23:59:59"), "конец диапазона — конец суток"
