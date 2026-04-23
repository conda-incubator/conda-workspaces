"""Tests for conda_workspaces.manifests.pixi_toml (pixi.toml parser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conda_workspaces.exceptions import WorkspaceParseError
from conda_workspaces.manifests.pixi_toml import PixiTomlParser


@pytest.fixture
def parser():
    return PixiTomlParser()


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("pixi.toml", True),
        ("pyproject.toml", False),
        ("conda.toml", False),
    ],
    ids=["pixi-toml", "pyproject-toml", "conda-toml"],
)
def test_can_handle(parser, filename, expected):
    assert parser.can_handle(Path(filename)) is expected


def test_has_workspace(parser, sample_pixi_toml):
    assert parser.has_workspace(sample_pixi_toml)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(None, id="missing-file"),
        pytest.param("{{invalid toml", id="bad-toml"),
    ],
)
def test_has_workspace_false(parser, tmp_path, content):
    path = tmp_path / "pixi.toml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert not parser.has_workspace(path)


def test_parse_basic(parser, sample_pixi_toml):
    config = parser.parse(sample_pixi_toml)
    assert config.name == "test-project"
    assert config.version == "0.1.0"
    assert len(config.channels) == 1
    assert config.channels[0].canonical_name == "conda-forge"
    assert "linux-64" in config.platforms
    assert "osx-arm64" in config.platforms


def test_parse_default_dependencies(parser, sample_pixi_toml):
    config = parser.parse(sample_pixi_toml)
    default = config.features["default"]
    assert "python" in default.conda_dependencies
    assert str(default.conda_dependencies["python"].version) == ">=3.10"
    assert "numpy" in default.conda_dependencies


def test_parse_features(parser, sample_pixi_toml):
    config = parser.parse(sample_pixi_toml)
    assert "test" in config.features
    assert "docs" in config.features
    test_feat = config.features["test"]
    assert "pytest" in test_feat.conda_dependencies


def test_parse_environments(parser, sample_pixi_toml):
    config = parser.parse(sample_pixi_toml)
    assert "default" in config.environments
    assert "test" in config.environments
    assert "docs" in config.environments
    test_env = config.environments["test"]
    assert test_env.features == ["test"]


def test_parse_with_targets(tmp_path):
    content = """\
[workspace]
name = "target-test"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"

[target.linux-64.dependencies]
gcc = ">=12"
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")

    parser = PixiTomlParser()
    config = parser.parse(path)
    default = config.features["default"]
    assert "linux-64" in default.target_conda_dependencies
    assert "gcc" in default.target_conda_dependencies["linux-64"]


def test_parse_with_pypi_deps(tmp_path):
    content = """\
[workspace]
name = "pypi-test"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"

[pypi-dependencies]
requests = ">=2.28"
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")

    parser = PixiTomlParser()
    config = parser.parse(path)
    default = config.features["default"]
    assert "requests" in default.pypi_dependencies


def test_parse_activation(tmp_path):
    content = """\
[workspace]
name = "activation-test"
channels = ["conda-forge"]
platforms = ["linux-64"]

[activation]
scripts = ["setup.sh"]

[activation.env]
MY_VAR = "hello"
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")

    parser = PixiTomlParser()
    config = parser.parse(path)
    default = config.features["default"]
    assert default.activation_scripts == ["setup.sh"]
    assert default.activation_env == {"MY_VAR": "hello"}


def test_parse_environment_as_list(tmp_path):
    content = """\
[workspace]
name = "list-env-test"
channels = ["conda-forge"]
platforms = ["linux-64"]

[environments]
dev = ["test", "lint"]
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")

    parser = PixiTomlParser()
    config = parser.parse(path)
    env = config.environments["dev"]
    assert env.features == ["test", "lint"]


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("{{invalid toml", id="bad-toml"),
        pytest.param('[dependencies]\npython = ">=3.10"\n', id="no-workspace-table"),
    ],
)
def test_parse_error(parser, tmp_path, content):
    """Malformed TOML or missing [workspace] raises WorkspaceParseError."""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(WorkspaceParseError):
        parser.parse(path)


def test_parse_system_requirements(tmp_path):
    """Top-level system-requirements are parsed into the default feature."""
    content = """\
[workspace]
name = "sysreq-test"
channels = ["conda-forge"]
platforms = ["linux-64"]

[system-requirements]
linux = "5.10"
cuda = "12.0"
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")
    config = PixiTomlParser().parse(path)
    default = config.features["default"]
    assert default.system_requirements == {"linux": "5.10", "cuda": "12.0"}


def test_parse_feature_system_requirements(tmp_path):
    """Feature-level system-requirements are parsed."""
    content = """\
[workspace]
name = "feat-sysreq"
channels = ["conda-forge"]
platforms = ["linux-64"]

[feature.gpu.dependencies]
cudatoolkit = "*"

[feature.gpu.system-requirements]
cuda = "11.8"
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")
    config = PixiTomlParser().parse(path)
    assert config.features["gpu"].system_requirements == {"cuda": "11.8"}


def test_parse_feature_activation(tmp_path):
    """Feature-level activation scripts and env vars are parsed."""
    content = """\
[workspace]
name = "feat-act"
channels = ["conda-forge"]
platforms = ["linux-64"]

[feature.dev.activation]
scripts = ["dev-setup.sh"]

[feature.dev.activation.env]
DEBUG = "1"
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")
    config = PixiTomlParser().parse(path)
    dev = config.features["dev"]
    assert dev.activation_scripts == ["dev-setup.sh"]
    assert dev.activation_env == {"DEBUG": "1"}


def test_parse_returns_plain_str_values(tmp_path):
    """Names/platforms/feature names are plain ``str``, not tomlkit subclasses.

    Regression guard for a lockfile-write failure: ``tomlkit.loads``
    returns ``tomlkit.items.String`` instances (a ``str`` subclass).
    YAML serialisation uses exact-type dispatch on dict keys, so any
    ``tomlkit`` string that reaches ``export`` as a key (platform
    or environment name) raises ``TypeError: Object of type String is
    not YAML serializable``.  Normalising at the parser keeps
    everything downstream oblivious to the TOML backend.
    """
    content = """\
[workspace]
name = "tomlkit-bleed"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"

[feature.gpu]
platforms = ["linux-64"]

[environments]
cuda = ["gpu"]
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")
    config = PixiTomlParser().parse(path)

    for platform in config.platforms:
        assert type(platform) is str, type(platform).__name__
    for env_name in config.environments:
        assert type(env_name) is str, type(env_name).__name__
    for feat_name, feature in config.features.items():
        assert type(feat_name) is str, type(feat_name).__name__
        for platform in feature.platforms:
            assert type(platform) is str, type(platform).__name__
