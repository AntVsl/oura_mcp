"""Конфигурация из окружения.

Единственное место, где читается .env. Всё остальное получает готовый
Settings — так тесты не зависят от переменных окружения.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
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
    cache_db: Path | None
    # Публичный адрес сервера, если он развёрнут наружу: включает OAuth для
    # claude.ai. Без него сервер работает как раньше — на общем секрете.
    public_url: str | None = None
    # Разрешённые origins OAuth-клиентов для динамической регистрации.
    oauth_allowed_redirect_origins: frozenset[str] = frozenset(
        {"https://claude.ai", "https://chatgpt.com"}
    )

    @property
    def oauth_enabled(self) -> bool:
        return self.public_url is not None

    @property
    def resource_url(self) -> str:
        """URL самого MCP-эндпоинта.

        Он же идентификатор ресурса в метаданных OAuth, и совпадать с тем, что
        человек вводит в claude.ai, должен посимвольно — включая путь. Отсюда
        и требование к OURA_PUBLIC_URL быть без хвостового слэша.
        """
        return f"{self.public_url}/mcp"

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


def project_root() -> Path | None:
    """Корень checkout'а, если пакет запущен именно из него.

    В установленном wheel ``__file__`` живёт в site-packages: подниматься от
    него на два уровня и записывать туда .env/токены нельзя.
    """
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / "pyproject.toml").is_file() else None


def load_settings(env_file: Path | None = None) -> Settings:
    # Конфигурация принадлежит месту запуска, а не месту установки пакета.
    # В checkout это по-прежнему корень проекта, потому что команды из README
    # запускаются после ``cd oura_mcp``.
    root = (env_file.parent if env_file is not None else Path.cwd()).resolve()
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

    public_url = (os.getenv("OURA_PUBLIC_URL") or "").strip().rstrip("/") or None
    if public_url and not public_url.startswith("https://"):
        # Не придирка: по этому адресу ходят коды авторизации и токены, а
        # claude.ai к http-эндпоинту всё равно не подключится.
        raise ConfigError(
            f"OURA_PUBLIC_URL={public_url!r} — нужен https. "
            "Через http OAuth-коды и токены пошли бы открытым текстом."
        )

    allowed_origins = frozenset(
        part.strip().rstrip("/")
        for part in (
            os.getenv("OURA_OAUTH_ALLOWED_REDIRECT_ORIGINS")
            or "https://claude.ai,https://chatgpt.com"
        ).split(",")
        if part.strip()
    )

    def _is_https_origin(origin: str) -> bool:
        parsed = urlsplit(origin)
        return (
            parsed.scheme == "https"
            and bool(parsed.netloc)
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )

    if not all(_is_https_origin(origin) for origin in allowed_origins):
        raise ConfigError(
            "OURA_OAUTH_ALLOWED_REDIRECT_ORIGINS должен содержать origins через запятую, "
            "например https://claude.ai,https://chatgpt.com"
        )

    return Settings(
        mode=mode,
        tz=tz,
        client_id=(os.getenv("OURA_CLIENT_ID") or "").strip() or None,
        client_secret=(os.getenv("OURA_CLIENT_SECRET") or "").strip() or None,
        redirect_uri=os.getenv(
            "OURA_REDIRECT_URI", "http://localhost:8765/callback"
        ).strip(),
        token_store=_path("OURA_TOKEN_STORE", ".oura/tokens.json"),
        # Пустое значение — осознанный отказ от кэша, а не «не задано».
        cache_db=None
        if os.getenv("OURA_CACHE_DB", "").strip() == "" and "OURA_CACHE_DB" in os.environ
        else _path("OURA_CACHE_DB", ".oura/cache.db"),
        public_url=public_url,
        oauth_allowed_redirect_origins=allowed_origins,
    )
