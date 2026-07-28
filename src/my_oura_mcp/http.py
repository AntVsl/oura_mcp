"""HTTP-транспорт: ASGI-приложение с проверкой общего секрета.

MCP по streamable-http сам по себе никого не аутентифицирует. Без этого слоя
любой, кто знает URL, читает медицинские данные, поэтому проверка живёт здесь,
а не «когда-нибудь потом в reverse proxy».

Модель доступа простая и намеренно такая: один общий секрет, один владелец.
Многопользовательского разграничения здесь нет и не предполагается.
"""

from __future__ import annotations

import os
import secrets

from mcp.server.fastmcp import FastMCP

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
HEALTH_PATH = "/healthz"
MIN_TOKEN_LEN = 16


class EndpointAuthError(RuntimeError):
    """Конфигурация доступа небезопасна — запускаться нельзя."""


class BearerAuthMiddleware:
    """Пропускает дальше только запросы с верным `Authorization: Bearer <токен>`."""

    def __init__(self, app, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Health-check не требует секрета и не отдаёт никаких данных —
        # он нужен reverse proxy и оркестратору.
        if scope.get("path") == HEALTH_PATH:
            await _plain(send, 200, b"ok")
            return

        if not self._authorized(scope):
            await _plain(
                send,
                401,
                b"unauthorized",
                extra=[(b"www-authenticate", b'Bearer realm="my-oura-mcp"')],
            )
            return

        await self._app(scope, receive, send)

    def _authorized(self, scope) -> bool:
        for name, value in scope.get("headers") or []:
            if name.lower() != b"authorization":
                continue
            try:
                header = value.decode("latin-1")
            except UnicodeDecodeError:
                return False
            prefix, _, presented = header.partition(" ")
            if prefix.lower() != "bearer":
                return False
            # Сравнение постоянного времени: обычное == утекает длину общего префикса.
            return secrets.compare_digest(presented.strip(), self._token)
        return False


async def _plain(send, status: int, body: bytes, extra=()) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                *extra,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def resolve_token(host: str) -> str | None:
    """Секрет эндпоинта из окружения, с проверкой пригодности.

    На loopback без секрета работать можно — это локальная отладка. На любом
    другом адресе отсутствие секрета означает открытый доступ к медданным,
    и это повод не стартовать вовсе.
    """
    token = (os.getenv("OURA_MCP_TOKEN") or "").strip()
    exposed = host not in LOOPBACK

    if not token:
        if exposed:
            raise EndpointAuthError(
                f"Сервер слушает {host}, то есть доступен снаружи, но "
                "OURA_MCP_TOKEN не задан — эндпоинт отдавал бы медданные любому.\n"
                "Сгенерируй секрет и добавь его в .env:\n"
                "  python3 -c \"import secrets;print(secrets.token_urlsafe(32))\""
            )
        return None

    if len(token) < MIN_TOKEN_LEN:
        raise EndpointAuthError(
            f"OURA_MCP_TOKEN короче {MIN_TOKEN_LEN} символов — такой секрет "
            "перебирается. Сгенерируй новый: "
            "python3 -c \"import secrets;print(secrets.token_urlsafe(32))\""
        )
    return token


def build_app(mcp: FastMCP, host: str):
    """ASGI-приложение MCP, при наличии секрета — за проверкой Bearer."""
    app = mcp.streamable_http_app()
    token = resolve_token(host)
    return app if token is None else BearerAuthMiddleware(app, token)
