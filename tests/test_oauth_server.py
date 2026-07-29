"""Сервер авторизации: политика доступа и хранение.

Проверяется то, что мы решали сами. Протокольный слой — PKCE, срок жизни кода,
сверка redirect_uri — реализован в SDK и тестируется там; дублировать его
проверки здесь значило бы тестировать чужую библиотеку.

Главное, за чем следят эти тесты: авторизация ломается молча. Лишний пущенный
и не пущенный владелец выглядят в логах одинаково спокойно, поэтому каждая
граница проверяется с обеих сторон — и что пускает, и что не пускает.
"""

import json
import time

import pytest
from pydantic import AnyUrl

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from my_oura_mcp.oauth_server import AdvertisePublicClients, OAuthStore, OuraAuthProvider

SECRET = "o" * 32
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "oauth.json"


@pytest.fixture
def provider(store_path):
    return OuraAuthProvider(OAuthStore(store_path), SECRET)


@pytest.fixture
def client():
    return OAuthClientInformationFull(
        client_id="cid",
        client_secret=None,
        redirect_uris=[AnyUrl(REDIRECT)],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )


async def _authorized(provider, client, secret=SECRET):
    """Проходит согласие и возвращает код авторизации."""
    params = AuthorizationParams(
        state="st",
        scopes=[],
        code_challenge="chal",
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
        resource="https://oura-mcp.lol/mcp",
    )
    url = await provider.authorize(client, params)
    request_id = url.split("request=")[1]
    redirect = provider.grant(request_id, secret)
    return redirect.split("code=")[1].split("&")[0] if redirect else None


# --- регистрация клиента ----------------------------------------------------


async def test_registered_client_round_trips(provider, client):
    await provider.register_client(client)
    loaded = await provider.get_client("cid")
    assert loaded.client_id == "cid"
    assert str(loaded.redirect_uris[0]) == REDIRECT


async def test_unknown_client_is_none(provider):
    assert await provider.get_client("nope") is None


# --- согласие ---------------------------------------------------------------


async def test_wrong_secret_grants_nothing(provider, client):
    """Без секрета согласие не даётся, сколько бы заявок ни завели."""
    assert await _authorized(provider, client, secret="wrong") is None


async def test_near_miss_secret_rejected(provider, client):
    """Секрет сверяется целиком, а не по префиксу."""
    assert await _authorized(provider, client, secret=SECRET[:-1]) is None
    assert await _authorized(provider, client, secret=SECRET + "x") is None


