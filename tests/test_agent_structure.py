"""Проверяем артефакты, которыми пользуются кодовые агенты.

Инструкции и skills не исполняются при обычном запуске сервера, поэтому без
этого теста легко закоммитить битую ссылку, TODO из шаблона или несовпадающее
имя skill. Тогда агент молча не найдёт нужный процесс именно во время правки.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


def _frontmatter(path: Path) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---", path.read_text(), re.S)
    assert match, f"{path}: нет YAML frontmatter"
    return dict(re.findall(r"^(name|description):\s*(.+)$", match.group(1), re.M))


def test_agent_guide_and_workflow_templates_exist():
    guide = (ROOT / "AGENTS.md").read_text()
    assert "Claude.ai and ChatGPT" in guide
    assert "uv run pytest -q" in guide
    for filename in ("README.md", "task-template.md", "review-template.md"):
        assert (ROOT / "docs" / "agents" / filename).is_file()


def test_every_repository_skill_has_complete_frontmatter():
    for skill_file in SKILLS.glob("*/SKILL.md"):
        frontmatter = _frontmatter(skill_file)
        assert frontmatter["name"] == skill_file.parent.name
        assert "TODO" not in skill_file.read_text(), skill_file


def test_codex_skill_has_ui_metadata_and_client_neutral_oura_skill():
    metadata = (SKILLS / "oura-mcp-maintenance" / "agents" / "openai.yaml").read_text()
    assert 'display_name: "Oura MCP Maintenance"' in metadata
    assert "$oura-mcp-maintenance" in metadata
    assert "Codex" in (SKILLS / "oura" / "SKILL.md").read_text()
