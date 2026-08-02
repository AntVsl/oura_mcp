"""Ресурсы MCP должны быть зарегистрированы и читаемы через протокол."""

from zoneinfo import ZoneInfo
from pathlib import Path

import httpx
import respx

from my_oura_mcp.config import SANDBOX_BASE, Settings
from my_oura_mcp.server import build


def settings() -> Settings:
    return Settings(
        mode="sandbox",
        tz=ZoneInfo("UTC"),
        client_id=None,
        client_secret=None,
        redirect_uri="http://localhost:8765/callback",
        token_store=Path("/tmp/tokens.json"),
        cache_db=None,
    )


async def test_builtin_resources_are_advertised():
    resources = await build(settings()).list_resources()
    assert {str(resource.uri) for resource in resources} == {
        "oura://today",
        "oura://yesterday",
        "oura://week",
    }


@respx.mock
async def test_today_resource_returns_a_summary():
    for endpoint in ("daily_sleep", "daily_readiness", "daily_activity"):
        respx.get(f"{SANDBOX_BASE}/{endpoint}").mock(
            return_value=httpx.Response(200, json={"data": [{"day": "2026-08-02", "score": 80}]})
        )

    contents = await build(settings()).read_resource("oura://today")
    assert len(contents) == 1
    assert '"sleep"' in contents[0].content
