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

import html
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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

# Refresh-токен живёт три месяца. Срок нужен не столько для безопасности,
# сколько против бесконечного роста: без него каждое переподключение claude.ai
# оставляло бы в хранилище запись навсегда. Три месяца — с запасом больше
# любого разумного простоя телефона, так что переспрашивать секрет не придётся.
REFRESH_TTL_SEC = 90 * 24 * 3600

# Исходы согласия. Их три, а не два, потому что «протухла заявка» и «неверный
# секрет» требуют разных действий от человека: в первом случае надо начать
# подключение заново в claude.ai, во втором — ввести правильный секрет. Одно
# сообщение на оба случая отправляло владельца искать несуществующую ошибку.
GRANT_OK = "ok"
GRANT_BAD_SECRET = "bad_secret"
GRANT_STALE = "stale"


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

    def prune_expired(self, now: float) -> None:
        """Выбрасывает протухшие токены из обоих вёдер.

        Ленивая уборка вместо фонового таймера: вызывается в момент выдачи
        новых токенов, то есть примерно раз в час на активном сервере. Своего
        планировщика ради этого поднимать незачем, а без уборки вовсе файл
        растёт от каждого переподключения и никогда не уменьшается.
        """
        changed = False
        for bucket in ("access", "refresh"):
            keep = {
                token: row
                for token, row in self._data[bucket].items()
                if not row.get("expires_at") or row["expires_at"] > now
            }
            if len(keep) != len(self._data[bucket]):
                self._data[bucket] = keep
                changed = True
        if changed:
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

    def __init__(
        self,
        store: OAuthStore,
        owner_secret: str,
        issuer: str,
        consent_path: str = "/oauth/consent",
    ) -> None:
        self._store = store
        self._owner_secret = owner_secret
        self._issuer = issuer
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

    def redirect_origin(self, request_id: str) -> str | None:
        """Origin, куда уйдёт браузер после согласия, — или None если заявки нет.

        Нужен странице согласия для заголовка CSP. `form-action` проверяется
        браузером на всей цепочке редиректов, а не только на адресе самой
        формы: со значением `'self'` ответ 303 на claude.ai блокируется молча,
        и человек видит, что кнопка «не работает». Поэтому конкретный адрес
        возврата приходится объявить заранее.

        Origin, а не полный URL: в CSP путь всё равно не учитывается.
        """
        self._sweep()
        entry = self._pending.get(request_id)
        if entry is None:
            return None
        parsed = urlsplit(str(entry[1].redirect_uri))
        return f"{parsed.scheme}://{parsed.netloc}"

    def grant(self, request_id: str, presented_secret: str) -> tuple[str | None, str]:
        """Проверяет секрет и выдаёт код.

        Возвращает `(url_редиректа, причина)`. Причины различаются намеренно, и
        это исправление: раньше «заявка протухла» и «неверный секрет» давали
        одно и то же сообщение, чтобы не подсказывать подбирающему. Рассуждение
        не выдержало проверки практикой — владелец получал «проверь секрет»
        после перезапуска сервера и искал ошибку там, где её не было.

        Утечки здесь нет: `request_id` сам по себе секрет на 24 случайных
        байта, и тот, у кого он на руках, и так знает, что заявка живая.
        Про сам `OURA_MCP_TOKEN` наружу по-прежнему не сообщается ничего, кроме
        «подошёл или нет».
        """
        self._sweep()
        entry = self._pending.get(request_id)
        if entry is None:
            # Чаще всего это не атака, а перезапуск сервера: заявки живут в
            # памяти процесса, и рестарт их стирает вместе с открытой вкладкой.
            return None, GRANT_STALE

        # Постоянное время: обычное сравнение выдаёт длину общего префикса.
        if not secrets.compare_digest(presented_secret, self._owner_secret):
            return None, GRANT_BAD_SECRET

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
        # iss — обязательный параметр по RFC 9207 (защита от mix-up атак: без
        # него клиент, работающий с несколькими серверами авторизации, не
        # может убедиться, что редирект пришёл от того же issuer, который был
        # заявлен в метаданных, и вправе молча отбросить callback). Строка
        # берётся из server.py посимвольно совпадающей с полем "issuer" в
        # /.well-known/oauth-authorization-server — иначе проверка всё равно
        # провалится, просто по другой причине.
        redirect = construct_redirect_uri(
            str(params.redirect_uri), code=code, state=params.state, iss=self._issuer
        )
        return redirect, GRANT_OK

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
        now = int(time.time())
        # Уборка приурочена к выдаче: на активном сервере это примерно раз в час,
        # чаще не нужно, а отдельный таймер ради этого не стоит завода.
        self._store.prune_expired(now)

        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        expires_at = now + ACCESS_TTL_SEC

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
            {
                "token": refresh,
                "client_id": client_id,
                "scopes": scopes,
                "expires_at": now + REFRESH_TTL_SEC,
                "subject": "owner",
            },
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

