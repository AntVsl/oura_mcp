import pytest

from my_oura_mcp.http import HEALTH_PATH, BearerAuthMiddleware, EndpointAuthError, resolve_token

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
    """Без токена health обязан пройти: на нём висит HEALTHCHECK из Dockerfile.

    Middleware его пропускает, а отвечает маршрут в server.py — ответ живёт в
    одном месте, потому что при включённом OAuth этого middleware в стеке нет
    вовсе, а health-check нужен в обоих режимах.
    """
    status, reached = await call([], path=HEALTH_PATH)
    assert status == 200
    assert reached, "health должен доходить до приложения, где его обслуживает маршрут"


async def test_non_http_scope_passes_through():
    """Lifespan-события ASGI не должны упираться в проверку заголовка."""
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    await BearerAuthMiddleware(app, TOKEN)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]


# --- вычистка секретов из журнала доступа -----------------------------------
#
# uvicorn пишет query-строку целиком. В логах живого сервера я своими глазами
# видел `GET /oauth/consent?request=…` — это одноразовый секрет заявки. Доступа
# сам по себе он не даёт (нужен ещё OURA_MCP_TOKEN), но логи читают через
# docker logs и копируют в переписку.

import logging

from my_oura_mcp.http import RedactAccessLog, redact_query


def test_request_id_is_redacted():
    assert redact_query("/oauth/consent?request=abc123") == "/oauth/consent?request=…"


def test_authorization_code_is_redacted():
    assert "code=…" in redact_query("/cb?code=secret&state=s")


def test_keys_survive_only_values_go():
    """По ключам видно, что за запрос был, — это нужно при разборе."""
    out = redact_query("/authorize?response_type=code&client_id=x&state=sec")
    assert "response_type=code" in out, "не-секретные параметры трогать не надо"
    assert "client_id=x" in out
    assert "state=…" in out
    assert "sec" not in out


def test_paths_without_query_are_untouched():
    assert redact_query("/healthz") == "/healthz"
    assert redact_query("/mcp") == "/mcp"


def test_filter_rewrites_the_uvicorn_record():
    """Путь лежит третьим аргументом — на этом держится вся вычистка."""
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', None, None
    )
    record.args = ("1.2.3.4:5", "GET", "/oauth/consent?request=leak", "1.1", 200)
    assert RedactAccessLog().filter(record) is True
    assert record.args[2] == "/oauth/consent?request=…"
    assert "leak" not in str(record.args)


def test_filter_survives_unexpected_args():
    """Формат uvicorn может поменяться — падать из-за логгера непозволительно."""
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s", None, None)
    record.args = ("just one",)
    assert RedactAccessLog().filter(record) is True
