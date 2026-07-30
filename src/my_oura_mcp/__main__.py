"""Точка входа.

    my-oura-mcp                  # stdio, для Claude Code
    my-oura-mcp --transport http # streamable-http, для удалённого доступа
    my-oura-mcp auth                 # разовая авторизация в Oura
    my-oura-mcp auth --status        # состояние токенов
    my-oura-mcp auth --logout        # забыть токены
    my-oura-mcp install              # готовые настройки для клиентов
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .auth import AuthError, OuraOAuth, TokenStore
from .config import ConfigError, Settings, load_settings
from .http import (
    HEALTH_PATH,
    EndpointAuthError,
    build_app,
    install_access_log_redaction,
    resolve_token,
)
from .server import build


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        # stdout занят протоколом MCP — вся диагностика уходит в stderr.
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    if argv and argv[0] == "auth":
        return _auth(settings, argv[1:])
    if argv and argv[0] == "install":
        from . import install
        from .config import project_root

        return install.run(settings, project_root())
    return _serve(settings, argv)


# --- авторизация ------------------------------------------------------------


def _auth(settings: Settings, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="my-oura-mcp auth")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="показать состояние токенов")
    group.add_argument("--logout", action="store_true", help="удалить сохранённые токены")
    args = parser.parse_args(argv)

    oauth = OuraOAuth(settings, TokenStore(settings.token_store))

    if args.status:
        for key, value in oauth.status().items():
            print(f"  {key}: {value}")
        return 0

    if args.logout:
        oauth.logout()
        print(f"Токены удалены: {settings.token_store}")
        return 0

    from . import authflow

    try:
        print(asyncio.run(authflow.run(settings)))
    except (AuthError, ConfigError) as exc:
        print(f"Авторизация не удалась: {exc}", file=sys.stderr)
        return 1
    return 0


# --- обслуживание -----------------------------------------------------------


def _serve(settings: Settings, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="my-oura-mcp", description=__doc__)
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio — локально в Claude Code; http — удалённый доступ",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="только для --transport http; 0.0.0.0 внутри контейнера",
    )
    parser.add_argument("--port", type=int, default=8000, help="только для --transport http")
    args = parser.parse_args(argv)

    # В production каждый запрос идёт с токеном, который сам обновляется по
    # истечении; в sandbox авторизация не нужна вовсе.
    provider = None
    if not settings.is_sandbox:
        try:
            settings.require_oauth()
        except ConfigError as exc:
            print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
            return 2
        provider = OuraOAuth(settings, TokenStore(settings.token_store)).access_token
        if not settings.token_store.exists():
            print(
                f"Внимание: режим production, но токенов в {settings.token_store} нет. "
                "Запросы будут отклоняться, пока не пройдёшь `my-oura-mcp auth`.",
                file=sys.stderr,
            )

    if args.transport == "stdio":
        _banner(settings, "stdio")
        build(settings, provider).run(transport="stdio")
        return 0

    try:
        endpoint_token = resolve_token(args.host)
    except EndpointAuthError as exc:
        print(f"Отказ в запуске: {exc}", file=sys.stderr)
        return 3

    oauth = settings.oauth_enabled and endpoint_token is not None
    if settings.oauth_enabled and not oauth:
        # Молчать здесь нельзя: человек задал публичный адрес, то есть просил
        # OAuth, а получил бы сервер без него — и узнал бы об этом только когда
        # claude.ai откажется подключаться. Секрет нужен потому, что он же
        # служит паролем на странице согласия.
        print(
            f"Внимание: OURA_PUBLIC_URL={settings.public_url} задан, но OAuth не "
            "включён — нет OURA_MCP_TOKEN, а он служит паролем на странице "
            "согласия. Сервер поднимется без OAuth, и claude.ai к нему не "
            "подключится.",
            file=sys.stderr,
        )
    mcp = build(
        settings,
        provider,
        owner_secret=endpoint_token,
        host=args.host,
        port=args.port,
    )
    # При включённом OAuth собственную проверку заголовка не вешаем: она стоит
    # снаружи и не пропустила бы ни /authorize, ни /token, ни страницу
    # согласия — то есть сломала бы ровно тот флоу, ради которого всё это.
    # Общий секрет при этом продолжает работать: провайдер принимает его как
    # бессрочный access-токен, и Claude Code ходит как раньше.
    if oauth:
        from .oauth_server import AdvertisePublicClients

        app = AdvertisePublicClients(mcp.streamable_http_app())
    else:
        app = build_app(mcp, args.host)

    _banner(settings, f"http://{args.host}:{args.port}/mcp")
    if oauth:
        auth_line = f"OAuth 2.1 + общий секрет, issuer {settings.public_url}"
    elif endpoint_token:
        auth_line = "Bearer-токен"
    else:
        auth_line = "НЕТ (только loopback)"
    print(
        f"  авторизация эндпоинта: {auth_line}\n"
        f"  health-check: {HEALTH_PATH}",
        file=sys.stderr,
    )

    import uvicorn

    # До запуска: uvicorn пишет query-строки целиком, а там ездят
    # одноразовый идентификатор заявки и authorization code.
    install_access_log_redaction()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _banner(settings: Settings, where: str) -> None:
    print(
        f"my-oura-mcp: режим {settings.mode}, часовой пояс {settings.tz}, {where}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
