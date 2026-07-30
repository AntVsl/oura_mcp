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
    """server.json описывает сервер для реестра MCP и ссылается на артефакты.

    Версия у пакетов задаётся по-разному, и это требование реестра, а не наш
    выбор: у OCI её полагается держать только в идентификаторе
    (`docker.io/owner/image:tag`), а поля `version` и `registryBaseUrl` там
    запрещены — публикация с ними отвергается с 400.
    """
    manifest = json.loads((ROOT / "server.json").read_text())
    version = declared_version()

    assert manifest["version"] == version
    for package in manifest.get("packages", []):
        identifier = package.get("identifier", "")
        if package.get("registryType") == "oci":
            assert "version" not in package, f"{identifier}: реестр запретит поле version"
            assert "registryBaseUrl" not in package, f"{identifier}: и registryBaseUrl тоже"
            assert identifier.endswith(f":{version}"), f"{identifier} отстал от {version}"
        else:
            assert package["version"] == version, f"{identifier} отстал"


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


def test_registry_description_fits_the_limit():
    """Реестр MCP отвергает описание длиннее 100 символов.

    Ошибка вылезает только при `mcp-publisher validate`, то есть в момент
    публикации, — а туда доходишь редко и обычно в спешке.
    """
    manifest = json.loads((ROOT / "server.json").read_text())
    assert len(manifest["description"]) <= 100, (
        f"{len(manifest['description'])} символов, реестр примет не больше 100"
    )


def test_readme_proves_pypi_ownership():
    """Реестр MCP требует токен `mcp-name:` в README пакета PyPI.

    Без него публикация падает с 400 — но уже ПОСЛЕ того, как версия ушла в
    PyPI и образ выложен, а значит чинится только новым релизом. Проверять
    заранее дешевле.
    """
    manifest = json.loads((ROOT / "server.json").read_text())
    readme = (ROOT / "README.md").read_text()
    assert f"mcp-name: {manifest['name']}" in readme


def test_release_workflow_labels_the_image_for_the_registry():
    """То же доказательство для образа — метка OCI, иначе публикация падает."""
    manifest = json.loads((ROOT / "server.json").read_text())
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert f"io.modelcontextprotocol.server.name={manifest['name']}" in workflow
