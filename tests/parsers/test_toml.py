"""Tests for conda_workspaces.parsers.toml (conda.toml parser and helpers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conda_workspaces.exceptions import WorkspaceParseError
from conda_workspaces.models import Feature, MatchSpec
from conda_workspaces.parsers.toml import (
    CondaTomlParser,
    _parse_channels,
    _parse_conda_deps,
    _parse_environment,
    _parse_pypi_deps,
    _parse_target_overrides,
)


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("conda.toml", True),
        ("pixi.toml", False),
        ("pyproject.toml", False),
    ],
    ids=["conda-toml", "pixi-toml", "pyproject-toml"],
)
def test_can_handle(filename, expected):
    parser = CondaTomlParser()
    assert parser.can_handle(Path(filename)) is expected


@pytest.mark.parametrize(
    "write_file, expected",
    [
        (True, True),
        (False, False),
    ],
    ids=["file-exists", "file-missing"],
)
def test_has_workspace(tmp_path, write_file, expected):
    path = tmp_path / "conda.toml"
    if write_file:
        path.write_text(
            '[workspace]\nname = "my-workspace"\nchannels'
            ' = ["conda-forge"]\nplatforms = ["linux-64"]\n',
            encoding="utf-8",
        )
    parser = CondaTomlParser()
    assert parser.has_workspace(path) is expected


@pytest.mark.parametrize(
    "content",
    [
        '[dependencies]\npython = ">=3.10"\n',
        "[invalid\n",
    ],
    ids=["no-workspace-key", "invalid-toml"],
)
def test_has_workspace_returns_false(tmp_path, content):
    """Files without a valid [workspace] table should return False."""
    path = tmp_path / "conda.toml"
    path.write_text(content, encoding="utf-8")
    parser = CondaTomlParser()
    assert parser.has_workspace(path) is False


def test_parse(tmp_path):
    content = """\
[workspace]
name = "my-workspace"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"
"""
    path = tmp_path / "conda.toml"
    path.write_text(content, encoding="utf-8")

    parser = CondaTomlParser()
    config = parser.parse(path)
    assert config.name == "my-workspace"
    assert config.manifest_path == str(path)
    default = config.features["default"]
    assert "python" in default.conda_dependencies


@pytest.mark.parametrize(
    "raw, expected_names",
    [
        (["conda-forge"], ["conda-forge"]),
        (["conda-forge", "bioconda"], ["conda-forge", "bioconda"]),
        ([{"channel": "nvidia"}], ["nvidia"]),
        (["conda-forge", {"channel": "nvidia"}], ["conda-forge", "nvidia"]),
        ([], []),
    ],
    ids=["single-str", "two-strs", "single-dict", "mixed", "empty"],
)
def test_parse_channels(raw, expected_names):
    channels = _parse_channels(raw)
    assert [ch.canonical_name for ch in channels] == expected_names


@pytest.mark.parametrize(
    "raw, expected_name",
    [
        ({"python": ">=3.10"}, "python"),
        ({"numpy": {"version": ">=1.24"}}, "numpy"),
        ({"gcc": {"version": ">=12", "build": "h*"}}, "gcc"),
        ({"pkg": 42}, "pkg"),
    ],
    ids=["str-spec", "dict-version", "dict-version-build", "other-type"],
)
def test_parse_conda_deps(raw, expected_name):
    deps = _parse_conda_deps(raw)
    assert expected_name in deps
    assert isinstance(deps[expected_name], MatchSpec)


def test_parse_conda_deps_empty():
    assert _parse_conda_deps({}) == {}


@pytest.mark.parametrize(
    "raw, key",
    [
        ({"requests": ">=2.28"}, "requests"),
        ({"flask": {"version": ">=3.0"}}, "flask"),
        ({"pkg": 1}, "pkg"),
    ],
    ids=["str-spec", "dict-version", "other-type"],
)
def test_parse_pypi_deps(raw, key):
    deps = _parse_pypi_deps(raw)
    assert key in deps
    assert deps[key].name == key


def test_parse_pypi_deps_empty():
    assert _parse_pypi_deps({}) == {}


@pytest.mark.parametrize(
    "raw, expected_features",
    [
        (["feat1", "feat2"], ["feat1", "feat2"]),
        ({"features": ["a"]}, ["a"]),
        ({"features": ["a"], "solve-group": "g"}, ["a"]),
    ],
    ids=["list", "dict-features", "dict-solve-group"],
)
def test_parse_environment(tmp_path, raw, expected_features):
    env = _parse_environment("myenv", raw, tmp_path / "conda.toml")
    assert env.name == "myenv"
    assert env.features == expected_features


def test_parse_environment_invalid_type(tmp_path):
    path = tmp_path / "conda.toml"
    with pytest.raises(WorkspaceParseError, match="expected list or dict, got str"):
        _parse_environment("myenv", "unexpected", path)


def test_parse_environment_no_default_feature(tmp_path):
    env = _parse_environment(
        "e", {"no-default-feature": True, "features": ["x"]}, tmp_path / "conda.toml"
    )
    assert env.no_default_feature is True


@pytest.mark.parametrize(
    "platform, dep_key, attr, pkg",
    [
        ("linux-64", "dependencies", "target_conda_dependencies", "gcc"),
        ("osx-arm64", "pypi-dependencies", "target_pypi_dependencies", "torch"),
    ],
    ids=["conda-deps", "pypi-deps"],
)
def test_parse_target_overrides(platform, dep_key, attr, pkg):
    feature = Feature(name="default")
    version = ">=12" if dep_key == "dependencies" else ">=2.0"
    target_data = {platform: {dep_key: {pkg: version}}}
    _parse_target_overrides(target_data, feature)
    result = getattr(feature, attr)
    assert platform in result
    assert pkg in result[platform]


def test_parse_target_overrides_empty():
    feature = Feature(name="default")
    _parse_target_overrides({}, feature)
    assert feature.target_conda_dependencies == {}
    assert feature.target_pypi_dependencies == {}


@pytest.mark.parametrize(
    "raw, field_name, expected_value",
    [
        (
            {"pkg": {"version": ">=1.0", "extras": ["extra1", "extra2"]}},
            "extras",
            ("extra1", "extra2"),
        ),
        (
            {"pkg": {"version": ">=1.0", "path": "/local/pkg"}},
            "path",
            "/local/pkg",
        ),
        (
            {"pkg": {"version": ">=1.0", "editable": True}},
            "editable",
            True,
        ),
        (
            {"pkg": {"git": "https://github.com/user/repo.git"}},
            "git",
            "https://github.com/user/repo.git",
        ),
        (
            {"pkg": {"url": "https://example.com/pkg-1.0.tar.gz"}},
            "url",
            "https://example.com/pkg-1.0.tar.gz",
        ),
    ],
    ids=["extras", "path", "editable", "git", "url"],
)
def test_parse_pypi_deps_dict_fields(raw, field_name, expected_value):
    deps = _parse_pypi_deps(raw)
    assert "pkg" in deps
    assert getattr(deps["pkg"], field_name) == expected_value


@pytest.mark.parametrize(
    "raw, type_name",
    [
        (42, "int"),
        (True, "bool"),
    ],
    ids=["int-type", "bool-type"],
)
def test_parse_environment_rejects_invalid_types(tmp_path, raw, type_name):
    path = tmp_path / "conda.toml"
    with pytest.raises(WorkspaceParseError, match=f"got {type_name}"):
        _parse_environment("badenv", raw, path)
