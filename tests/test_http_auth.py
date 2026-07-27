import pytest

from oura_mcp.http import HEALTH_PATH, BearerAuthMiddleware, EndpointAuthError, resolve_token

TOKEN = "s" * 32


# --- политика запуска -------------------------------------------------------


def test_public_bind_without_token_refuses_to_start(monkeypatch):
    """Ключевая гарантия: наружу без секрета сервер не поднимется."""
    monkeypatch.delenv("OURA_MCP_TOKEN", raising=False)
    with pytest.raises(EndpointAuthError, match="доступен снаружи"):
        resolve_token("0.0.0.0")


def test_loopback_without_token_is_allowed(monkeypatch):
    monkeypatch.delenv("OURA_MCP_TOKEN", raising=False)
    assert resolve_token("127.0.0.1") is None


def test_short_token_rejected(monkeypatch):
    monkeypatch.setenv("OURA_MCP_TOKEN", "korotkiy")
    with pytest.raises(EndpointAuthError, match="короче"):
        resolve_token("0.0.0.0")


def test_valid_token_accepted(monkeypatch):
    monkeypatch.setenv("OURA_MCP_TOKEN", TOKEN)
    assert resolve_token("0.0.0.0") == TOKEN


# --- поведение middleware ---------------------------------------------------


async def call(headers: list[tuple[bytes, bytes]], path: str = "/mcp"):
    """Прогоняет запрос через middleware, возвращает (статус, дошло_ли_до_app)."""
    reached = False

    async def app(scope, receive, send):
        nonlocal reached
        reached = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    captured: dict = {}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    mw = BearerAuthMiddleware(app, TOKEN)
    await mw({"type": "http", "path": path, "headers": headers}, receive, send)
    return captured.get("status"), reached


async def test_correct_token_passes_through():
    status, reached = await call([(b"authorization", f"Bearer {TOKEN}".encode())])
    assert status == 200 and reached


async def test_missing_header_is_rejected():
    status, reached = await call([])
    assert status == 401 and not reached


async def test_wrong_token_is_rejected():
    status, reached = await call([(b"authorization", b"Bearer nepravilnyy_token_dlinnyy")])
    assert status == 401 and not reached


async def test_token_without_bearer_scheme_is_rejected():
    """Claude отправляет значение как есть — без схемы это не наш случай."""
    status, reached = await call([(b"authorization", TOKEN.encode())])
    assert status == 401 and not reached


async def test_health_check_needs_no_token():
    status, reached = await call([], path=HEALTH_PATH)
    assert status == 200 and not reached, "health не должен доходить до MCP"


async def test_non_http_scope_passes_through():
    """Lifespan-события ASGI не должны упираться в проверку заголовка."""
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    await BearerAuthMiddleware(app, TOKEN)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]