async def test_grant_returns_code_and_preserves_state(provider, client):
    params = AuthorizationParams(
        state="opaque-state",
        scopes=[],
        code_challenge="chal",
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(client, params)
    redirect = provider.grant(url.split("request=")[1], SECRET)
    # state обязан вернуться нетронутым: на нём держится защита от CSRF.
    assert "state=opaque-state" in redirect
    assert redirect.startswith(REDIRECT)


async def test_request_is_single_use(provider, client):
    params = AuthorizationParams(
        state=None,
        scopes=[],
        code_challenge="chal",
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(client, params)
    request_id = url.split("request=")[1]
    assert provider.grant(request_id, SECRET) is not None
    assert provider.grant(request_id, SECRET) is None


async def test_expired_request_is_swept(provider, client, monkeypatch):
    real_now = time.time()
    params = AuthorizationParams(
        state=None,
        scopes=[],
        code_challenge="chal",
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(client, params)
    request_id = url.split("request=")[1]
    # Час спустя заявка мертва, даже с верным секретом.
    monkeypatch.setattr(time, "time", lambda: real_now + 3600)
    assert provider.grant(request_id, SECRET) is None


# --- обмен кода -------------------------------------------------------------


async def test_code_carries_challenge_and_resource(provider, client):
    await provider.register_client(client)
    code = await _authorized(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    assert loaded.code_challenge == "chal"
    assert loaded.resource == "https://oura-mcp.lol/mcp"


async def test_code_is_single_use(provider, client):
    code = await _authorized(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    await provider.exchange_authorization_code(client, loaded)
    assert await provider.load_authorization_code(client, code) is None


async def test_code_not_shared_between_clients(provider, client):
    """Чужой код не обменивается — иначе один клиент забирает сессию другого."""
    code = await _authorized(provider, client)
    other = client.model_copy(update={"client_id": "other"})
    assert await provider.load_authorization_code(other, code) is None


# --- обновление -------------------------------------------------------------


async def test_refresh_rotates_and_kills_the_old_one(provider, client):
    """Ротация обязательна: Claude регистрируется публичным клиентом."""
    code = await _authorized(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    first = await provider.exchange_authorization_code(client, loaded)

    rt = await provider.load_refresh_token(client, first.refresh_token)
    second = await provider.exchange_refresh_token(client, rt, [])

    assert second.access_token != first.access_token
    assert second.refresh_token != first.refresh_token
    assert await provider.load_refresh_token(client, first.refresh_token) is None


async def test_refresh_not_shared_between_clients(provider, client):
    code = await _authorized(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    tokens = await provider.exchange_authorization_code(client, loaded)
    other = client.model_copy(update={"client_id": "other"})
    assert await provider.load_refresh_token(other, tokens.refresh_token) is None


# --- общий секрет как токен -------------------------------------------------


async def test_shared_secret_works_as_access_token(provider):
    """Путь Claude Code: заголовок с общим секретом, без всякого OAuth."""
    token = await provider.load_access_token(SECRET)
    assert token is not None
    assert token.expires_at is None, "статический секрет не должен протухать"
    assert token.scopes == []


async def test_almost_the_secret_is_not_the_secret(provider):
    assert await provider.load_access_token(SECRET[:-1]) is None
    assert await provider.load_access_token(SECRET + "x") is None
    assert await provider.load_access_token("") is None


# --- проверка и отзыв -------------------------------------------------------


async def test_expired_access_token_is_rejected_and_dropped(provider, client, store_path):
    code = await _authorized(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    tokens = await provider.exchange_authorization_code(client, loaded)

    raw = json.loads(store_path.read_text())
    raw["access"][tokens.access_token]["expires_at"] = int(time.time()) - 1
    store_path.write_text(json.dumps(raw))

    fresh = OuraAuthProvider(OAuthStore(store_path), SECRET)
    assert await fresh.load_access_token(tokens.access_token) is None
    # И вычищен, иначе хранилище растёт на токен в час.
    assert tokens.access_token not in json.loads(store_path.read_text())["access"]


async def test_revoke_kills_the_paired_token(provider, client):
    code = await _authorized(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    tokens = await provider.exchange_authorization_code(client, loaded)

    access = await provider.load_access_token(tokens.access_token)
    await provider.revoke_token(access)

    assert await provider.load_access_token(tokens.access_token) is None
    assert await provider.load_refresh_token(client, tokens.refresh_token) is None


# --- хранилище --------------------------------------------------------------


async def test_survives_restart(provider, client, store_path):
    """Перезапуск контейнера не должен выкидывать телефон из авторизации."""
    await provider.register_client(client)
    code = await _authorized(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    tokens = await provider.exchange_authorization_code(client, loaded)

    revived = OuraAuthProvider(OAuthStore(store_path), SECRET)
    assert await revived.get_client("cid") is not None
    assert await revived.load_access_token(tokens.access_token) is not None


async def test_store_is_owner_only(provider, client, store_path):
    await provider.register_client(client)
    assert store_path.stat().st_mode & 0o777 == 0o600


def test_corrupted_store_does_not_prevent_start(store_path):
    """Битое хранилище — не повод не стартовать: здесь нет ничего
    невосстановимого, в отличие от токенов Oura. Худшее — переподключить
    коннектор."""
    store_path.write_text("{ это не json")
    store = OAuthStore(store_path)
    assert store.get("clients", "cid") is None


# --- заплатка на метаданные -------------------------------------------------
#
# Заплатка живёт на ASGI-уровне и ломается незаметно: ответ остаётся
# двухсотым, просто с неверным телом или рассогласованным content-length.
# Поэтому проверяется и то, что она правит, и то, чего она НЕ трогает.


async def _call(app, path="/.well-known/oauth-authorization-server", method="GET"):
    """Прогоняет ASGI-приложение и собирает ответ."""
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await app({"type": "http", "path": path, "method": method}, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, body


def _inner(payload: bytes, status=200, chunks=1):
    """Приложение-заглушка, отдающее payload одним или несколькими кусками."""

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                ],
            }
        )
        if chunks == 1:
            await send({"type": "http.response.body", "body": payload})
            return
        half = len(payload) // 2
        await send({"type": "http.response.body", "body": payload[:half], "more_body": True})
        await send({"type": "http.response.body", "body": payload[half:]})

    return app


METADATA = {
    "issuer": "https://oura-mcp.lol/",
    "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
    "code_challenge_methods_supported": ["S256"],
}


async def test_public_client_method_is_advertised():
    """Claude при DCR приходит публичным клиентом — метод должен быть объявлен."""
    app = AdvertisePublicClients(_inner(json.dumps(METADATA).encode()))
    _, _, body = await _call(app)
    assert json.loads(body)["token_endpoint_auth_methods_supported"] == [
        "client_secret_post",
        "client_secret_basic",
        "none",
    ]


async def test_other_fields_survive():
    """Дописываем, а не подменяем: поля, которые SDK добавит позже, уцелеют."""
    payload = {**METADATA, "introspection_endpoint": "https://oura-mcp.lol/introspect"}
    app = AdvertisePublicClients(_inner(json.dumps(payload).encode()))
    _, _, body = await _call(app)
    doc = json.loads(body)
    assert doc["introspection_endpoint"] == "https://oura-mcp.lol/introspect"
    assert doc["code_challenge_methods_supported"] == ["S256"]


async def test_content_length_matches_patched_body():
    """Рассогласованный content-length рвёт ответ на стороне клиента."""
    app = AdvertisePublicClients(_inner(json.dumps(METADATA).encode()))
    _, headers, body = await _call(app)
    assert int(headers["content-length"]) == len(body)


async def test_not_duplicated_if_already_present():
    payload = {**METADATA, "token_endpoint_auth_methods_supported": ["none"]}
    app = AdvertisePublicClients(_inner(json.dumps(payload).encode()))
    _, _, body = await _call(app)
    assert json.loads(body)["token_endpoint_auth_methods_supported"] == ["none"]


async def test_chunked_body_is_reassembled():
    app = AdvertisePublicClients(_inner(json.dumps(METADATA).encode(), chunks=2))
    _, _, body = await _call(app)
    assert "none" in json.loads(body)["token_endpoint_auth_methods_supported"]


async def test_other_paths_pass_through_untouched():
    raw = b'{"token_endpoint_auth_methods_supported": []}'
    app = AdvertisePublicClients(_inner(raw))
    _, _, body = await _call(app, path="/mcp")
    assert body == raw


async def test_non_get_passes_through_untouched():
    raw = b'{"token_endpoint_auth_methods_supported": []}'
    app = AdvertisePublicClients(_inner(raw))
    _, _, body = await _call(app, method="POST")
    assert body == raw


async def test_non_json_body_is_left_alone():
    """Неразобранное тело отдаём как есть: заплатка не повод ломать ответ."""
    app = AdvertisePublicClients(_inner(b"not json at all"))
    status, headers, body = await _call(app)
    assert body == b"not json at all"
    assert int(headers["content-length"]) == len(body)


async def test_error_response_is_not_patched():
    app = AdvertisePublicClients(_inner(b'{"error":"boom"}', status=500))
    status, _, body = await _call(app)
    assert status == 500
    assert json.loads(body) == {"error": "boom"}
