"""Сборка сервера: включается ли OAuth и с какими параметрами.

Эти проверки закрывают промежуток, который иначе виден только на живом
сервере. Ошибка здесь тихая: сервер поднимается, отвечает, отдаёт данные
Claude Code — и только claude.ai не подключается, причём с невнятной ошибкой
где-то на стороне Claude. Отлаживать это с телефона в руках очень невесело.

Особенно важен `resource_server_url`: он попадает в метаданные защищённого
ресурса и должен посимвольно совпадать с URL, который человек вводит в
claude.ai. Разойдётся на слэш — подключения не будет.
"""

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from my_oura_mcp.config import ConfigError, Settings, load_settings
from my_oura_mcp.server import build

SECRET = "o" * 32
PUBLIC = "https://oura-mcp.lol"


def settings(tmp_path: Path, public_url: str | None = PUBLIC) -> Settings:
    return Settings(
        mode="sandbox",
        tz=ZoneInfo("UTC"),
        client_id=None,
        client_secret=None,
        redirect_uri="http://localhost:8765/callback",
        token_store=tmp_path / "tokens.json",
        cache_db=tmp_path / "cache.db",
        public_url=public_url,
    )


# --- включение ---------------------------------------------------------------


def test_public_url_turns_oauth_on(tmp_path):
    mcp = build(settings(tmp_path), owner_secret=SECRET)
    assert mcp.settings.auth is not None


def test_without_public_url_there_is_no_oauth(tmp_path):
    """Локально OAuth не нужен: там stdio, и абсолютных URL взять негде."""
    mcp = build(settings(tmp_path, public_url=None), owner_secret=SECRET)
    assert mcp.settings.auth is None


def test_without_secret_there_is_no_oauth(tmp_path):
    """Секрет служит паролем на странице согласия — без него пускать некого.

    Сам факт молчаливого отключения компенсируется предупреждением в
    __main__.py: иначе человек задал публичный адрес и не понял, почему
    claude.ai не подключается.
    """
    mcp = build(settings(tmp_path), owner_secret=None)
    assert mcp.settings.auth is None


# --- параметры ---------------------------------------------------------------


def test_resource_url_points_at_the_mcp_endpoint(tmp_path):
    """Должен совпадать с тем, что вводится в claude.ai, посимвольно."""
    mcp = build(settings(tmp_path), owner_secret=SECRET)
    assert str(mcp.settings.auth.resource_server_url) == f"{PUBLIC}/mcp"


def test_issuer_is_the_public_url(tmp_path):
    mcp = build(settings(tmp_path), owner_secret=SECRET)
    assert str(mcp.settings.auth.issuer_url).rstrip("/") == PUBLIC


def test_dynamic_registration_is_enabled(tmp_path):
    """Без DCR подключиться нельзя: вписать client_id в диалоге негде."""
    mcp = build(settings(tmp_path), owner_secret=SECRET)
    assert mcp.settings.auth.client_registration_options.enabled is True


def test_no_required_scopes(tmp_path):
    """Непустой список сломал бы вход по общему секрету: у него скоупов нет."""
    mcp = build(settings(tmp_path), owner_secret=SECRET)
    assert not mcp.settings.auth.required_scopes


# --- маршруты ----------------------------------------------------------------


def test_consent_route_is_registered(tmp_path):
    """Провайдер уводит на этот путь — без маршрута флоу упёрся бы в 404."""
    mcp = build(settings(tmp_path), owner_secret=SECRET)
    assert "/oauth/consent" in {r.path for r in mcp._custom_starlette_routes}


def test_health_route_exists_in_both_modes(tmp_path):
    """На нём висит HEALTHCHECK из Dockerfile, а middleware при OAuth нет."""
    for public in (PUBLIC, None):
        mcp = build(settings(tmp_path, public_url=public), owner_secret=SECRET)
        paths = {r.path for r in mcp._custom_starlette_routes}
        assert "/healthz" in paths, f"public_url={public}"


# --- разбор настроек ---------------------------------------------------------


def test_http_public_url_is_refused(monkeypatch):
    """По этому адресу ходят коды авторизации и токены."""
    monkeypatch.setenv("OURA_PUBLIC_URL", "http://oura-mcp.lol")
    with pytest.raises(ConfigError, match="https"):
        load_settings()


def test_trailing_slash_is_trimmed(monkeypatch):
    """Иначе resource стал бы `https://host//mcp` и не совпал бы с введённым."""
    monkeypatch.setenv("OURA_PUBLIC_URL", f"{PUBLIC}/")
    assert load_settings().resource_url == f"{PUBLIC}/mcp"


def test_empty_public_url_means_disabled(monkeypatch):
    """Пустая переменная в .env — обычное дело, и она не должна включать OAuth."""
    monkeypatch.setenv("OURA_PUBLIC_URL", "   ")
    assert load_settings().oauth_enabled is False


def test_claude_and_chatgpt_are_default_allowed_oauth_origins(monkeypatch):
    monkeypatch.delenv("OURA_OAUTH_ALLOWED_REDIRECT_ORIGINS", raising=False)
    assert load_settings().oauth_allowed_redirect_origins == frozenset(
        {"https://claude.ai", "https://chatgpt.com"}
    )


def test_oauth_origin_must_not_contain_a_path(monkeypatch):
    monkeypatch.setenv("OURA_OAUTH_ALLOWED_REDIRECT_ORIGINS", "https://claude.ai/callback")
    with pytest.raises(ConfigError, match="origins"):
        load_settings()
