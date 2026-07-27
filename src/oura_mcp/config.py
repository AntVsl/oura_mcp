"""Конфигурация из окружения.

Единственное место, где читается .env. Всё остальное получает готовый
Settings — так тесты не зависят от переменных окружения.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

SANDBOX_BASE = "https://api.ouraring.com/v2/sandbox/usercollection"
PRODUCTION_BASE = "https://api.ouraring.com/v2/usercollection"

AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"

# Скоупы, включённые в приложении. Порядок неважен, разделитель — пробел.
SCOPES = ("daily", "heartrate", "tag", "spo2", "stress", "heart_health")


class ConfigError(RuntimeError):
    """Конфигурация непригодна для запуска. Сообщение адресовано человеку."""


@dataclass(frozen=True)
class Settings:
    mode: str
    tz: ZoneInfo
    client_id: str | None
    client_secret: str | None
    redirect_uri: str
    token_store: Path
    cache_db: Path

    @property
    def is_sandbox(self) -> bool:
        return self.mode == "sandbox"

    @property
    def base_url(self) -> str:
        return SANDBOX_BASE if self.is_sandbox else PRODUCTION_BASE

    def require_oauth(self) -> tuple[str, str]:
        """client_id/secret для OAuth. Падает внятно, если их нет."""
        if not self.client_id or not self.client_secret:
            raise ConfigError(
                "Для режима production нужны OURA_CLIENT_ID и OURA_CLIENT_SECRET "
                "в .env — возьми их на https://developer.ouraring.com/applications"
            )
        return self.client_id, self.client_secret


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_settings(env_file: Path | None = None) -> Settings:
    root = project_root()
    load_dotenv(env_file or root / ".env")

    mode = os.getenv("OURA_API_MODE", "sandbox").strip().lower()
    if mode not in ("sandbox", "production"):
        raise ConfigError(
            f"OURA_API_MODE={mode!r} — допустимы только 'sandbox' или 'production'"
        )

    tz_name = os.getenv("OURA_TZ", "UTC").strip()
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:  # noqa: BLE001 — хотим человекочитаемое сообщение
        raise ConfigError(
            f"OURA_TZ={tz_name!r} — не распознан как часовой пояс "
            f"(ожидается вид 'Europe/Moscow'): {exc}"
        ) from exc

    def _path(key: str, default: str) -> Path:
        raw = Path(os.getenv(key, default))
        return raw if raw.is_absolute() else root / raw

    return Settings(
        mode=mode,
        tz=tz,
        client_id=(os.getenv("OURA_CLIENT_ID") or "").strip() or None,
        client_secret=(os.getenv("OURA_CLIENT_SECRET") or "").strip() or None,
        redirect_uri=os.getenv(
            "OURA_REDIRECT_URI", "http://localhost:8765/callback"
        ).strip(),
        token_store=_path("OURA_TOKEN_STORE", ".oura/tokens.json"),
        cache_db=_path("OURA_CACHE_DB", ".oura/cache.db"),
    )
