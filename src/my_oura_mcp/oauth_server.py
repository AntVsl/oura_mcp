"""Сервер авторизации OAuth 2.1 — вход для claude.ai.

Зачем он нужен. У claude.ai два способа аутентифицироваться в стороннем
MCP-сервере: статический заголовок и OAuth. Первый (`static_headers`) — beta с
ограниченной раскаткой, и в диалоге добавления коннектора поля для него может
не быть вовсе. Тогда остаётся OAuth, причём непременно с динамической
регистрацией: Claude регистрируется сам, вручную вписать client_id некуда.

Чего здесь НЕТ, и это намеренно. Весь протокольный слой уже реализован в
официальном SDK — `mcp.server.auth`, — и дублировать его было бы не только
лишней работой, но и лишним местом для ошибки. SDK сам:

  * сверяет PKCE (`token.py`: sha256 от code_verifier против code_challenge)
    и принимает только метод S256 — иные не проходят по типу;
  * следит за сроком жизни кода и refresh-токена;
  * требует, чтобы redirect_uri на /token совпадал с тем, что был на
    /authorize, и чтобы он был среди зарегистрированных у клиента;
  * генерирует client_id и client_secret при регистрации.

Поэтому ниже нет ни одной криптографической проверки: если искать их здесь,
не найдёшь — они выше по стеку. Этому модулю остаются хранение и политика,
то есть «кого пускать» и «что переживает перезапуск».

Модель доступа та же, что и у всего сервера: **владелец один**. Поэтому
страница согласия спрашивает уже существующий `OURA_MCP_TOKEN`, а не заводит
второй пароль. Лишний секрет пришлось бы отдельно хранить, отдельно менять и
отдельно терять — а защищает он ровно то же самое.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

# Access-токен живёт час: Claude обновляет его сам, реактивно по 401 и
# заранее за пять минут до истечения, так что короткий срок ничего не стоит.
ACCESS_TTL_SEC = 3600

# Код обменивается за секунды. Минута — запас на неспешную сеть, не больше:
# всё это время код лежит в истории браузера и в логах редиректов.
CODE_TTL_SEC = 60

# Согласие ждём десять минут — столько человек может вводить пароль на
# телефоне, переключаясь в менеджер паролей и обратно.
PENDING_TTL_SEC = 600


class OAuthStore:
    """Клиенты и токены на диске: только владелец, запись атомарная.

    На диске, а не в памяти, ради одной вещи: перезапуск контейнера не должен
    выкидывать телефон из авторизации. Иначе каждое обновление образа
    оборачивается походом в настройки claude.ai с ноутбука.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any]] = {
            "clients": {},
            "refresh": {},
            "access": {},
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text())
        except (OSError, ValueError):
            # Повреждённое хранилище — не повод не стартовать: здесь нет
            # ничего невосстановимого, в отличие от токенов Oura. Худшее
            # последствие — заново подключить коннектор.
            return
        for key in self._data:
            if isinstance(loaded.get(key), dict):
                self._data[key] = loaded[key]

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(self._data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def get(self, bucket: str, key: str) -> dict[str, Any] | None:
        return self._data[bucket].get(key)

    def put(self, bucket: str, key: str, value: dict[str, Any]) -> None:
        self._data[bucket][key] = value
        self._flush()

    def drop(self, bucket: str, key: str) -> None:
        if self._data[bucket].pop(key, None) is not None:
            self._flush()

    def drop_where(self, bucket: str, field: str, value: Any) -> None:
        """Удалить все записи, у которых поле равно значению.

        Нужно при отзыве: по спецификации отзыв одного токена должен убивать и
        парный ему, а связь между ними — общий `client_id` плюс происхождение.
        """
        keep = {k: v for k, v in self._data[bucket].items() if v.get(field) != value}
        if len(keep) != len(self._data[bucket]):
            self._data[bucket] = keep
            self._flush()


class OuraAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Провайдер авторизации на одного владельца.

    Согласие даётся вводом `OURA_MCP_TOKEN` на странице /oauth/consent.
    """

    def __init__(self, store: OAuthStore, owner_secret: str, consent_path: str = "/oauth/consent") -> None:
        self._store = store
        self._owner_secret = owner_secret
        self._consent_path = consent_path
        # Заявки на авторизацию живут минуты и переживать перезапуск не должны:
        # незавершённое согласие после рестарта честнее начать заново.
        self._pending: dict[str, tuple[str, AuthorizationParams, float]] = {}
        # Коды — тоже в памяти и по той же причине.
        self._codes: dict[str, AuthorizationCode] = {}

    # --- регистрация клиента (DCR) -----------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = self._store.get("clients", client_id)
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Регистрация открытая, и это осознанно. Единственное, что даёт
        # регистрация, — возможность начать флоу; закончить его без
        # OURA_MCP_TOKEN всё равно нельзя. Закрывать её значило бы требовать
        # секрет ещё и здесь, а Claude на этом шаге его предъявить не может.
        self._store.put("clients", client_info.client_id, client_info.model_dump(mode="json"))

    # --- согласие -----------------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Отправляет владельца на страницу согласия.

        Обычно здесь начинается второй OAuth-обмен со сторонним провайдером.
        У нас его нет: владелец один и он уже знает секрет, поэтому
        «провайдер личности» — форма с этим секретом.
        """
        self._sweep()
        request_id = secrets.token_urlsafe(24)
        self._pending[request_id] = (client.client_id, params, time.time() + PENDING_TTL_SEC)
        return f"{self._consent_path}?request={request_id}"

    def grant(self, request_id: str, presented_secret: str) -> str | None:
        """Проверяет секрет и выдаёт код. Возвращает URL редиректа или None.

        None означает «не пущен» — без уточнения, что именно не так. Разделять
        «нет такой заявки» и «неверный секрет» в ответе не стоит: это
        подсказка тому, кто подбирает.
        """
        self._sweep()
        entry = self._pending.get(request_id)
        if entry is None:
            return None

        # Постоянное время: обычное сравнение выдаёт длину общего префикса.
        if not secrets.compare_digest(presented_secret, self._owner_secret):
            return None

        client_id, params, _ = self._pending.pop(request_id)
        code = secrets.token_urlsafe(32)  # 256 бит, вчетверо выше требуемого RFC 6749
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + CODE_TTL_SEC,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject="owner",
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    def _sweep(self) -> None:
        """Выбрасывает протухшие заявки и коды.

        Без этого словари растут от каждой брошенной попытки входа — медленная
        утечка, которую в норме никто не заметит.
        """
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if v[2] > now}
        self._codes = {k: v for k, v in self._codes.items() if v.expires_at > now}

    # --- обмен кода на токены ----------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Код одноразовый: снимаем до выдачи токенов, а не после.
        self._codes.pop(authorization_code.code, None)
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    # --- обновление ---------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        raw = self._store.get("refresh", refresh_token)
        if raw is None or raw.get("client_id") != client.client_id:
            return None
        return RefreshToken.model_validate(raw)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Ротация обязательна: спецификация MCP требует её для публичных
        # клиентов, а Claude при DCR регистрируется именно публичным. Старый
        # refresh снимается в той же операции, что выдаёт новый.
        self._store.drop("refresh", refresh_token.token)
        return self._issue(client.client_id, scopes or refresh_token.scopes, None)

    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + ACCESS_TTL_SEC

        self._store.put(
            "access",
            access,
            {
                "token": access,
                "client_id": client_id,
                "scopes": scopes,
                "expires_at": expires_at,
                "resource": resource,
                "subject": "owner",
            },
        )
        self._store.put(
            "refresh",
            refresh,
            {"token": refresh, "client_id": client_id, "scopes": scopes, "subject": "owner"},
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL_SEC,
            refresh_token=refresh,
            scope=" ".join(scopes) if scopes else None,
        )

    # --- проверка и отзыв ---------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        # Общий секрет работает как бессрочный access-токен. Это не поблажка,
        # а способ иметь ОДИН путь проверки вместо двух: иначе статический
        # секрет проходил бы свой привратник и упирался в OAuth-middleware
        # SDK, стоящий следом. Так Claude Code продолжает ходить с заголовком,
        # claude.ai ходит по OAuth, а расходятся они здесь, в одной функции.
        #
        # expires_at=None — токен не протухает (middleware проверяет срок,
        # только если он задан); пустые scopes безопасны, пока required_scopes
        # тоже пуст.
        if secrets.compare_digest(token, self._owner_secret):
            return AccessToken(
                token=token,
                client_id="static",
                scopes=[],
                expires_at=None,
                subject="owner",
            )

        raw = self._store.get("access", token)
        if raw is None:
            return None
        if raw.get("expires_at") and raw["expires_at"] < time.time():
            # Протухшее чистим сразу: иначе хранилище растёт на токен в час.
            self._store.drop("access", token)
            return None
        return AccessToken.model_validate(raw)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        bucket = "access" if isinstance(token, AccessToken) else "refresh"
        self._store.drop(bucket, token.token)
        # Спецификация просит убивать и парный токен. Пары как таковой у нас
        # нет, поэтому отзываем всё, что выдано этому клиенту: для сервера на
        # одного владельца это ровно та сессия, которую и просили отозвать.
        other = "refresh" if bucket == "access" else "access"
        self._store.drop_where(other, "client_id", token.client_id)


