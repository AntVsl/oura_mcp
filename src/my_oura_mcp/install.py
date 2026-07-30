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


def render(settings: Settings, project_root: Path) -> str:
    """Собирает подсказку целиком. Отдельно от печати — чтобы тестировать."""
    out: list[str] = []
    remote = settings.public_url

    if remote:
        out += [
            "Сервер развёрнут публично, и это самый простой путь: конфиги",
            "править не нужно вообще.",
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
            "Ниже — локальный вариант, если сервер нужен рядом, без сети.",
            "",
        ]

    # Абсолютный путь: ради него всё и затевалось.
    root = project_root.resolve()
    uv = shutil.which("uv") or "uv"

    out += [
        "── Claude Code ─────────────────────────────────────────────",
        "",
        f"  claude mcp add oura -- {uv} --directory {root} run my-oura-mcp",
        "",
    ]

    desktop = _claude_desktop_config()
    entry = {
        "mcpServers": {
            "oura": {
                "command": uv,
                "args": ["--directory", str(root), "run", "my-oura-mcp"],
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


def run(settings: Settings, project_root: Path) -> int:
    print(render(settings, project_root))
    return 0
