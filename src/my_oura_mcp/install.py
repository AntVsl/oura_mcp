"""Готовые к вставке настройки для MCP-клиентов.

Печатает, а не записывает. Соблазн дописаться в чужой конфиг велик — сосед
`YasuakiOmokawa/oura-mcp` так и делает, — но цена ошибки платится не нами:
кривой merge ломает у человека уже настроенные серверы. Плюс форматы и пути
меняются по версиям и операционным системам, и это обслуживание навсегда.

Смысл здесь ровно один: подставить абсолютный путь. Именно на относительном
пути в `claude mcp add` спотыкаются чаще всего — настолько, что это попало в
раздел README про грабли.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .config import Settings


def _claude_desktop_config() -> Path | None:
    """Путь к конфигу Claude Desktop для текущей ОС."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform.startswith("win"):
        appdata = os.getenv("APPDATA")
        return Path(appdata) / "Claude/claude_desktop_config.json" if appdata else None
    return home / ".config/Claude/claude_desktop_config.json"


def _found(path: Path | None) -> str:
    """Помечает, существует ли конфиг, — но ничего с ним не делает."""
    if path is None:
        return "путь для этой ОС неизвестен"
    return "найден" if path.exists() else "не найден, создастся при первой настройке"


def render(settings: Settings, project_root: Path | None) -> str:
    """Собирает подсказку целиком. Отдельно от печати — чтобы тестировать."""
    out: list[str] = []
    remote = settings.public_url

    if remote:
        out += [
            "Сервер развёрнут публично: веб-клиенты подключаются без правки",
            "локальных конфигов.",
            "",
            "  claude.ai → Settings → Connectors → Add custom connector",
            f"  URL: {settings.resource_url}",
            "",
            "Coгласие спросит OURA_MCP_TOKEN. Коннектор заводится один раз и",
            "работает во всех клиентах Claude одного аккаунта, включая телефон.",
            "",
            "Claude Code с любой машины — заголовком, без OAuth:",
            "",
            f'  claude mcp add --scope user --transport http oura {settings.resource_url} \\',
            '      --header "Authorization: Bearer $OURA_MCP_TOKEN"',
            "",
            "Codex с любой машины — тем же токеном:",
            "",
            f"  codex mcp add oura --url {settings.resource_url} \\",
            "      --bearer-token-env-var OURA_MCP_TOKEN",
            "",
            "ChatGPT подключается только к публичному MCP: Settings → Apps → Create,",
            f"  endpoint: {settings.resource_url}; выбери OAuth и заверши consent.",
            "",
            "Ниже — локальный вариант, если сервер нужен рядом, без сети.",
            "",
        ]

    # В исходном checkout используем uv --directory. В wheel/uvx рядом с
    # модулем нет pyproject.toml, поэтому правильная команда — сам entrypoint.
    root = project_root.resolve() if project_root is not None else None
    uv = shutil.which("uv") or "uv"
    command = f"{uv} --directory {root} run my-oura-mcp" if root is not None else "my-oura-mcp"
    desktop_command = uv if root is not None else "my-oura-mcp"
    desktop_args = ["--directory", str(root), "run", "my-oura-mcp"] if root is not None else []

    out += [
        "── Claude Code ─────────────────────────────────────────────",
        "",
        f"  claude mcp add oura -- {command}",
        "",
        "── Codex ───────────────────────────────────────────────────",
        "",
        f"  codex mcp add oura -- {command}",
        "",
    ]

    desktop = _claude_desktop_config()
    entry = {
        "mcpServers": {
            "oura": {
                "command": desktop_command,
                "args": desktop_args,
            }
        }
    }
    out += [
        "── Claude Desktop ──────────────────────────────────────────",
        "",
        f"  файл: {desktop}  ({_found(desktop)})",
        "",
        "  Вставь в него — или влей в существующий mcpServers:",
        "",
    ]
    out += ["  " + line for line in json.dumps(entry, indent=2).splitlines()]
    out += [""]

    cursor = Path.home() / ".cursor/mcp.json"
    out += [
        "── Cursor ──────────────────────────────────────────────────",
        "",
        f"  файл: {cursor}  ({_found(cursor)})",
        "  формат тот же, что у Claude Desktop выше",
        "",
        "Ничего из перечисленного не изменено — это только текст для вставки.",
    ]
    return "\n".join(out)


def run(settings: Settings, project_root: Path | None) -> int:
    print(render(settings, project_root))
    return 0
