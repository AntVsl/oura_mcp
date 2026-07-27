"""Сжатие ответов Oura до того, что реально нужно модели.

Сырой daily_sleep за полгода — это сотни килобайт почти одинаковых объектов.
Здесь из них остаются посуточные значения и статистика по периоду; полный JSON
доступен через raw=True у каждого инструмента.

Все функции терпимы к отсутствующим полям: Oura добавляет и убирает ключи, и
падать из-за нового поля сервер не должен.
"""

from __future__ import annotations

from typing import Any

Row = dict[str, Any]


def _num(row: Row, *path: str) -> float | None:
    """Достаёт вложенное числовое значение, не падая на пропусках."""
    cur: Any = row
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return float(cur) if isinstance(cur, (int, float)) else None


def _hours(row: Row, key: str) -> float | None:
    sec = _num(row, key)
    return round(sec / 3600, 2) if sec is not None else None


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def describe(values: list[float]) -> dict[str, Any]:
    """Статистика по ряду: среднее, разброс, направление тренда."""
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0}
    n = len(clean)
    mean = sum(clean) / n
    out: dict[str, Any] = {
        "n": n,
        "mean": round(mean, 1),
        "min": round(min(clean), 1),
        "max": round(max(clean), 1),
    }
    if n >= 4:
        out["trend_per_week"] = round(_slope(clean) * 7, 2)
    return out


def _slope(values: list[float]) -> float:
    """Наклон методом наименьших квадратов, единиц в день."""
    n = len(values)
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den if den else 0.0


def _envelope(
    metric: str, daily: list[Row], series: dict[str, list[float]]
) -> dict[str, Any]:
    days = [d["day"] for d in daily if d.get("day")]
    return _compact(
        {
            "metric": metric,
            "period": {"start": days[0], "end": days[-1], "days": len(days)}
            if days
            else None,
            "stats": {k: describe(v) for k, v in series.items() if any(x is not None for x in v)}
            or None,
            "daily": daily,
        }
    )


# --- посуточные сводки -----------------------------------------------------


def daily_sleep(rows: list[Row]) -> dict[str, Any]:
    daily = [
        _compact({"day": r.get("day"), "score": _num(r, "score")}) for r in rows
    ]
    return _envelope("sleep_score", daily, {"score": [d.get("score") for d in daily]})


def daily_readiness(rows: list[Row]) -> dict[str, Any]:
    daily = [
        _compact(
            {
                "day": r.get("day"),
                "score": _num(r, "score"),
                "temp_deviation_c": _num(r, "temperature_deviation"),
                "hrv_balance": _num(r, "contributors", "hrv_balance"),
                "resting_hr_score": _num(r, "contributors", "resting_heart_rate"),
            }
        )
        for r in rows
    ]
    return _envelope(
        "readiness",
        daily,
        {
            "score": [d.get("score") for d in daily],
            "temp_deviation_c": [d.get("temp_deviation_c") for d in daily],
        },
    )


def daily_activity(rows: list[Row]) -> dict[str, Any]:
    daily = [
        _compact(
            {
                "day": r.get("day"),
                "score": _num(r, "score"),
                "steps": _num(r, "steps"),
                "active_kcal": _num(r, "active_calories"),
                "total_kcal": _num(r, "total_calories"),
            }
        )
        for r in rows
    ]
    return _envelope(
        "activity",
        daily,
        {
            "score": [d.get("score") for d in daily],
            "steps": [d.get("steps") for d in daily],
        },
    )


def sleep_detail(rows: list[Row]) -> dict[str, Any]:
    """Детальный сон: стадии, HRV, пульс, дыхание, температура."""
    daily = [
        _compact(
            {
                "day": r.get("day"),
                "total_h": _hours(r, "total_sleep_duration"),
                "deep_h": _hours(r, "deep_sleep_duration"),
                "rem_h": _hours(r, "rem_sleep_duration"),
                "light_h": _hours(r, "light_sleep_duration"),
                "awake_h": _hours(r, "awake_time"),
                "efficiency": _num(r, "efficiency"),
                "latency_min": (
                    round(_num(r, "latency") / 60, 1)
                    if _num(r, "latency") is not None
                    else None
                ),
                "avg_hrv": _num(r, "average_hrv"),
                "avg_hr": _num(r, "average_heart_rate"),
                "lowest_hr": _num(r, "lowest_heart_rate"),
                "avg_breath": _num(r, "average_breath"),
                "temp_deviation_c": _num(r, "readiness", "temperature_deviation"),
                "bedtime_start": r.get("bedtime_start"),
            }
        )
        for r in rows
    ]
    keys = ("total_h", "deep_h", "rem_h", "efficiency", "avg_hrv", "avg_hr",
            "lowest_hr", "avg_breath")
    return _envelope(
        "sleep_detail", daily, {k: [d.get(k) for d in daily] for k in keys}
    )


