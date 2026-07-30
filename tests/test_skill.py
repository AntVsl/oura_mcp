"""Навык и рецепты: ссылаются ли они на то, что существует.

Рецепты — это инструкции модели, и врут они тихо: модель послушно запросит
`resting_hr`, не найдёт его и молча обойдётся без. Проверять их некому, кроме
теста, потому что при обычном прогоне markdown никто не исполняет.

Первый же прогон этой проверки нашёл четыре выдуманных имени поля.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "oura"


def skill_files() -> list[Path]:
    return sorted(SKILL_DIR.rglob("*.md"))


def known_names() -> set[str]:
    """Всё, на что рецептам законно ссылаться: поля сводок и имена инструментов."""
    shaping = (ROOT / "src" / "my_oura_mcp" / "shaping.py").read_text()
    tools = (ROOT / "src" / "my_oura_mcp" / "tools.py").read_text()

    names = set(re.findall(r'"([a-z_0-9]+)":', shaping))
    # \w включает цифры — без этого `get_spo2` обрезается до `get_spo`.
    names |= set(re.findall(r"async def (get_\w+)", tools))
    # Аргументы инструментов и общие ключи ответа.
    names |= {"days_back", "start_date", "end_date", "raw", "stats", "trend_per_week"}
    return names


def test_skill_exists():
    assert (SKILL_DIR / "SKILL.md").exists()
    assert len(skill_files()) > 1, "рецепты не найдены"


def test_frontmatter_has_name_and_description():
    """Без этих полей навык не подхватится."""
    head = (SKILL_DIR / "SKILL.md").read_text().split("---")[1]
    assert re.search(r"^name:\s*\S+", head, re.M)
    assert re.search(r"^description:\s*\S+", head, re.M)


def test_recipes_reference_real_fields_and_tools():
    """Выдуманное имя поля хуже отсутствия рецепта: модель промолчит о промахе."""
    known = known_names()
    unknown: dict[str, set[str]] = {}
    for path in skill_files():
        # Только имена в обратных кавычках — прозой рецепты писать не запрещено.
        used = set(re.findall(r"`([a-z_][a-z_0-9]*)`", path.read_text()))
        missing = used - known
        if missing:
            unknown[path.name] = missing
    assert not unknown, f"нет в коде: {unknown}"


def test_recipes_are_linked_from_the_skill():
    """Ненайденный рецепт — потраченная работа: модель его просто не увидит."""
    index = (SKILL_DIR / "SKILL.md").read_text()
    for path in SKILL_DIR.glob("recipes/*.md"):
        assert path.name in index, f"{path.name} не упомянут в SKILL.md"


def test_recipes_keep_the_medical_boundary():
    """Данные потребительские, и рецепты не должны звучать как диагноз."""
    for path in SKILL_DIR.glob("recipes/*.md"):
        text = path.read_text().lower()
        assert "watch for" in text, f"{path.name}: нет раздела с оговорками"
