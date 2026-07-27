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
    **fastmcp_kwargs: object,
) -> FastMCP:
    settings = settings or load_settings()
    mcp = FastMCP("oura", **fastmcp_kwargs)
    tools.register(mcp, settings, token_provider)
    return mcp
