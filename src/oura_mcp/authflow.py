"""Разовый интерактивный вход в Oura.

Поднимает локальный сервер на адресе из OURA_REDIRECT_URI, открывает браузер,
ловит код и меняет его на токены. Дальше сервер живёт на refresh-токене, и
повторять это не нужно — пока авторизацию не отзовут.

Слушаем строго loopback: этот адрес принимает authorization code, и выставлять
его наружу нельзя.
"""

from __future__ import annotations

import asyncio
import http.server
import secrets
import threading
import urllib.parse
import webbrowser

from .auth import AuthError, OuraOAuth, TokenStore
from .config import AUTHORIZE_URL, SCOPES, Settings

WAIT_TIMEOUT_SEC = 300

_PAGE = """<!doctype html><meta charset="utf-8">
<title>oura-mcp</title>
<body style="font-family:system-ui;padding:3rem;max-width:32rem">
<h2>{title}</h2><p>{message}</p></body>"""


class _Callback(http.server.BaseHTTPRequestHandler):
    """Принимает единственный редирект от Oura."""

    result: dict[str, str] = {}
    expected_path = "/callback"
    done = threading.Event()

    def do_GET(self) -> None:  # noqa: N802 — имя задано базовым классом
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.expected_path:
            self._reply(404, "Не тот адрес", f"Ожидался {self.expected_path}")
            return

        params = urllib.parse.parse_qs(parsed.query)
        type(self).result = {k: v[0] for k, v in params.items()}

        if "error" in type(self).result:
            self._reply(
                400,
                "Доступ не выдан",
                f"Oura вернула ошибку: {type(self).result['error']}. "
                "Вернись в терминал.",
            )
        elif "code" in type(self).result:
            self._reply(200, "Готово", "Авторизация принята — можно закрыть вкладку.")
        else:
            self._reply(400, "Пустой ответ", "Oura не прислала код. Вернись в терминал.")
        type(self).done.set()

    def _reply(self, status: int, title: str, message: str) -> None:
        body = _PAGE.format(title=title, message=message).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Молчим: адрес запроса содержит authorization code."""


async def run(settings: Settings) -> str:
    """Проводит авторизацию целиком. Возвращает сообщение об успехе."""
    settings.require_oauth()

    parsed = urllib.parse.urlparse(settings.redirect_uri)
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        raise AuthError(
            f"OURA_REDIRECT_URI указывает на {parsed.hostname}, а флоу поднимает "
            "локальный сервер. Для авторизации нужен адрес на localhost."
        )
    port = parsed.port or 80
    state = secrets.token_urlsafe(24)

    _Callback.expected_path = parsed.path or "/callback"
    _Callback.result = {}
    _Callback.done = threading.Event()

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Callback)
    except OSError as exc:
        raise AuthError(
            f"Не удалось занять порт {port} ({exc}). Освободи его или поменяй "
            "OURA_REDIRECT_URI — не забыв поправить и приложение на Oura."
        ) from exc

    url = f"{AUTHORIZE_URL}?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "scope": " ".join(SCOPES),
            "state": state,
        }
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print(f"Открываю браузер. Если не открылся — перейди сам:\n\n  {url}\n")
        webbrowser.open(url)
        print(f"Жду ответа Oura (до {WAIT_TIMEOUT_SEC // 60} мин)…")

        if not await asyncio.to_thread(_Callback.done.wait, WAIT_TIMEOUT_SEC):
            raise AuthError(
                "Ответ от Oura не пришёл. Проверь, что открылась именно та ссылка, "
                "и что redirect URI в приложении совпадает с OURA_REDIRECT_URI."
            )
    finally:
        server.shutdown()
        server.server_close()

    result = _Callback.result
    if "error" in result:
        raise AuthError(
            f"Oura отказала в доступе: {result['error']}"
            + (f" — {result['error_description']}" if "error_description" in result else "")
        )
    if result.get("state") != state:
        # Несовпадение state означает, что редирект пришёл не из нашего запроса.
        raise AuthError("Параметр state не совпал — запрос отброшен. Повтори авторизацию.")
    if "code" not in result:
        raise AuthError(f"В ответе нет кода. Получено: {sorted(result)}")

    oauth = OuraOAuth(settings, TokenStore(settings.token_store))
    tokens = await oauth.exchange_code(result["code"])
    return (
        f"Авторизация пройдена. Токен сохранён в {settings.token_store} "
        f"(действует {tokens.expires_in_human}).\n"
        "Переключи OURA_API_MODE=production в .env, чтобы работать со своими данными."
    )
