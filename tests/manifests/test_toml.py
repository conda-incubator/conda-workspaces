"""Tests for conda_workspaces.manifests.toml (conda.toml parser and helpers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conda_workspaces.exceptions import WorkspaceParseError
from conda_workspaces.manifests.toml import (
    CondaTomlParser,
    WorkspaceDependencyResolver,
    parse_channels,
    parse_environment,
    parse_pypi_dependencies,
    parse_target_overrides,
)
from conda_workspaces.models import Feature, MatchSpec


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
    "table, feature_name, platform, expected_build",
    [
        ("dependencies", "default", None, None),
        ("feature.build.dependencies", "build", None, "h*"),
        ("target.linux-64.dependencies", "default", "linux-64", "py*"),
        ("feature.build.target.linux-64.dependencies", "build", "linux-64", "h*"),
    ],
    ids=["default", "feature", "target", "feature-target"],
)
def test_parse_workspace_dependency_inheritance_tables(
    tmp_path,
    table,
    feature_name,
    platform,
    expected_build,
):
    build_part = f', build = "{expected_build}"' if expected_build else ""
    content = f"""\
[workspace]
name = "workspace-deps"
channels = ["conda-forge"]
platforms = ["linux-64"]

[workspace.dependencies]
numpy = {{ version = "1.*", channel = "conda-forge" }}

