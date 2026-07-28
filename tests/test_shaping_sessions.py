"""Несколько записей сна за одни сутки.

Oura отдаёт эндпоинт sleep по сессиям: ночь плюс дневной сон. Раньше shaping
брал произвольную из них, и дрёма на 12 минут вытесняла полноценную ночь —
статистика за период молча получалась неверной.
"""

from oura_mcp import shaping


def night(day, hours, hrv=40, **extra):
    return {
        "day": day,
        "type": "long_sleep",
        "total_sleep_duration": int(hours * 3600),
        "average_hrv": hrv,
        **extra,
    }


def nap(day, hours, hrv=20, kind="late_nap"):
    return {
        "day": day,
        "type": kind,
        "total_sleep_duration": int(hours * 3600),
        "average_hrv": hrv,
    }


def test_long_sleep_wins_over_nap_regardless_of_order():
    """Порядок записей в ответе не гарантирован — выбор не должен от него зависеть."""
    for rows in (
        [nap("2026-07-13", 1.45), night("2026-07-13", 6.75), nap("2026-07-13", 0.24)],
        [night("2026-07-13", 6.75), nap("2026-07-13", 1.45)],
        [nap("2026-07-13", 1.45), night("2026-07-13", 6.75)],
    ):
        out = shaping.sleep_detail(rows)
        assert len(out["daily"]) == 1
        assert out["daily"][0]["total_h"] == 6.75


def test_naps_reported_separately_not_merged():
    out = shaping.sleep_detail(
        [night("2026-07-13", 6.75), nap("2026-07-13", 1.45), nap("2026-07-13", 0.24)]
    )
    row = out["daily"][0]
    assert row["total_h"] == 6.75, "ночь не должна раздуваться за счёт дрёмы"
    assert row["naps_h"] == 1.69, "дрёма считается отдельно"


def test_nap_hrv_does_not_pollute_night_stats():
    """Усреднять HRV ночи и дрёмы бессмысленно — в статистику идёт только ночь."""
    out = shaping.sleep_detail(
        [night("2026-07-13", 6.75, hrv=40), nap("2026-07-13", 1.45, hrv=12)]
    )
    assert out["stats"]["avg_hrv"]["mean"] == 40


def test_day_without_naps_has_no_naps_field():
    out = shaping.sleep_detail([night("2026-07-27", 6.2)])
    assert "naps_h" not in out["daily"][0]


def test_longest_wins_when_no_long_sleep_type():
    """Если long_sleep за сутки нет, берём самую длинную запись, а не первую."""
    out = shaping.sleep_detail(
        [nap("2026-07-19", 0.21, kind="sleep"), nap("2026-07-19", 6.18, kind="sleep")]
    )
    assert out["daily"][0]["total_h"] == 6.18


def test_one_row_per_day_across_period():
    rows = [
        night("2026-07-26", 6.0),
        night("2026-07-27", 7.0),
        nap("2026-07-27", 0.5),
        night("2026-07-28", 6.3),
    ]
    out = shaping.sleep_detail(rows)
    assert [d["day"] for d in out["daily"]] == ["2026-07-26", "2026-07-27", "2026-07-28"]
    assert out["period"]["days"] == 3


def test_days_are_sorted_even_if_response_is_not():
    out = shaping.sleep_detail(
        [night("2026-07-28", 6.3), night("2026-07-26", 6.0), night("2026-07-27", 7.0)]
    )
    assert [d["day"] for d in out["daily"]] == ["2026-07-26", "2026-07-27", "2026-07-28"]


def test_rows_without_day_are_dropped_not_crashed():
    out = shaping.sleep_detail([{"type": "long_sleep"}, night("2026-07-27", 6.2)])
    assert [d["day"] for d in out["daily"]] == ["2026-07-27"]


def test_empty_input_still_safe():
    assert shaping.sleep_detail([])["daily"] == []
