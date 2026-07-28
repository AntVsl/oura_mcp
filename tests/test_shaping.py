import json

from my_oura_mcp import shaping

# Формы взяты из живых ответов песочницы Oura.
DAILY_SLEEP = [
    {"id": "a", "day": "2026-07-20", "score": 73, "contributors": {"deep_sleep": 90}},
    {"id": "b", "day": "2026-07-21", "score": 80, "contributors": {"deep_sleep": 70}},
    {"id": "c", "day": "2026-07-22", "score": 77, "contributors": {"deep_sleep": 80}},
    {"id": "d", "day": "2026-07-23", "score": 85, "contributors": {"deep_sleep": 85}},
]

SLEEP_DETAIL = [
    {
        "day": "2026-07-25",
        "total_sleep_duration": 25200,
        "deep_sleep_duration": 3600,
        "rem_sleep_duration": 5400,
        "efficiency": 80,
        "latency": 900,
        "average_hrv": 70,
        "average_heart_rate": 60.0,
        "lowest_heart_rate": 54,
        "average_breath": 10.0,
        "readiness": {"temperature_deviation": -0.2},
    }
]


def test_daily_sleep_keeps_days_and_scores():
    out = shaping.daily_sleep(DAILY_SLEEP)
    assert out["period"] == {"start": "2026-07-20", "end": "2026-07-23", "days": 4}
    assert [d["score"] for d in out["daily"]] == [73, 80, 77, 85]


def test_stats_include_trend_when_enough_points():
    out = shaping.daily_sleep(DAILY_SLEEP)
    stats = out["stats"]["score"]
    assert stats["n"] == 4
    assert stats["min"] == 73 and stats["max"] == 85
    assert stats["trend_per_week"] > 0, "ряд растёт — тренд должен быть положительным"


def test_trend_omitted_for_short_series():
    assert "trend_per_week" not in shaping.daily_sleep(DAILY_SLEEP[:2])["stats"]["score"]


def test_seconds_converted_to_hours():
    daily = shaping.sleep_detail(SLEEP_DETAIL)["daily"][0]
    assert daily["total_h"] == 7.0
    assert daily["deep_h"] == 1.0
    assert daily["latency_min"] == 15.0


def test_nested_temperature_is_extracted():
    assert shaping.sleep_detail(SLEEP_DETAIL)["daily"][0]["temp_deviation_c"] == -0.2


def test_missing_fields_do_not_crash():
    """Oura добавляет и убирает ключи — сервер не должен падать из-за этого."""
    out = shaping.sleep_detail([{"day": "2026-07-25"}])
    assert out["daily"][0] == {"day": "2026-07-25"}


def test_empty_input_is_safe():
    for fn in (shaping.daily_sleep, shaping.sleep_detail, shaping.heartrate):
        assert fn([])["daily"] == []


def test_heartrate_collapses_series_by_day():
    rows = [
        {"timestamp": "2026-07-25T01:00:00+00:00", "bpm": 50, "source": "sleep"},
        {"timestamp": "2026-07-25T02:00:00+00:00", "bpm": 60, "source": "sleep"},
        {"timestamp": "2026-07-26T01:00:00+00:00", "bpm": 70, "source": "awake"},
    ]
    out = shaping.heartrate(rows)
    assert len(out["daily"]) == 2
    first = out["daily"][0]
    assert first["samples"] == 2 and first["mean_bpm"] == 55.0
    assert first["min_bpm"] == 50.0 and first["max_bpm"] == 60.0


def test_shaping_actually_shrinks_large_payloads():
    """Смысл модуля: длинные ряды должны заметно ужиматься."""
    rows = [
        {"timestamp": f"2026-07-25T{h:02d}:{m:02d}:00+00:00", "bpm": 60 + (h + m) % 20}
        for h in range(24)
        for m in range(0, 60, 5)
    ]
    raw = len(json.dumps(rows))
    shaped = len(json.dumps(shaping.heartrate(rows)))
    assert shaped * 10 < raw, f"сжатие всего {raw / shaped:.1f}x"
