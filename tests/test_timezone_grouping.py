"""Суточная группировка пульса по местному поясу, а не по дате UTC.

Oura отдаёт timestamp в UTC. Группировка по первым 10 символам строки режет
местные сутки надвое: в поясе +03 точки с 00:00 до 03:00 уезжают в предыдущий
день — то есть ночной пульс, ради которого инструмент и нужен.
"""

from zoneinfo import ZoneInfo

from oura_mcp import shaping

MSK = ZoneInfo("Europe/Moscow")  # +03
LA = ZoneInfo("America/Los_Angeles")  # -07/-08

# Местные сутки 27 июля в MSK = UTC с 26T21:00 по 27T20:59.
NIGHT_MSK = [
    {"timestamp": "2026-07-26T21:30:00.000Z", "bpm": 50},  # 00:30 MSK 27-го
    {"timestamp": "2026-07-26T23:00:00.000Z", "bpm": 52},  # 02:00 MSK 27-го
    {"timestamp": "2026-07-27T09:00:00.000Z", "bpm": 70},  # 12:00 MSK 27-го
    {"timestamp": "2026-07-27T20:00:00.000Z", "bpm": 60},  # 23:00 MSK 27-го
]


def test_local_day_keeps_msk_night_with_its_own_date():
    out = shaping.heartrate(NIGHT_MSK, MSK)
    assert len(out["daily"]) == 1, "местные сутки не должны распадаться на два дня"
    row = out["daily"][0]
    assert row["day"] == "2026-07-27"
    assert row["samples"] == 4
    assert row["mean_bpm"] == 58.0


def test_night_low_points_are_not_lost_to_previous_day():
    """Самое низкое значение ночи должно остаться в своих сутках."""
    out = shaping.heartrate(NIGHT_MSK, MSK)
    assert out["daily"][0]["min_bpm"] == 50.0


def test_utc_grouping_would_split_the_day():
    """Фиксируем прежнее поведение как неверное: без пояса день распадается."""
    out = shaping.heartrate(NIGHT_MSK, None)
    assert len(out["daily"]) == 2, "без пояса дата берётся из строки UTC"


def test_negative_offset_shifts_the_other_way():
    """Обратный знак смещения: вечер по местному времени — уже завтра в UTC."""
    rows = [{"timestamp": "2026-07-28T04:00:00.000Z", "bpm": 55}]  # 21:00 LA 27-го
    assert shaping.heartrate(rows, LA)["daily"][0]["day"] == "2026-07-27"


def test_offset_suffix_instead_of_z_is_handled():
    rows = [{"timestamp": "2026-07-27T00:30:00.000+03:00", "bpm": 50}]
    assert shaping.heartrate(rows, MSK)["daily"][0]["day"] == "2026-07-27"


def test_naive_timestamp_does_not_crash():
    rows = [{"timestamp": "2026-07-27T00:30:00", "bpm": 50}]
    assert shaping.heartrate(rows, MSK)["daily"][0]["day"] == "2026-07-27"


def test_unparseable_timestamp_falls_back_instead_of_crashing():
    rows = [{"timestamp": "не дата", "bpm": 50}, {"timestamp": "2026-07-27T09:00:00Z", "bpm": 70}]
    out = shaping.heartrate(rows, MSK)
    assert any(d["day"] == "2026-07-27" for d in out["daily"])


def test_days_stay_sorted():
    rows = [
        {"timestamp": "2026-07-28T09:00:00.000Z", "bpm": 60},
        {"timestamp": "2026-07-26T21:30:00.000Z", "bpm": 50},
        {"timestamp": "2026-07-27T09:00:00.000Z", "bpm": 70},
    ]
    days = [d["day"] for d in shaping.heartrate(rows, MSK)["daily"]]
    assert days == sorted(days)