# Отдельная страница для протухшей заявки: формы на ней нет намеренно. Вводить
# секрет заново бессмысленно — этот request_id мёртв, и повторная отправка даст
# ровно тот же ответ. Единственный работающий выход — начать заново в claude.ai.
STALE_HTML = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Запрос устарел</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
         margin: 0; display: grid; place-items: center; min-height: 100dvh; padding: 1.5rem; }
  main { width: min(23rem, 100%); }
  h1 { font-size: 1.25rem; margin: 0 0 .5rem; }
  p { margin: 0 0 1rem; opacity: .75; }
</style></head>
<body><main>
<h1>Запрос устарел</h1>
<p>Эта страница уже использована или сервер перезапускался — с секретом всё в
порядке, проверять его не нужно.</p>
<p>Вернись в claude.ai и нажми «Connect» у коннектора Oura ещё раз.</p>
</main></body></html>
"""


# Заголовки страницы согласия. Каждый закрывает свою дыру, а вместе они делают
# внедрение неисполнимым даже если экранирование однажды прохлопают.
#
#   default-src 'none'  скриптов на странице нет вообще, поэтому запрет полный:
#                       внедрённый <script> просто не исполнится
#   style-src            только для инлайнового <style> ниже
#   form-action 'self'   форму с секретом нельзя перенаправить на чужой хост
#   frame-ancestors      страницу нельзя обернуть в iframe (clickjacking:
#                        поверх формы кладут прозрачный слой и ловят ввод)
#   no-store             в кэш и историю страница с полем секрета не попадает
#   no-referrer          идентификатор заявки не утечёт в Referer
def consent_headers(redirect_origin: str | None = None) -> dict[str, str]:
    """Заголовки страницы согласия.

    `form-action` перечисляет и свой origin, и адрес возврата клиента. Второе
    обязательно, и это стоило одного сбоя вживую: браузер применяет
    `form-action` **ко всей цепочке редиректов**, а не только к адресу, куда
    уходит сама форма. С одним лишь `'self'` наш ответ `303` на claude.ai
    блокировался молча — кнопка «Разрешить» выглядела сломанной, claude.ai не
    получал callback, и `/token` не вызывался ни разу. Curl этого не ловит:
    он CSP не проверяет.

    Origin берётся из заявки, а не зашивается: сюда ходит не только claude.ai,
    и разрешать всё подряд значило бы выкинуть саму защиту, ради которой
    директива стоит — не дать увести форму с секретом на чужой хост.
    """
    form_action = "'self'" if redirect_origin is None else f"'self' {redirect_origin}"
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'unsafe-inline'; "
            f"form-action {form_action}; frame-ancestors 'none'; base-uri 'none'"
        ),
        # Дубль frame-ancestors для браузеров, которые его не знают.
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


# Для страниц без формы (протухшая заявка) адрес возврата не нужен.
CONSENT_HEADERS = consent_headers()


def _consent_page(
    request_id: str, error: bool = False, redirect_origin: str | None = None
) -> HTMLResponse:
    """Страница согласия с экранированным идентификатором заявки.

    Экранирование здесь обязательно и неочевидно: `request_id` приходит из
    query-параметра, то есть управляется тем, кто прислал ссылку. Без escape
    ссылка вида `?request="><script>…` внедряла бы скрипт в страницу, где
    владелец вводит секрет, — и скрипт читал бы это поле. Пример есть в тестах.
    """
    return HTMLResponse(
        CONSENT_HTML.format(
            request_id=html.escape(request_id, quote=True),
            error=ERROR_BLOCK if error else "",
        ),
        status_code=401 if error else 200,
        headers=consent_headers(redirect_origin),
    )


def register_consent_route(mcp, provider: OuraAuthProvider, path: str = "/oauth/consent") -> None:
    """Вешает страницу согласия на сервер.

    Отдельной функцией, а не внутри провайдера: провайдер ничего не знает про
    HTTP и тестируется без него, что и показал прогон флоу.
    """

    @mcp.custom_route(path, methods=["GET", "POST"])
    async def consent(request: Request):
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            return _consent_page(
                request_id, redirect_origin=provider.redirect_origin(request_id)
            )

        form = await request.form()
        request_id = str(form.get("request", ""))
        # Origin читаем ДО grant(): успешный grant заявку снимает.
        origin = provider.redirect_origin(request_id)
        redirect, reason = provider.grant(request_id, str(form.get("password", "")))
        if reason == GRANT_STALE:
            # Форму не показываем: этот request_id мёртв, и повторный ввод
            # секрета ничего не изменит. Раньше здесь была та же форма с
            # «проверь секрет», и это отправляло владельца искать ошибку в
            # правильном секрете.
            return HTMLResponse(STALE_HTML, status_code=410, headers=CONSENT_HEADERS)
        if redirect is None:
            return _consent_page(request_id, error=True, redirect_origin=origin)
        # 303: после POST браузер обязан пойти GET'ом, иначе повторная отправка
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
