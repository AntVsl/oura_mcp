"""Кэш завершённых суток в SQLite.

Старые дни Oura меняются редко, но поздняя синхронизация способна обновить
недавнюю дату. Поэтому клиент повторно проверяет два последних завершённых дня,
а всё более старое хранит в базе. Выигрыш заметен на длинных периодах — полгода
трендов перестают быть сотнями обращений к API — и данные остаются доступны при
обрыве сети.

Четыре решения, каждое из которых легко сделать тихо-неправильно.

**Сегодняшний день не кэшируется никогда.** Данные за текущие сутки Oura ещё
дописывает, и заморозить их значит показывать вчерашнюю правду как сегодняшнюю.
Граница «сегодня» берётся в настроенном поясе, а не в системном, — ровно та
ошибка, что уже стоила нам одного из пяти багов.

**Пустые дни не кэшируются.** Соблазн есть: раз за день записей нет, запомним
это и не будем спрашивать снова. Но пустота бывает двух родов — кольцо не
носили и кольцо ещё не синхронизировалось. Второе лечится само через несколько
часов, а закэшированная пустота осталась бы навсегда. Цена отказа: дни без
данных перезапрашиваются каждый раз. Это дешевле молчаливо неверного ответа.

**Режим входит в ключ.** Иначе синтетика из песочницы всплывёт в production под
видом настоящих данных.

**Сбой кэша не должен ломать доступ к данным.** База — ускорение, а не источник
правды: любая ошибка SQLite означает «считаем, что кэша нет», и запрос уходит в
сеть как обычно.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

Row = dict[str, Any]

SCHEMA = """
CREATE TABLE IF NOT EXISTS days (
    mode      TEXT    NOT NULL,
    endpoint  TEXT    NOT NULL,
    day       TEXT    NOT NULL,
    payload   TEXT    NOT NULL,
    cached_at INTEGER NOT NULL,
    PRIMARY KEY (mode, endpoint, day)
)
"""


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


class DayCache:
    """Записи по суткам. Все ошибки проглатываются: кэш не источник правды."""

    def __init__(self, path: Path, mode: str) -> None:
        self.path = path
        self.mode = mode
        self._broken = False
        self._prepare()

    # --- служебное ----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection | None:
        if self._broken:
            return None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Здесь лежат медицинские показатели. Права SQLite по umask обычно
            # 0644, поэтому права файла задаём явно. Каталог намеренно не
            # меняем: пользователь мог указать существующий общий путь вроде
            # /tmp, и chmod родителя там был бы опасным побочным эффектом.
            conn = sqlite3.connect(self.path, timeout=5.0)
            os.chmod(self.path, 0o600)
            conn.row_factory = sqlite3.Row
            return conn
        except (sqlite3.Error, OSError):
            # Один раз не получилось — больше не пытаемся до перезапуска, чтобы
            # не платить неудачным открытием файла на каждом запросе.
            self._broken = True
            return None

    def _prepare(self) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            with conn:
                conn.execute(SCHEMA)
        except sqlite3.Error:
            self._broken = True
        finally:
            conn.close()

    # --- чтение и запись ----------------------------------------------------

    def lookup(self, endpoint: str, start: date, end: date) -> dict[str, list[Row]]:
        """Закэшированные сутки диапазона: {день: записи}. Отсутствующих нет."""
        conn = self._connect()
        if conn is None:
            return {}
        try:
            rows = conn.execute(
                "SELECT day, payload FROM days "
                "WHERE mode = ? AND endpoint = ? AND day BETWEEN ? AND ?",
                (self.mode, endpoint, start.isoformat(), end.isoformat()),
            ).fetchall()
            return {r["day"]: json.loads(r["payload"]) for r in rows}
        except (sqlite3.Error, ValueError):
            return {}
        finally:
            conn.close()

    def store(self, endpoint: str, rows: list[Row], today: date) -> int:
        """Раскладывает записи по суткам и сохраняет завершённые.

        Возвращает число сохранённых дней — для тестов и диагностики.
        """
        by_day: dict[str, list[Row]] = {}
        for row in rows:
            day = row.get("day")
            # Записи без day (например, поминутный пульс) сюда не попадают:
            # раскладывать их по суткам — отдельная задача с отдельными
            # ловушками, и делать её мимоходом не стоит.
            if isinstance(day, str) and day < today.isoformat():
                by_day.setdefault(day, []).append(row)

        if not by_day:
            return 0

        conn = self._connect()
        if conn is None:
            return 0
        try:
            import time

            now = int(time.time())
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO days (mode, endpoint, day, payload, cached_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (self.mode, endpoint, day, json.dumps(items), now)
                        for day, items in by_day.items()
                    ],
                )
            return len(by_day)
        except (sqlite3.Error, ValueError):
            return 0
        finally:
            conn.close()

    def replace_range(
        self, endpoint: str, rows: list[Row], start: date, end: date, today: date
    ) -> int:
        """Заменяет закэшированные дни успешным свежим ответом.

        В отличие от ``store`` удаляет и день, для которого API теперь отдал
        пустоту: иначе старый частичный ответ переживал бы повторную проверку.
        """
        by_day: dict[str, list[Row]] = {}
        for row in rows:
            day = row.get("day")
            if isinstance(day, str) and day < today.isoformat():
                by_day.setdefault(day, []).append(row)

        conn = self._connect()
        if conn is None:
            return 0
        try:
            import time

            with conn:
                conn.execute(
                    "DELETE FROM days WHERE mode = ? AND endpoint = ? "
                    "AND day BETWEEN ? AND ? AND day < ?",
                    (self.mode, endpoint, start.isoformat(), end.isoformat(), today.isoformat()),
                )
                now = int(time.time())
                conn.executemany(
                    "INSERT INTO days (mode, endpoint, day, payload, cached_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (self.mode, endpoint, day, json.dumps(items), now)
                        for day, items in by_day.items()
                    ],
                )
            return len(by_day)
        except (sqlite3.Error, ValueError):
            return 0
        finally:
            conn.close()

    # --- обслуживание -------------------------------------------------------

    def clear(self, endpoint: str | None = None) -> int:
        """Забыть всё или один эндпоинт. Возвращает число удалённых дней."""
        conn = self._connect()
        if conn is None:
            return 0
        try:
            with conn:
                if endpoint:
                    cur = conn.execute(
                        "DELETE FROM days WHERE mode = ? AND endpoint = ?",
                        (self.mode, endpoint),
                    )
                else:
                    cur = conn.execute("DELETE FROM days WHERE mode = ?", (self.mode,))
                return cur.rowcount
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def stats(self) -> dict[str, Any]:
        conn = self._connect()
        if conn is None:
            return {"available": False, "path": str(self.path)}
        try:
            total = conn.execute(
                "SELECT COUNT(*) AS n, MIN(day) AS lo, MAX(day) AS hi "
                "FROM days WHERE mode = ?",
                (self.mode,),
            ).fetchone()
            per_endpoint = conn.execute(
                "SELECT endpoint, COUNT(*) AS n FROM days WHERE mode = ? "
                "GROUP BY endpoint ORDER BY n DESC",
                (self.mode,),
            ).fetchall()
            return {
                "available": True,
                "path": str(self.path),
                "mode": self.mode,
                "days": total["n"],
                "range": [total["lo"], total["hi"]] if total["n"] else None,
                "by_endpoint": {r["endpoint"]: r["n"] for r in per_endpoint},
            }
        except sqlite3.Error:
            return {"available": False, "path": str(self.path)}
        finally:
            conn.close()