# --- страница согласия ------------------------------------------------------

# Вёрстка нарочно в одном файле и без зависимостей: страницу открывают один раз
# при подключении коннектора, и тащить ради неё шаблонизатор незачем.
#
# Открывают её, как правило, с телефона — claude.ai уводит в браузер прямо с
# айфона, — отсюда viewport, крупное поле и inputmode. Атрибуты autocomplete и
# name=password нужны, чтобы менеджер паролей предложил сохранить секрет: без
# них его придётся каждый раз вводить руками с экрана.
CONSENT_HTML = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Доступ к данным Oura</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
         margin: 0; display: grid; place-items: center; min-height: 100dvh; padding: 1.5rem; }}
  main {{ width: min(23rem, 100%); }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .5rem; }}
  p {{ margin: 0 0 1.25rem; opacity: .75; }}
  input {{ width: 100%; box-sizing: border-box; font-size: 1rem; padding: .75rem;
           border: 1px solid currentColor; border-radius: .5rem; background: transparent;
           color: inherit; }}
  button {{ width: 100%; margin-top: .75rem; font-size: 1rem; padding: .75rem;
            border: 0; border-radius: .5rem; background: #2563eb; color: #fff; }}
  .err {{ color: #dc2626; margin: 0 0 1rem; }}
</style></head>
<body><main>
<h1>Доступ к данным Oura</h1>
<p>Claude просит доступ к твоим показателям сна и восстановления.
Подтверди секретом сервера.</p>
{error}
<form method="post">
  <input type="hidden" name="request" value="{request_id}">
  <input type="password" name="password" autocomplete="current-password"
         autofocus required placeholder="OURA_MCP_TOKEN"
         aria-label="Секрет сервера">
  <button type="submit">Разрешить</button>
</form>
</main></body></html>
"""

ERROR_BLOCK = '<p class="err">Не подошло. Проверь секрет и попробуй ещё раз.</p>'


def register_consent_route(mcp, provider: OuraAuthProvider, path: str = "/oauth/consent") -> None:
    """Вешает страницу согласия на сервер.

    Отдельной функцией, а не внутри провайдера: провайдер ничего не знает про
    HTTP и тестируется без него, что и показал прогон флоу.
    """

    @mcp.custom_route(path, methods=["GET", "POST"])
    async def consent(request: Request):
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            # Несуществующую заявку показываем той же формой, без пояснений:
            # разница в ответах подсказывала бы, какие идентификаторы живые.
            return HTMLResponse(
                CONSENT_HTML.format(request_id=request_id, error=""),
                # Страница содержит поле с секретом — в кэш ей нельзя.
                headers={"Cache-Control": "no-store"},
            )

        form = await request.form()
        request_id = str(form.get("request", ""))
        redirect = provider.grant(request_id, str(form.get("password", "")))
        if redirect is None:
            return HTMLResponse(
                CONSENT_HTML.format(request_id=request_id, error=ERROR_BLOCK),
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        # 303: после POST браузер обязан пойтиGET'ом, иначе повторная отправка
        # формы по «назад» уткнётся в уже потраченную заявку.
        return RedirectResponse(redirect, status_code=303)


# --- заплатка на метаданные -------------------------------------------------

AS_METADATA_PATH = "/.well-known/oauth-authorization-server"
PUBLIC_CLIENT_METHOD = "none"


class AdvertisePublicClients:
    """Дописывает "none" в token_endpoint_auth_methods_supported.

    SDK зашивает этот список константой (`routes.py`:
    ["client_secret_post", "client_secret_basic"]) и настройки не даёт. При этом
    публичных клиентов он обслуживает правильно — `client_auth.py` разбирает
    случай "none" явно. То есть расходятся объявленное и фактическое поведение,
    а Claude при динамической регистрации приходит именно публичным клиентом:
    секрета ему не выдают, и на /token он аутентификацию клиента не предъявляет.

    Почему middleware, а не свой маршрут: маршруты SDK регистрируются раньше
    пользовательских, и Starlette отдаёт запрос первому совпавшему — свой
    обработчик на этом пути недостижим. Проверено запуском.

    Почему дописываем, а не подменяем документ целиком: подмена молча потеряла
    бы поля, которые SDK добавит в будущих версиях. Заплатка правит одно поле и
    ничего не знает про остальные.
    """

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("path") != AS_METADATA_PATH:
            await self._app(scope, receive, send)
            return
        if scope.get("method") != "GET":
            await self._app(scope, receive, send)
            return

        start: dict | None = None
        chunks: list[bytes] = []

        async def capture(message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                await _flush_patched(send, start, b"".join(chunks))
                return
            await send(message)

        await self._app(scope, receive, capture)


async def _flush_patched(send, start: dict | None, body: bytes) -> None:
    """Отдаёт ответ, добавив "none" в список методов аутентификации клиента."""
    patched = body
    if start is not None and start.get("status") == 200:
        try:
            doc = json.loads(body)
            methods = doc.get("token_endpoint_auth_methods_supported")
            if isinstance(methods, list) and PUBLIC_CLIENT_METHOD not in methods:
                doc["token_endpoint_auth_methods_supported"] = [*methods, PUBLIC_CLIENT_METHOD]
                patched = json.dumps(doc).encode()
        except (ValueError, AttributeError):
            # Не разобралось — отдаём как есть. Заплатка на метаданных не
            # повод ломать ответ, который сам по себе рабочий.
            patched = body

    headers = [
        (name, value)
        for name, value in (start or {}).get("headers", [])
        if name.lower() != b"content-length"
    ]
    headers.append((b"content-length", str(len(patched)).encode()))

    await send(
        {
            "type": "http.response.start",
            "status": (start or {}).get("status", 200),
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": patched})
