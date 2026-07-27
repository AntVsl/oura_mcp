"""OAuth2 для Oura: хранение токенов, обновление, разовый флоу авторизации.

Главная опасность здесь — одноразовость refresh-токена. Обменяв его, Oura тут
же аннулирует старый; если новый не доедет до диска, авторизация потеряна и
её придётся проходить заново. Поэтому запись атомарная (временный файл плюс
rename) и происходит ДО того, как новый токен кому-то отдан.

Вторая опасность — одновременное обновление из двух корутин: второй запрос
уйдёт с уже потраченным refresh-токеном и получит отказ. Отсюда блокировка.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from .config import TOKEN_URL, Settings

# Обновляемся заранее: запрос, стартовавший на грани, успеет получить отказ.
REFRESH_MARGIN_SEC = 300
TIMEOUT = httpx.Timeout(30.0, connect=15.0)


class AuthError(RuntimeError):
    """Проблема авторизации, сформулированная для человека."""


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - REFRESH_MARGIN_SEC

    @property
    def expires_in_human(self) -> str:
        left = int(self.expires_at - time.time())
        if left <= 0:
            return "истёк"
        if left < 3600:
            return f"{left // 60} мин"
        if left < 86400:
            return f"{left // 3600} ч"
        return f"{left // 86400} дн"

    @classmethod
    def from_response(cls, payload: dict) -> Tokens:
        try:
            return cls(
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                expires_at=time.time() + float(payload.get("expires_in", 86400)),
            )
        except KeyError as exc:
            raise AuthError(
                f"Ответ Oura без поля {exc}. Получено: {sorted(payload)}"
            ) from exc


class TokenStore:
    """Файл с токенами: только владелец, запись атомарная."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Tokens | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text())
            return Tokens(**data)
        except (OSError, ValueError, TypeError) as exc:
            raise AuthError(
                f"Хранилище токенов {self.path} повреждено ({exc}). "
                "Удали файл и пройди авторизацию заново: uv run oura-mcp auth"
            ) from exc

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # Права выставляются до записи, иначе секрет успевает полежать открытым.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(asdict(tokens), fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)  # атомарно в пределах ФС

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class OuraOAuth:
    def __init__(
        self,
        settings: Settings,
        store: TokenStore | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._store = store or TokenStore(settings.token_store)
        self._http = http
        self._lock = asyncio.Lock()
        self._cached: Tokens | None = None

    # --- получение действующего токена ------------------------------------

    async def access_token(self) -> str:
        """Действующий access-токен; обновляет по необходимости."""
        async with self._lock:
            tokens = self._cached or self._store.load()
            if tokens is None:
                raise AuthError(
                    "Авторизация не пройдена — токенов нет.\n"
                    "Запусти: uv run oura-mcp auth"
                )
            if tokens.expired:
                tokens = await self._refresh(tokens.refresh_token)
            self._cached = tokens
            return tokens.access_token

    async def _refresh(self, refresh_token: str) -> Tokens:
        tokens = await self._post(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        # Сохраняем немедленно: старый refresh уже мёртв на стороне Oura.
        self._store.save(tokens)
        return tokens

    # --- обмен кода на токены ----------------------------------------------

    async def exchange_code(self, code: str) -> Tokens:
        tokens = await self._post(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._settings.redirect_uri,
            }
        )
        self._store.save(tokens)
        self._cached = tokens
        return tokens

    async def _post(self, payload: dict[str, str]) -> Tokens:
        client_id, client_secret = self._settings.require_oauth()
        body = {**payload, "client_id": client_id, "client_secret": client_secret}

        http = self._http or httpx.AsyncClient(timeout=TIMEOUT)
        try:
            resp = await http.post(TOKEN_URL, data=body)
        except httpx.TransportError as exc:
            raise AuthError(
                f"Не удалось связаться с {TOKEN_URL} ({type(exc).__name__}). "
                "Проверь сеть или VPN."
            ) from exc
        finally:
            if self._http is None:
                await http.aclose()

        if resp.status_code != 200:
            raise AuthError(self._explain(resp, payload["grant_type"]))
        try:
            return Tokens.from_response(resp.json())
        except ValueError as exc:
            raise AuthError(f"Ответ Oura не разобрался как JSON: {resp.text[:200]}") from exc

    def _explain(self, resp: httpx.Response, grant: str) -> str:
        try:
            detail = str(resp.json())[:300]
        except ValueError:
            detail = resp.text[:200]

        if resp.status_code in (400, 401) and grant == "refresh_token":
            hint = (
                "refresh-токен отвергнут. Он одноразовый: так бывает, если тем же "
                "токеном воспользовался другой экземпляр сервера. Держи один "
                "живой инстанс и пройди авторизацию заново: uv run oura-mcp auth"
            )
        elif resp.status_code in (400, 401):
            hint = (
                "Oura отверг код или учётные данные. Проверь OURA_CLIENT_ID / "
                "OURA_CLIENT_SECRET и что OURA_REDIRECT_URI совпадает "
                "посимвольно с указанным в приложении"
            )
        else:
            hint = "неожиданный ответ сервера авторизации"

        return f"HTTP {resp.status_code} при grant_type={grant} — {hint}. Ответ: {detail}"

    # --- состояние ----------------------------------------------------------

    def status(self) -> dict[str, object]:
        tokens = self._store.load()
        if tokens is None:
            return {"authorized": False, "token_store": str(self._store.path)}
        return {
            "authorized": True,
            "token_store": str(self._store.path),
            "access_token_expires_in": tokens.expires_in_human,
            "needs_refresh": tokens.expired,
        }

    def logout(self) -> None:
        self._store.clear()
        self._cached = None
