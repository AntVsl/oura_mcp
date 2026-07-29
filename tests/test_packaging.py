"""Метаданные пакета: согласованность версий.

Версия объявлена в четырёх местах — pyproject.toml, __init__.py, server.json и
дефолтный тег в compose.server.yml. Релиз сверяет тег только с pyproject
(release.yml), поэтому разъехаться остальные могут молча: пакет уедет в PyPI
правильной версии, а сервер сообщит о себе чужой и compose потянет прошлый
образ.
"""

import json
import re
import tomllib
from pathlib import Path

import my_oura_mcp

ROOT = Path(__file__).resolve().parents[1]


def declared_version() -> str:
    """Версия из pyproject — та, по которой CI сверяет тег релиза."""
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_package_version_matches_pyproject():
    assert my_oura_mcp.__version__ == declared_version()


def test_server_json_matches_pyproject():
    """server.json описывает сервер для реестра MCP и ссылается на артефакты."""
    manifest = json.loads((ROOT / "server.json").read_text())
    version = declared_version()

    assert manifest["version"] == version
    for package in manifest.get("packages", []):
        assert package["version"] == version, f"{package.get('identifier')} отстал"


def test_compose_default_tag_matches_pyproject():
    """Дефолтный тег в compose: с ним сервер поднимается без OURA_TAG в .env.

    Отставший тег — самая обидная из ошибок этого набора: всё разворачивается
    без единой жалобы, просто работает прошлая версия.
    """
    compose = (ROOT / "compose.server.yml").read_text()
    match = re.search(r"iican/oura-mcp:\$\{OURA_TAG:-([\d.]+)\}", compose)
    assert match, "не нашёл дефолтный тег образа в compose.server.yml"
    assert match.group(1) == declared_version()


def test_changelog_has_an_entry_for_this_version():
    """Выпускать версию без записи в CHANGELOG — терять историю решений."""
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert f"## [{declared_version()}]" in changelog
