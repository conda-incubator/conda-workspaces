"""Tests for conda_workspaces.manifests.toml (conda.toml parser and helpers)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from conda.exceptions import InvalidMatchSpec

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


def test_has_workspace(tmp_path):
    path = tmp_path / "conda.toml"
    path.write_text(
        '[workspace]\nname = "my-workspace"\nchannels'
        ' = ["conda-forge"]\nplatforms = ["linux-64"]\n',
        encoding="utf-8",
    )
    parser = CondaTomlParser()
    assert parser.has_workspace(path)


def test_has_workspace_returns_false_without_workspace(tmp_path):
    path = tmp_path / "conda.toml"
    path.write_text(
        '[dependencies]\npython = ">=3.10"\n',
        encoding="utf-8",
    )
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


def test_parse_error_redacts_malformed_dependency_credentials(tmp_path: Path) -> None:
    path = tmp_path / "conda.toml"
    path.write_text(
        """\
[workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = "https://user:LEAKME@example.test/["
""",
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceParseError) as error:
        CondaTomlParser().parse(path)

    assert "user" not in str(error.value)
    assert "LEAKME" not in str(error.value)


def test_parse_rejects_project_table(tmp_path):
    path = tmp_path / "conda.toml"
    path.write_text('[project]\nname = "pixi-only"\n', encoding="utf-8")

    with pytest.raises(WorkspaceParseError, match=r"No \[workspace\] table found"):
        CondaTomlParser().parse(path)


@pytest.mark.parametrize(
    "content",
    [
        'workspace = "bad"\n',
        "[workspace]\nchannels = [{ priority = 1 }]\n",
    ],
    ids=["workspace-not-table", "channel-missing-url"],
)
def test_parse_wraps_semantic_errors(tmp_path: Path, content: str) -> None:
    path = tmp_path / "conda.toml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(WorkspaceParseError):
        CondaTomlParser().parse(path)


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
    "url",
    [
        "https://user:password@packages.example.test/pkg-1.0-0.conda",
        "https://packages.example.test/t/secret/pkg-1.0-0.conda",
        "https://packages.example.test/%74/SENSITIVE-VALUE/pkg-1.0-0.conda",
        "https://packages.example.test/t%2FSENSITIVE-VALUE/pkg-1.0-0.conda",
        "https://packages.example.test/t%252FSENSITIVE-VALUE/pkg-1.0-0.conda",
        "HTTPS://user:SENSITIVE-VALUE@packages.example.test/pkg-1.0-0.conda",
        "https://packages.example.test/pkg-1.0-0.conda?token=secret",
        "https://packages.example.test/pkg-1.0-0.conda#secret",
    ],
    ids=[
        "basic-auth",
        "anaconda-token",
        "encoded-token-segment",
        "encoded-token-separator",
        "double-encoded-token-separator",
        "uppercase-basic-auth",
        "query",
        "fragment",
    ],
)
@pytest.mark.parametrize("field", ["url", "build"], ids=["url", "build"])
def test_match_spec_to_toml_rejects_credential_bearing_fields(
    url: str,
    field: str,
) -> None:
    spec = MatchSpec(name="pkg", **{field: url})

    with pytest.raises(
        InvalidMatchSpec,
        match="Configure authentication outside the manifest",
    ) as error:
        WorkspaceDependencyResolver.match_spec_to_toml(spec)

    assert url not in str(error.value)
    assert "SENSITIVE-VALUE" not in str(error.value)


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(
            MatchSpec("https://conda.anaconda.org/private::pkg"),
            id="prefix",
        ),
        pytest.param(
            MatchSpec(
                "pkg[channel='https://conda.anaconda.org/private']",
            ),
            id="quoted-bracket",
        ),
        pytest.param(
            MatchSpec(
                "pkg[channel=https://conda.anaconda.org/private]",
            ),
            id="unquoted-bracket",
        ),
        pytest.param(
            MatchSpec(
                name="pkg",
                channel="https://conda.anaconda.org/private",
            ),
            id="programmatic",
        ),
        pytest.param(
            MatchSpec("t/SENSITIVE-VALUE/private::pkg"),
            id="relative-token-prefix",
        ),
        pytest.param(
            MatchSpec("pkg[channel='t/SENSITIVE-VALUE/private']"),
            id="relative-token-bracket",
        ),
    ],
)
def test_match_spec_to_toml_normalizes_channel_url(spec: MatchSpec) -> None:
    value = WorkspaceDependencyResolver.match_spec_to_toml(spec)

    assert value["channel"] == "https://conda.anaconda.org/private"


def test_match_spec_to_toml_redacts_uppercase_channel_credentials() -> None:
    spec = MatchSpec("HTTPS://user:password@repo.example.test/t/secret/private::pkg")

    value = WorkspaceDependencyResolver.match_spec_to_toml(spec)

    assert value["channel"] == "HTTPS://repo.example.test/private"


def test_match_spec_to_toml_error_redacts_relative_channel_token() -> None:
    spec = MatchSpec("t/SENSITIVE-VALUE/private::pkg[subdir='custom-64']")

    with pytest.raises(InvalidMatchSpec) as error:
        WorkspaceDependencyResolver.match_spec_to_toml(spec)

    assert "SENSITIVE-VALUE" not in str(error.value)


def test_parse_channels_debug_log_redacts_relative_channel_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("DEBUG"):
        parse_channels([{"channel": "t/SENSITIVE-VALUE/private", "priority": 1}])

    assert "SENSITIVE-VALUE" not in caplog.text
    assert "https://conda.anaconda.org/private" in caplog.text


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


def test_parse_environment_dependencies(tmp_path):
    env = parse_environment(
        "qa",
        {
            "dependencies": {"coverage": ">=7"},
            "pypi-dependencies": {"pytest-plugin": ">=1"},
            "target": {
                "linux-64": {
                    "dependencies": {"gcc": ">=12"},
                    "pypi-dependencies": {"uvloop": ">=0.21"},
                }
            },
        },
        tmp_path / "conda.toml",
    )

    assert env.conda_dependencies == {"coverage": MatchSpec("coverage >=7")}
    assert env.pypi_dependencies["pytest-plugin"].spec == ">=1"
    assert env.target_conda_dependencies["linux-64"] == {"gcc": MatchSpec("gcc >=12")}
    assert env.target_pypi_dependencies["linux-64"]["uvloop"].spec == ">=0.21"


def test_parse_environment_target_workspace_dependency(tmp_path):
    path = tmp_path / "conda.toml"
    path.write_text(
        """\
[workspace]
name = "environment-target"
channels = ["conda-forge"]
platforms = ["linux-64"]

[workspace.dependencies]
numpy = { version = ">=2", channel = "conda-forge" }

[environments.qa.target.linux-64.dependencies]
numpy = { workspace = true, build = "py*" }
""",
        encoding="utf-8",
    )

    environment = CondaTomlParser().parse(path).environments["qa"]
    dependency = environment.target_conda_dependencies["linux-64"]["numpy"]
    assert str(dependency.version) == ">=2"
    assert dependency.get_raw_value("build") == "py*"
    assert dependency.get_raw_value("channel") == (
        "https://conda.anaconda.org/conda-forge"
    )


def test_parse_legacy_default_feature_preserves_v1_replacement_semantics(tmp_path):
    path = tmp_path / "conda.toml"
    path.write_text(
        """\
[workspace]
name = "legacy-default"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = "*"

[feature.default.dependencies]
numpy = "*"
""",
        encoding="utf-8",
    )

    config = CondaTomlParser().parse(path)

    assert set(config.features["default"].conda_dependencies) == {"numpy"}


def test_v1_schema_allows_legacy_default_feature() -> None:
    schema_path = Path(__file__).parents[2] / "schema" / "conda-toml-1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert "propertyNames" not in schema["properties"]["feature"]
    assert schema["properties"]["feature"]["additionalProperties"] == {
        "$ref": "#/$defs/feature"
    }


@pytest.mark.parametrize(
    "table",
    [
        "target.linux-64.dependencies",
        "feature.build.target.linux-64.dependencies",
        "environments.qa.target.linux-64.dependencies",
    ],
    ids=["default", "feature", "environment"],
)
def test_parse_target_dependency_error_uses_full_table_path(tmp_path, table):
    path = tmp_path / "conda.toml"
    path.write_text(
        f"""\
[workspace]
name = "target-path"
channels = ["conda-forge"]
platforms = ["linux-64"]

[workspace.dependencies]
numpy = ">=2"

[{table}]
numpy = {{ workspace = false }}
""",
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceParseError, match=re.escape(f"[{table}].numpy")):
        CondaTomlParser().parse(path)


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
