"""Подсказка по настройке клиентов.

Смысл команды ровно один — подставить абсолютный путь. На относительном пути в
`claude mcp add` спотыкаются чаще всего, настолько, что это попало в README как
типичная ошибка. Всё остальное здесь — текст.

И главное свойство: команда ничего не записывает. Сосед дописывается в чужие
конфиги; цена ошибки там платится не нами — кривой merge ломает у человека уже
настроенные серверы.
"""

import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from my_oura_mcp import install
from my_oura_mcp.config import Settings

# resolve() разворачивает симлинки — на macOS /tmp это /private/tmp. Так и надо:
# в конфиг должен уехать настоящий путь, а не тот, что ведёт через ссылку.
ROOT = Path("/tmp/some/project")
RESOLVED = str(ROOT.resolve())


def settings(public_url: str | None = None) -> Settings:
    return Settings(
        mode="production",
        tz=ZoneInfo("Europe/Moscow"),
        client_id=None,
        client_secret=None,
        redirect_uri="http://localhost:8765/callback",
        token_store=Path("/tmp/t.json"),
        cache_db=Path("/tmp/c.db"),
        public_url=public_url,
    )


def test_path_is_absolute():
    """Ради этого команда и существует."""
    out = install.render(settings(), ROOT)
    assert f"--directory {RESOLVED}" in out
    assert "--directory ." not in out
    assert "--directory ~" not in out


def test_installed_package_uses_its_entrypoint_not_a_fake_project_root():
    out = install.render(settings(), None)
    assert "claude mcp add oura -- my-oura-mcp" in out
    assert "codex mcp add oura -- my-oura-mcp" in out
    assert "--directory" not in out


def test_claude_desktop_snippet_is_valid_json():
    """Невалидный JSON в подсказке хуже отсутствия подсказки."""
    out = install.render(settings(), ROOT)
    block = re.search(r"\{\n(?:.*\n)*?  \}", out)
    assert block, "не нашёл блок JSON"
    parsed = json.loads(re.sub(r"^  ", "", block.group(0), flags=re.M))
    assert parsed["mcpServers"]["oura"]["args"][1] == RESOLVED


def test_remote_setup_comes_first_when_deployed():
    """Если сервер развёрнут, конфиги править не надо вовсе — это главное."""
    out = install.render(settings("https://oura-mcp.lol"), ROOT)
    assert out.index("Add custom connector") < out.index("Claude Code")
    assert "https://oura-mcp.lol/mcp" in out


def test_local_setup_still_shown_when_deployed():
    """Локальный запуск остаётся рабочим вариантом и не должен пропадать."""
    out = install.render(settings("https://oura-mcp.lol"), ROOT)
    assert "claude mcp add oura --" in out
    assert "codex mcp add oura --" in out


def test_remote_setup_includes_codex_and_chatgpt():
    out = install.render(settings("https://oura-mcp.lol"), ROOT)
    assert "codex mcp add oura --url https://oura-mcp.lol/mcp" in out
    assert "--bearer-token-env-var OURA_MCP_TOKEN" in out
    assert "ChatGPT" in out


def test_no_remote_block_without_public_url():
    out = install.render(settings(), ROOT)
    assert "Add custom connector" not in out


def test_render_touches_nothing(tmp_path, monkeypatch):
    """Ничего не записывает — ни в чужие конфиги, ни куда-либо ещё."""
    monkeypatch.setenv("HOME", str(tmp_path))
    before = set(tmp_path.rglob("*"))
    install.render(settings(), ROOT)
    assert set(tmp_path.rglob("*")) == before


def test_says_plainly_that_nothing_was_changed():
    """Человек должен понимать, что от него ждут вставки руками."""
    assert "не изменено" in install.render(settings(), ROOT)
