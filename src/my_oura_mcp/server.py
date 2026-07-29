"""Сборка MCP-сервера.

Один и тот же объект обслуживает оба транспорта: stdio для Claude Code
локально и streamable-http для развёртывания на VPS. Разница только в
аргументе run() — см. __main__.py.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import tools
from .client import TokenProvider
from .config import Settings, load_settings


def build(
    settings: Settings | None = None,
    token_provider: TokenProvider | None = None,
    owner_secret: str | None = None,
    **fastmcp_kwargs: object,
) -> FastMCP:
    """Собирает сервер; при заданном OURA_PUBLIC_URL включает OAuth.

    OAuth поднимается только вместе с публичным адресом, потому что его
    метаданные содержат абсолютные URL: локально их взять неоткуда, да и
    незачем — там работает stdio.
    """
    settings = settings or load_settings()

    if settings.oauth_enabled and owner_secret:
        # Импорт внутри функции: без публичного адреса этот код не нужен, а
        # starlette-зависимости тянуть на stdio-запуске ни к чему.
        from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

        from .oauth_server import OAuthStore, OuraAuthProvider, register_consent_route

        provider = OuraAuthProvider(
            OAuthStore(settings.token_store.with_name("oauth.json")),
            owner_secret,
        )
        fastmcp_kwargs["auth_server_provider"] = provider
        fastmcp_kwargs["auth"] = AuthSettings(
            issuer_url=settings.public_url,
            resource_server_url=settings.resource_url,
            # DCR обязателен: Claude регистрируется сам, вписать client_id
            # вручную в диалоге коннектора негде.
            client_registration_options=ClientRegistrationOptions(enabled=True),
            # Скоупов нет намеренно. Владелец один, разграничивать нечего, а
            # непустой required_scopes сломал бы вход по общему секрету:
            # у статического токена скоупов нет.
            required_scopes=None,
        )
        mcp = FastMCP("oura", **fastmcp_kwargs)
        register_consent_route(mcp, provider)
    else:
        mcp = FastMCP("oura", **fastmcp_kwargs)

    _register_health(mcp)
    tools.register(mcp, settings, token_provider)
    return mcp


def _register_health(mcp: FastMCP) -> None:
    """Health-check маршрутом, а не только в middleware.

    Раньше он жил в BearerAuthMiddleware, но при включённом OAuth то
    middleware не подключается — и HEALTHCHECK из Dockerfile начал бы падать,
    молча уронив контейнер в unhealthy. Маршрут работает в обоих режимах.
    """
    from starlette.responses import PlainTextResponse

    from .http import HEALTH_PATH

    @mcp.custom_route(HEALTH_PATH, methods=["GET"])
    async def healthz(_request):  # noqa: ANN001 — сигнатура задана starlette
        return PlainTextResponse("ok")

