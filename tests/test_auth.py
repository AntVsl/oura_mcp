import json
import os
import time
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from my_oura_mcp.auth import AuthError, OuraOAuth, Tokens, TokenStore
from my_oura_mcp.config import TOKEN_URL, Settings


def settings(tmp_path, mode="production") -> Settings:
    return Settings(
        mode=mode,
        tz=ZoneInfo("Europe/Moscow"),
        client_id="cid",
        client_secret="csecret",
        redirect_uri="http://localhost:8765/callback",
        token_store=tmp_path / "tokens.json",
        cache_db=tmp_path / "cache.db",
    )


def token_response(access="a1", refresh="r1", expires_in=86400):
    return httpx.Response(
        200,
        json={
            "token_type": "bearer",
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": expires_in,
        },
    )


# --- хранилище --------------------------------------------------------------


def test_store_roundtrip(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    tokens = Tokens("access", "refresh", time.time() + 3600)
    store.save(tokens)
    assert store.load() == tokens


def test_store_file_is_owner_only(tmp_path):
    """Секрет на диске не должен быть читаем другими пользователями."""
    store = TokenStore(tmp_path / "t.json")
    store.save(Tokens("a", "r", time.time() + 60))
    assert oct(os.stat(store.path).st_mode)[-3:] == "600"


def test_store_leaves_no_temp_file(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    store.save(Tokens("a", "r", time.time() + 60))
    assert list(tmp_path.glob("*.tmp")) == [], "временный файл должен быть переименован"


def test_missing_store_is_not_an_error(tmp_path):
    assert TokenStore(tmp_path / "нет.json").load() is None


def test_corrupt_store_explains_itself(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("{это не json")
    with pytest.raises(AuthError, match="повреждено"):
        TokenStore(path).load()


def test_clear_removes_file(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    store.save(Tokens("a", "r", time.time() + 60))
    store.clear()
    assert not store.path.exists()


# --- срок жизни -------------------------------------------------------------


def test_token_expiring_within_margin_counts_as_expired():
    """Обновляемся заранее, иначе запрос на грани получит отказ."""
    assert Tokens("a", "r", time.time() + 60).expired


def test_fresh_token_is_not_expired():
    assert not Tokens("a", "r", time.time() + 86400).expired


# --- обновление -------------------------------------------------------------


@respx.mock
async def test_refresh_persists_before_returning(tmp_path):
    """Новый refresh обязан лечь на диск: старый Oura уже аннулировала."""
    cfg = settings(tmp_path)
    store = TokenStore(cfg.token_store)
    store.save(Tokens("old_access", "old_refresh", time.time() - 1))
    respx.post(TOKEN_URL).mock(return_value=token_response("new_access", "new_refresh"))

    assert await OuraOAuth(cfg, store).access_token() == "new_access"
    saved = json.loads(cfg.token_store.read_text())
    assert saved["refresh_token"] == "new_refresh"


@respx.mock
async def test_valid_token_is_not_refreshed(tmp_path):
    cfg = settings(tmp_path)
    store = TokenStore(cfg.token_store)
    store.save(Tokens("live", "r", time.time() + 86400))
    route = respx.post(TOKEN_URL)

    assert await OuraOAuth(cfg, store).access_token() == "live"
    assert route.call_count == 0, "живой токен обновлять незачем"


@respx.mock
async def test_concurrent_calls_refresh_once(tmp_path):
    """Второе одновременное обновление ушло бы с потраченным refresh-токеном."""
    import asyncio

    cfg = settings(tmp_path)
    store = TokenStore(cfg.token_store)
    store.save(Tokens("old", "r", time.time() - 1))
    route = respx.post(TOKEN_URL).mock(return_value=token_response("new", "r2"))

    oauth = OuraOAuth(cfg, store)
    results = await asyncio.gather(*(oauth.access_token() for _ in range(5)))
    assert results == ["new"] * 5
    assert route.call_count == 1


@respx.mock
async def test_rejected_refresh_names_the_cause(tmp_path):
    cfg = settings(tmp_path)
    store = TokenStore(cfg.token_store)
    store.save(Tokens("a", "spent", time.time() - 1))
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid"}))

    with pytest.raises(AuthError, match="одноразовый"):
        await OuraOAuth(cfg, store).access_token()


async def test_no_tokens_points_at_auth_command(tmp_path):
    cfg = settings(tmp_path)
    with pytest.raises(AuthError, match="my-oura-mcp auth"):
        await OuraOAuth(cfg, TokenStore(cfg.token_store)).access_token()


# --- обмен кода -------------------------------------------------------------


@respx.mock
async def test_exchange_code_sends_expected_body(tmp_path):
    cfg = settings(tmp_path)
    route = respx.post(TOKEN_URL).mock(return_value=token_response())
    await OuraOAuth(cfg, TokenStore(cfg.token_store)).exchange_code("код")

    body = dict(p.split("=", 1) for p in route.calls[0].request.content.decode().split("&"))
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "%D0%BA%D0%BE%D0%B4"  # urlencoded
    assert body["client_id"] == "cid" and body["client_secret"] == "csecret"


@respx.mock
async def test_exchange_saves_tokens(tmp_path):
    cfg = settings(tmp_path)
    respx.post(TOKEN_URL).mock(return_value=token_response("acc", "ref"))
    await OuraOAuth(cfg, TokenStore(cfg.token_store)).exchange_code("c")
    assert json.loads(cfg.token_store.read_text())["access_token"] == "acc"


@respx.mock
async def test_response_without_refresh_token_is_rejected(tmp_path):
    cfg = settings(tmp_path)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "a", "expires_in": 60})
    )
    with pytest.raises(AuthError, match="refresh_token"):
        await OuraOAuth(cfg, TokenStore(cfg.token_store)).exchange_code("c")


@respx.mock
async def test_bad_credentials_mention_redirect_uri(tmp_path):
    cfg = settings(tmp_path)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "bad"}))
    with pytest.raises(AuthError, match="OURA_REDIRECT_URI"):
        await OuraOAuth(cfg, TokenStore(cfg.token_store)).exchange_code("c")


@respx.mock
async def test_network_failure_is_explained(tmp_path):
    cfg = settings(tmp_path)
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("оборвалось"))
    with pytest.raises(AuthError, match="Проверь сеть"):
        await OuraOAuth(cfg, TokenStore(cfg.token_store)).exchange_code("c")


# --- состояние --------------------------------------------------------------


def test_status_without_tokens(tmp_path):
    cfg = settings(tmp_path)
    assert OuraOAuth(cfg, TokenStore(cfg.token_store)).status()["authorized"] is False


def test_status_with_tokens(tmp_path):
    cfg = settings(tmp_path)
    store = TokenStore(cfg.token_store)
    store.save(Tokens("a", "r", time.time() + 3 * 86400))
    status = OuraOAuth(cfg, store).status()
    assert status["authorized"] is True
    assert status["access_token_expires_in"] == "2 дн"