def daily_spo2(rows: list[Row]) -> dict[str, Any]:
    daily = [
        _compact(
            {
                "day": r.get("day"),
                "spo2_avg": _num(r, "spo2_percentage", "average"),
                "breathing_disturbance_index": _num(r, "breathing_disturbance_index"),
            }
        )
        for r in rows
    ]
    return _envelope(
        "spo2", daily, {"spo2_avg": [d.get("spo2_avg") for d in daily]}
    )


def daily_stress(rows: list[Row]) -> dict[str, Any]:
    daily = [
        _compact(
            {
                "day": r.get("day"),
                "stress_high_min": (
                    round(_num(r, "stress_high") / 60, 1)
                    if _num(r, "stress_high") is not None
                    else None
                ),
                "recovery_high_min": (
                    round(_num(r, "recovery_high") / 60, 1)
                    if _num(r, "recovery_high") is not None
                    else None
                ),
                "summary": r.get("day_summary"),
            }
        )
        for r in rows
    ]
    return _envelope(
        "stress",
        daily,
        {
            "stress_high_min": [d.get("stress_high_min") for d in daily],
            "recovery_high_min": [d.get("recovery_high_min") for d in daily],
        },
    )


def heart_health(rows: list[Row], metric: str) -> dict[str, Any]:
    """cardiovascular_age и vO2_max — поля различаются, берём что есть."""
    daily = []
    for r in rows:
        value = (
            _num(r, "vascular_age")
            or _num(r, "vo2_max")
            or _num(r, "vo2max")
        )
        daily.append(_compact({"day": r.get("day"), "value": value}))
    return _envelope(metric, daily, {"value": [d.get("value") for d in daily]})


def tags(rows: list[Row]) -> dict[str, Any]:
    items = [
        _compact(
            {
                "day": r.get("day") or (r.get("start_time") or "")[:10] or None,
                "type": r.get("tag_type_code") or r.get("tag_type"),
                "comment": r.get("comment"),
                "start_time": r.get("start_time"),
            }
        )
        for r in rows
    ]
    return {"metric": "tags", "count": len(items), "items": items}


def heartrate(rows: list[Row]) -> dict[str, Any]:
    """Поминутный пульс сворачивается посуточно.

    Сырой ряд — это тысячи точек за сутки; отдавать его целиком нельзя.
    """
    by_day: dict[str, list[float]] = {}
    sources: dict[str, set[str]] = {}
    for r in rows:
        ts = r.get("timestamp") or ""
        bpm = _num(r, "bpm")
        if not ts or bpm is None:
            continue
        day = ts[:10]
        by_day.setdefault(day, []).append(bpm)
        if r.get("source"):
            sources.setdefault(day, set()).add(str(r["source"]))

    daily = [
        _compact(
            {
                "day": day,
                "samples": len(vals),
                "mean_bpm": round(sum(vals) / len(vals), 1),
                "min_bpm": round(min(vals), 1),
                "max_bpm": round(max(vals), 1),
                "sources": sorted(sources[day]) if day in sources else None,
            }
        )
        for day, vals in sorted(by_day.items())
    ]
    return _envelope(
        "heartrate", daily, {"mean_bpm": [d.get("mean_bpm") for d in daily]}
    )


SHAPERS = {
    "daily_sleep": daily_sleep,
    "daily_readiness": daily_readiness,
    "daily_activity": daily_activity,
    "sleep": sleep_detail,
    "daily_spo2": daily_spo2,
    "daily_stress": daily_stress,
    "heartrate": heartrate,
    "enhanced_tag": tags,
    "tag": tags,
}