[{table}]
numpy = {{ workspace = true{build_part} }}
"""
    path = tmp_path / "conda.toml"
    path.write_text(content, encoding="utf-8")

    config = CondaTomlParser().parse(path)
    assert str(config.workspace_dependencies["numpy"].version) == "1.*"

    feature = config.features[feature_name]
    if platform is None:
        dep = feature.conda_dependencies["numpy"]
    else:
        dep = feature.target_conda_dependencies[platform]["numpy"]
    assert str(dep.version) == "1.*"
    assert dep.get_raw_value("channel") == "https://conda.anaconda.org/conda-forge"
    assert dep.get_raw_value("build") == expected_build


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
    channels = parse_channels(raw)
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
    deps = WorkspaceDependencyResolver().parse_dependency_table(raw)
    assert expected_name in deps
    assert isinstance(deps[expected_name], MatchSpec)


@pytest.mark.parametrize(
    "root_spec, inherited_spec, raw_field, expected_value",
    [
        ("1.*", {"workspace": True}, "version", "1.*"),
        (
            {"version": ">=24", "channel": "conda-forge"},
            {"workspace": True},
            "channel",
            "https://conda.anaconda.org/conda-forge",
        ),
        ({"version": ">=24"}, {"workspace": True, "build": "py*"}, "build", "py*"),
        (
            {"version": ">=24"},
            {"workspace": True, "build-number": ">=2"},
            "build_number",
            ">=2",
        ),
        (
            {"version": ">=24"},
            {"workspace": True, "subdir": "linux-64"},
            "subdir",
            "linux-64",
        ),
        (
            {"version": ">=24"},
            {"workspace": True, "file-name": "pkg-1.0-0.tar.bz2"},
            "fn",
            "pkg-1.0-0.tar.bz2",
        ),
        (
            {"version": ">=24"},
            {"workspace": True, "license-family": "BSD"},
            "license_family",
            "bsd",
        ),
        (
            {"version": ">=24"},
            {"workspace": True, "features": ["feature-a", "feature-b"]},
            "features",
            {"feature-a", "feature-b"},
        ),
        (
            {"version": ">=24"},
            {"workspace": True, "track-features": ["accelerated"]},
            "track_features",
            {"accelerated"},
        ),
    ],
    ids=[
        "string-root",
        "root-channel",
        "override-build",
        "override-build-number",
        "override-subdir",
        "override-file-name",
        "override-license-family",
        "override-features",
        "override-track-features",
    ],
)
def test_parse_conda_deps_with_workspace_inheritance(
    tmp_path,
    root_spec,
    inherited_spec,
    raw_field,
    expected_value,
):
    resolver = WorkspaceDependencyResolver(
        workspace_dependencies={"pkg": root_spec},
        path=tmp_path / "conda.toml",
    )
    deps = resolver.parse_dependency_table({"pkg": inherited_spec})
    value = deps["pkg"].get_raw_value(raw_field)

    if isinstance(value, frozenset):
        value = set(value)
    assert value == expected_value


@pytest.mark.parametrize(
    "raw, workspace_dependencies, match",
    [
        (
            {"numpy": {"workspace": True}},
            {},
            "no workspace dependency named 'numpy'",
        ),
        (
            {"numpy": {"workspace": False}},
            {"numpy": "1.*"},
            "`workspace` can only be true",
        ),
        (
            {"numpy": {"workspace": True, "version": ">=2"}},
            {"numpy": "1.*"},
            "cannot set both `workspace = true` and `version`",
        ),
        (
            {"numpy": {"workspace": True, "path": "../numpy"}},
            {"numpy": "1.*"},
            "unsupported by conda-workspaces inheritance: path",
        ),
        (
            {"numpy": {"workspace": True, "unsupported": "value"}},
            {"numpy": "1.*"},
            "unsupported field\\(s\\): unsupported",
        ),
        (
            {"numpy": {"workspace": True}},
            {"numpy": {"version": "1.*", "path": "../numpy"}},
            "unsupported by conda-workspaces inheritance: path",
        ),
        (
            {"numpy": {"workspace": True}},
            {"numpy": {"version": "1.*", "unsupported": "value"}},
            "unsupported field\\(s\\): unsupported",
        ),
        (
            {"numpy": {"workspace": True}},
            {"numpy": {"workspace": True}},
            "\\[workspace.dependencies\\].numpy cannot use `workspace = true`",
        ),
    ],
    ids=[
        "missing-root",
        "workspace-false",
        "version-restated",
        "source-field",
        "unsupported-field",
        "root-source-field",
        "root-unsupported-field",
        "root-workspace-inheritance",
    ],
)
def test_parse_conda_deps_workspace_inheritance_errors(
    tmp_path,
    raw,
    workspace_dependencies,
    match,
):
    with pytest.raises(WorkspaceParseError, match=match):
        resolver = WorkspaceDependencyResolver(
            workspace_dependencies=workspace_dependencies,
            path=tmp_path / "conda.toml",
        )
        resolver.parse_dependency_table(raw)


def test_parse_conda_deps_empty():
    assert WorkspaceDependencyResolver().parse_dependency_table({}) == {}


def test_parse_pypi_deps_empty():
    assert parse_pypi_dependencies({}) == {}


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
    deps = parse_pypi_dependencies(raw)
    assert key in deps
    assert deps[key].name == key


@pytest.mark.parametrize(
    "raw, expected_features",
    [
        (["feat1", "feat2"], ["feat1", "feat2"]),
        ({"features": ["a"]}, ["a"]),
    ],
    ids=["list", "dict-features"],
)
def test_parse_environment(tmp_path, raw, expected_features):
    env = parse_environment("myenv", raw, tmp_path / "conda.toml")
    assert env.name == "myenv"
    assert env.features == expected_features


def test_parse_environment_invalid_type(tmp_path):
    path = tmp_path / "conda.toml"
    with pytest.raises(WorkspaceParseError, match="expected list or dict, got str"):
        parse_environment("myenv", "unexpected", path)


def test_parse_environment_no_default_feature(tmp_path):
    env = parse_environment(
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
    if dep_key == "dependencies":
        version = ">=12"
    else:
        version = ">=2.0"
    target_data = {platform: {dep_key: {pkg: version}}}
    parse_target_overrides(target_data, feature)
    result = getattr(feature, attr)
    assert platform in result
    assert pkg in result[platform]


def test_parse_target_system_requirements_rejected():
    feature = Feature(name="default")
    target_data = {"linux-64": {"system-requirements": {"libc": "2.28"}}}
    with pytest.raises(ValueError, match="not supported"):
        parse_target_overrides(target_data, feature)


def test_parse_target_overrides_empty():
    feature = Feature(name="default")
    parse_target_overrides({}, feature)
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
    deps = parse_pypi_dependencies(raw)
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
        parse_environment("badenv", raw, path)
