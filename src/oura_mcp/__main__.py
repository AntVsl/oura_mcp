"""Точка входа.

    oura-mcp                      # stdio, для Claude Code
    oura-mcp --transport http     # streamable-http, для удалённого доступа
"""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_settings
from .http import AuthError, HEALTH_PATH, build_app, resolve_token
from .server import build


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oura-mcp", description=__doc__)
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

    try:
        settings = load_settings()
    except ConfigError as exc:
        # stdout занят протоколом MCP — вся диагностика уходит в stderr.
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    if args.transport == "stdio":
        _banner(settings, "stdio")
        build(settings).run(transport="stdio")
        return 0

    try:
        token = resolve_token(args.host)
    except AuthError as exc:
        print(f"Отказ в запуске: {exc}", file=sys.stderr)
        return 3

    mcp = build(settings, host=args.host, port=args.port)
    app = build_app(mcp, args.host)

    _banner(settings, f"http://{args.host}:{args.port}/mcp")
    print(
        f"  авторизация: {'Bearer-токен' if token else 'НЕТ (только loopback)'}\n"
        f"  health-check: {HEALTH_PATH}",
        file=sys.stderr,
    )

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _banner(settings, where: str) -> None:
    print(
        f"oura-mcp: режим {settings.mode}, часовой пояс {settings.tz}, {where}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
