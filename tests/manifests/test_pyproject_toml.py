"""Tests for conda_workspaces.manifests.pyproject_toml."""

from __future__ import annotations

from pathlib import Path

import pytest

from conda_workspaces.exceptions import WorkspaceParseError
from conda_workspaces.manifests.pyproject_toml import PyprojectTomlParser


@pytest.fixture
def parser():
    return PyprojectTomlParser()


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("pyproject.toml", True),
        ("pixi.toml", False),
        ("conda.toml", False),
    ],
    ids=["pyproject-toml", "pixi-toml", "conda-toml"],
)
def test_can_handle(parser, filename, expected):
    assert parser.can_handle(Path(filename)) is expected


def test_has_workspace(parser, sample_pyproject_toml):
    assert parser.has_workspace(sample_pyproject_toml)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param('[project]\nname = "foo"\n', id="no-pixi-table"),
        pytest.param(None, id="missing-file"),
        pytest.param("{{invalid toml", id="bad-toml"),
    ],
)
def test_has_workspace_false(parser, tmp_path, content):
    path = tmp_path / "pyproject.toml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert not parser.has_workspace(path)


def test_parse_basic(parser, sample_pyproject_toml):
    config = parser.parse(sample_pyproject_toml)
    assert config.name == "my-project"
    assert len(config.channels) == 1
    assert "linux-64" in config.platforms


def test_parse_dependencies(parser, sample_pyproject_toml):
    config = parser.parse(sample_pyproject_toml)
    default = config.features["default"]
    assert "python" in default.conda_dependencies
    assert str(default.conda_dependencies["python"].version) == ">=3.11"


def test_parse_workspace_dependency_inheritance(parser, tmp_path: Path):
    content = """\
[project]
name = "workspace-deps"

[tool.conda.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.conda.workspace.dependencies]
numpy = "1.*"

[tool.conda.dependencies]
numpy = { workspace = true }
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")

    config = parser.parse(path)
    assert str(config.workspace_dependencies["numpy"].version) == "1.*"
    assert str(config.features["default"].conda_dependencies["numpy"].version) == "1.*"


def test_parse_features(parser, sample_pyproject_toml):
    config = parser.parse(sample_pyproject_toml)
    assert "test" in config.features
    test_feat = config.features["test"]
    assert "pytest" in test_feat.conda_dependencies
    assert "pytest-cov" in test_feat.conda_dependencies


def test_parse_environments(parser, sample_pyproject_toml):
    config = parser.parse(sample_pyproject_toml)
    assert "test" in config.environments
    test_env = config.environments["test"]
    assert test_env.features == ["test"]


@pytest.mark.parametrize(
    "table_key, dep_key",
    [
        ("tool.conda", "tool.conda"),
    ],
    ids=["conda-table"],
)
def test_alternative_tables(parser, tmp_path, table_key, dep_key):
    content = f"""\
[project]
name = "alt-table-project"

[{table_key}.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[{dep_key}.dependencies]
python = ">=3.12"
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")

    assert parser.has_workspace(path)
    config = parser.parse(path)
    assert config.name == "alt-table-project"
    default = config.features["default"]
    assert "python" in default.conda_dependencies


@pytest.mark.parametrize(
    "content",
    [
        pytest.param('[project]\nname = "foo"\n', id="no-workspace-table"),
        pytest.param("{{invalid toml", id="bad-toml"),
    ],
)
def test_parse_error(parser, tmp_path, content):
    """Missing workspace or malformed TOML raises WorkspaceParseError."""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(WorkspaceParseError):
        parser.parse(path)


def test_conda_table_priority_over_pixi(parser, tmp_path):
    """[tool.conda] should win over [tool.pixi]."""
    content = """\
[project]
name = "priority-test"

[tool.conda.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.conda.dependencies]
python = ">=3.12"

[tool.pixi.workspace]
channels = ["defaults"]
platforms = ["win-64"]

[tool.pixi.dependencies]
python = ">=3.10"
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")

    config = parser.parse(path)
    # Should use conda table (>=3.12), not pixi (>=3.10)
    default = config.features["default"]
    assert str(default.conda_dependencies["python"].version) == ">=3.12"


def test_parse_activation(parser, tmp_path):
    """Activation scripts and env vars are parsed."""
    content = """\
[project]
name = "act-test"

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.pixi.dependencies]
python = ">=3.10"

[tool.pixi.activation]
scripts = ["setup.sh"]

[tool.pixi.activation.env]
MY_VAR = "hello"
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    config = parser.parse(path)
    default = config.features["default"]
    assert default.activation_scripts == ["setup.sh"]
    assert default.activation_env == {"MY_VAR": "hello"}


def test_parse_system_requirements(parser, tmp_path):
    """System requirements are parsed."""
    content = """\
[project]
name = "sysreq-test"

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.pixi.system-requirements]
linux = "5.10"
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    config = parser.parse(path)
    default = config.features["default"]
    assert default.system_requirements == {"linux": "5.10"}


def test_parse_feature_activation(parser, tmp_path):
    """Feature-level activation in pyproject.toml."""
    content = """\
[project]
name = "feat-act"

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.pixi.feature.dev.dependencies]
python = ">=3.10"

[tool.pixi.feature.dev.activation]
scripts = ["dev.sh"]

[tool.pixi.feature.dev.activation.env]
DEV = "1"
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    config = parser.parse(path)
    dev = config.features["dev"]
    assert dev.activation_scripts == ["dev.sh"]
    assert dev.activation_env == {"DEV": "1"}


def test_parse_feature_system_requirements(parser, tmp_path):
    """Feature-level system-requirements in pyproject.toml."""
    content = """\
[project]
name = "feat-sysreq"

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.pixi.feature.gpu.dependencies]
cudatoolkit = "*"

[tool.pixi.feature.gpu.system-requirements]
cuda = "11.8"
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    config = parser.parse(path)
    assert config.features["gpu"].system_requirements == {"cuda": "11.8"}


@pytest.mark.parametrize(
    "tool_table",
    [
        pytest.param("tool.conda", id="tool-conda"),
        pytest.param("tool.pixi", id="tool-pixi"),
    ],
)
def test_parse_rich_platform_system_requirements(parser, tmp_path, tool_table):
    """Pixi-style rich platform system requirements in pyproject.toml."""
    content = f"""\
[project]
name = "rich-platform-sysreq"

[{tool_table}.workspace]
channels = ["conda-forge"]
platforms = [
  "osx-arm64",
  {{ platform = "linux-64", libc = "2.28" }},
]
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    config = parser.parse(path)
    assert config.platforms == ["osx-arm64", "linux-64"]
    assert config.platform_system_requirements == {"linux-64": {"glibc": "2.28"}}
