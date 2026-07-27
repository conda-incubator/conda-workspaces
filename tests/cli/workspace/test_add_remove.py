"""Tests for conda_workspaces.cli.workspace.add and workspace.remove."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import tomlkit
from conda.exceptions import InvalidMatchSpec
from conda.utils import quote_for_shell
from conda_lockfiles.load_yaml import load_yaml

from conda_workspaces.cli.workspace.add import execute_add
from conda_workspaces.cli.workspace.dependencies import DependencyLocation
from conda_workspaces.cli.workspace.remove import execute_remove
from conda_workspaces.exceptions import CondaWorkspacesError
from conda_workspaces.manifests import find_parser
from conda_workspaces.resolver import ResolvedEnvironment

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console

    from conda_workspaces.models import WorkspaceConfig
    from tests.conftest import SnapshotTree

_DEFAULTS = {
    "manifest_file": None,
    "specs": [],
    "pypi": False,
    "feature": None,
    "environment": None,
    "platform": None,
    "no_install": False,
    # Most of the existing tests only care about the manifest edit; skip the
    # solve/install/lock pipeline by default and opt in where needed.
    "no_lockfile_update": True,
    "force_reinstall": False,
    "dry_run": False,
}


@pytest.fixture
def pixi_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a pixi.toml and chdir to tmp_path."""
    content = """\
[workspace]
name = "add-test"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"

[feature.test.dependencies]
pytest = ">=8.0"
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return path


@pytest.fixture
def pyproject_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a pyproject.toml with [tool.pixi] and chdir to tmp_path."""
    content = """\
[project]
name = "pp-test"

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.pixi.dependencies]
python = ">=3.10"
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return path


@pytest.mark.parametrize(
    "specs, expected_deps",
    [
        (["numpy"], {"numpy": "*"}),
        (["numpy >=1.24"], {"numpy": ">=1.24"}),
        (["numpy >=1.24", "pandas"], {"numpy": ">=1.24", "pandas": "*"}),
        (["python=*"], {"python": "*"}),
    ],
    ids=["bare-name", "with-version", "multiple", "clear-existing"],
)
def test_add_conda_deps_to_pixi_toml(
    pixi_toml: Path, specs: list[str], expected_deps: dict[str, str]
) -> None:
    args = make_args(_DEFAULTS, manifest_file=pixi_toml, specs=specs)
    result = execute_add(args)
    assert result == 0

    doc = tomlkit.loads(pixi_toml.read_text(encoding="utf-8"))
    for name, version in expected_deps.items():
        assert doc["dependencies"][name] == version


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (
            "conda-forge::numpy=2.3.1=py314h*",
            {
                "version": "2.3.1",
                "build": "py314h*",
                "channel": "conda-forge",
            },
        ),
        (
            "pkgs/main::numpy[build='py*']",
            {
                "build": "py*",
                "channel": "pkgs/main",
            },
        ),
        (
            (
                "numpy[version='>=2',build='py*',build_number=2,"
                "channel='conda-forge',subdir='linux-64',"
                "md5='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',"
                "sha256='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',"
                "url='https://example.com/numpy.conda',"
                "fn='numpy.conda',license='BSD-3-Clause',license_family='BSD',"
                "features='mkl blas',track_features='accelerated']"
            ),
            {
                "version": ">=2",
                "build": "py*",
                "build-number": "2",
                "channel": "conda-forge",
                "subdir": "linux-64",
                "md5": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "sha256": (
                    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                ),
                "url": "https://example.com/numpy.conda",
                "file-name": "numpy.conda",
                "license": "bsd-3-clause",
                "license-family": "bsd",
                "features": ["blas", "mkl"],
                "track-features": ["accelerated"],
            },
        ),
    ],
    ids=["channel-qualified", "multichannel-without-version", "all-supported-fields"],
)
def test_add_preserves_conda_matchspec_fields(
    pixi_toml: Path,
    spec: str,
    expected: dict[str, object],
) -> None:
    args = make_args(_DEFAULTS, manifest_file=pixi_toml, specs=[spec])
    assert execute_add(args) == 0

    doc = tomlkit.loads(pixi_toml.read_text(encoding="utf-8"))
    assert doc["dependencies"]["numpy"].unwrap() == expected


@pytest.mark.parametrize(
    ("filename", "namespace", "root_keys"),
    [
        ("conda.toml", "", ()),
        ("pixi.toml", "", ()),
        ("pyproject.toml", "tool.conda", ("tool", "conda")),
        ("pyproject.toml", "tool.pixi", ("tool", "pixi")),
    ],
    ids=["conda-toml", "pixi-toml", "pyproject-conda", "pyproject-pixi"],
)
def test_add_existing_conda_dependency_across_formats(
    tmp_path: Path,
    filename: str,
    namespace: str,
    root_keys: tuple[str, ...],
) -> None:
    prefix = f"{namespace}." if namespace else ""
    project = '[project]\nname = "add-test"\n\n' if namespace else ""
    path = tmp_path / filename
    path.write_text(
        f"""{project}[{prefix}workspace]
name = "add-test"
channels = ["conda-forge"]
platforms = ["linux-64"]

[{prefix}dependencies]
python = ">=3.10"
rich = {{ version = "1", build = "x", channel = "c", subdir = "linux-64" }}
""",
        encoding="utf-8",
    )

    before = path.read_text(encoding="utf-8")
    args = make_args(_DEFAULTS, manifest_file=path, specs=["rich"])
    assert execute_add(args) == 0
    assert path.read_text(encoding="utf-8") == before

    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["rich[version='>=14.1',channel='defaults']"],
    )
    assert execute_add(args) == 0

    root = tomlkit.loads(path.read_text(encoding="utf-8"))
    for key in root_keys:
        root = root[key]
    assert root["dependencies"]["rich"].unwrap() == {
        "version": ">=14.1",
        "channel": "defaults",
    }


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("python[when='linux']", "cannot be represented"),
        ("python[optional=true]", "cannot be represented"),
        ("python[target='x']", "cannot be represented"),
        ("python[subdir='custom-64']", "not a known conda platform"),
        ("python*", "package name is required"),
    ],
    ids=[
        "when",
        "optional",
        "target",
        "unknown-subdir",
        "wildcard-name",
    ],
)
def test_add_rejects_unrepresentable_conda_matchspec(
    pixi_toml: Path,
    spec: str,
    message: str,
) -> None:
    before = pixi_toml.read_text(encoding="utf-8")
    args = make_args(_DEFAULTS, manifest_file=pixi_toml, specs=["pandas>=2", spec])

    with pytest.raises(InvalidMatchSpec, match=message):
        execute_add(args)

    assert pixi_toml.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    ("kwargs", "target_keys"),
    [
        ({"feature": "test"}, ("feature", "test")),
        ({"environment": "test"}, ("environments", "test")),
    ],
    ids=["via-feature", "via-environment"],
)
def test_add_to_location(
    pixi_toml: Path,
    kwargs: dict,
    target_keys: tuple[str, ...],
) -> None:
    args = make_args(_DEFAULTS, manifest_file=pixi_toml, specs=["coverage"], **kwargs)
    execute_add(args)

    doc = tomlkit.loads(pixi_toml.read_text(encoding="utf-8"))
    target = doc
    for key in target_keys:
        target = target[key]
    assert target["dependencies"]["coverage"] == "*"
    if "environment" in kwargs:
        assert "coverage" not in doc["feature"]["test"]["dependencies"]


def test_add_pypi_deps(pixi_toml: Path) -> None:
    args = make_args(
        _DEFAULTS,
        manifest_file=pixi_toml,
        specs=["requests >=2.0"],
        pypi=True,
    )
    execute_add(args)

    doc = tomlkit.loads(pixi_toml.read_text(encoding="utf-8"))
    assert doc["pypi-dependencies"]["requests"] == ">=2.0"


def test_explicit_default_feature_targets_top_level(pixi_toml: Path) -> None:
    add_args = make_args(
        _DEFAULTS,
        manifest_file=pixi_toml,
        specs=["click"],
        feature="default",
    )
    assert execute_add(add_args) == 0

    doc = tomlkit.loads(pixi_toml.read_text(encoding="utf-8"))
    assert "default" not in doc["feature"]
    config = find_parser(pixi_toml).parse(pixi_toml)
    default = config.environments["default"]
    assert set(config.merged_conda_dependencies(default)) == {"python", "click"}

    remove_args = make_args(
        _DEFAULTS,
        manifest_file=pixi_toml,
        specs=["click"],
        feature="default",
    )
    assert execute_remove(remove_args) == 0
    config = find_parser(pixi_toml).parse(pixi_toml)
    default = config.environments["default"]
    assert set(config.merged_conda_dependencies(default)) == {"python"}


def test_add_to_pyproject(pyproject_toml: Path) -> None:
    args = make_args(
        _DEFAULTS,
        manifest_file=pyproject_toml,
        specs=["numpy >=1.24"],
    )
    execute_add(args)

    doc = tomlkit.loads(pyproject_toml.read_text(encoding="utf-8"))
    assert doc["tool"]["pixi"]["dependencies"]["numpy"] == ">=1.24"


@pytest.mark.parametrize(
    "specs, remaining",
    [
        (["python"], []),
        (["nonexistent"], ["python"]),
    ],
    ids=["remove-existing", "remove-missing-noop"],
)
def test_remove_from_pixi_toml(
    pixi_toml: Path, specs: list[str], remaining: list[str]
) -> None:
    args = make_args(_DEFAULTS, manifest_file=pixi_toml, specs=specs)
    result = execute_remove(args)
    assert result == 0

    doc = tomlkit.loads(pixi_toml.read_text(encoding="utf-8"))
    actual = list(doc["dependencies"].keys())
    assert actual == remaining


def test_remove_from_feature(pixi_toml: Path) -> None:
    args = make_args(
        _DEFAULTS,
        manifest_file=pixi_toml,
        specs=["pytest"],
        feature="test",
    )
    execute_remove(args)

    doc = tomlkit.loads(pixi_toml.read_text(encoding="utf-8"))
    assert "pytest" not in doc["feature"]["test"]["dependencies"]


def test_remove_from_pyproject(pyproject_toml: Path) -> None:
    args = make_args(_DEFAULTS, manifest_file=pyproject_toml, specs=["python"])
    execute_remove(args)

    doc = tomlkit.loads(pyproject_toml.read_text(encoding="utf-8"))
    assert "python" not in doc["tool"]["pixi"]["dependencies"]


def test_remove_prints_no_match(
    pixi_toml: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = make_args(_DEFAULTS, manifest_file=pixi_toml, specs=["nonexistent"])
    execute_remove(args)
    assert "No matching" in capsys.readouterr().out


def test_add_pypi_to_pyproject(pyproject_toml: Path) -> None:
    """Adding PyPI deps to pyproject.toml writes to pypi-dependencies."""
    args = make_args(
        _DEFAULTS,
        manifest_file=pyproject_toml,
        specs=["requests >=2.0"],
        pypi=True,
    )
    execute_add(args)
    doc = tomlkit.loads(pyproject_toml.read_text(encoding="utf-8"))
    assert doc["tool"]["pixi"]["pypi-dependencies"]["requests"] == ">=2.0"


def test_add_to_pyproject_feature(pyproject_toml: Path) -> None:
    """Adding deps to a feature in pyproject.toml."""
    args = make_args(
        _DEFAULTS,
        manifest_file=pyproject_toml,
        specs=["pytest"],
        feature="test",
    )
    execute_add(args)
    doc = tomlkit.loads(pyproject_toml.read_text(encoding="utf-8"))
    assert doc["tool"]["pixi"]["feature"]["test"]["dependencies"]["pytest"] == "*"


def test_remove_pypi_from_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing PyPI deps from pyproject.toml."""
    content = """\
[project]
name = "rm-pypi"

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.pixi.pypi-dependencies]
requests = ">=2.0"
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = make_args(_DEFAULTS, manifest_file=path, specs=["requests"], pypi=True)
    execute_remove(args)
    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    assert "requests" not in doc["tool"]["pixi"]["pypi-dependencies"]


def test_remove_from_pyproject_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing deps from a feature in pyproject.toml."""
    content = """\
[project]
name = "rm-feat"

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.pixi.feature.test.dependencies]
pytest = ">=8.0"
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["pytest"],
        feature="test",
    )
    execute_remove(args)
    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    assert "pytest" not in doc["tool"]["pixi"]["feature"]["test"]["dependencies"]


def test_remove_from_pyproject_no_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removing from pyproject.toml with no tool table returns empty."""
    content = """\
[project]
name = "no-tool"
"""
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = make_args(_DEFAULTS, manifest_file=path, specs=["numpy"])
    result = execute_remove(args)
    assert result == 0
    assert "No matching" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("filename", "namespace", "root_keys"),
    [
        ("conda.toml", "", ()),
        ("pixi.toml", "", ()),
        ("pyproject.toml", "tool.conda", ("tool", "conda")),
        ("pyproject.toml", "tool.pixi", ("tool", "pixi")),
    ],
    ids=["conda-toml", "pixi-toml", "pyproject-conda", "pyproject-pixi"],
)
def test_add_environment_auto_creates_env_entry(
    tmp_path: Path,
    filename: str,
    namespace: str,
    root_keys: tuple[str, ...],
) -> None:
    """Adding to an undefined environment auto-creates the env entry."""
    prefix = f"{namespace}." if namespace else ""
    project = '[project]\nname = "add-test"\n\n' if namespace else ""
    path = tmp_path / filename
    path.write_text(
        f"""{project}[{prefix}workspace]
name = "add-test"
channels = ["conda-forge"]
platforms = ["linux-64"]

[{prefix}dependencies]
python = ">=3.10"
""",
        encoding="utf-8",
    )
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["numpy"],
        environment="newenv",
    )
    result = execute_add(args)
    assert result == 0

    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    root = doc
    for key in root_keys:
        root = root[key]
    assert root["environments"]["newenv"]["dependencies"]["numpy"] == "*"
    assert "feature" not in root


@pytest.fixture
def environment_toml(tmp_path: Path) -> Path:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "environment-test"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"

[feature.test.dependencies]
pytest = ">=8"

[feature.lint.dependencies]
ruff = "*"

[feature.qa.dependencies]
same-name = "*"

[environments]
qa = ["test", "lint"]
isolated = { features = ["lint"], no-default-feature = true }
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def declaration_toml(tmp_path: Path) -> Path:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "declaration-test"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"

[feature.test.dependencies]
pytest = ">=8"

[environments.qa]
features = ["test"]
""",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("pypi", "dependency_key"),
    [(False, "dependencies"), (True, "pypi-dependencies")],
    ids=["conda", "pypi"],
)
@pytest.mark.parametrize(
    ("selectors", "table_path"),
    [
        ({}, ()),
        ({"platform": "linux-64"}, ("target", "linux-64")),
        ({"feature": "test"}, ("feature", "test")),
        (
            {"feature": "test", "platform": "linux-64"},
            ("feature", "test", "target", "linux-64"),
        ),
        ({"environment": "qa"}, ("environments", "qa")),
        (
            {"environment": "qa", "platform": "linux-64"},
            ("environments", "qa", "target", "linux-64"),
        ),
    ],
    ids=[
        "base",
        "base-target",
        "feature",
        "feature-target",
        "environment",
        "environment-target",
    ],
)
def test_add_remove_address_exact_declaration_location(
    declaration_toml: Path,
    pypi: bool,
    dependency_key: str,
    selectors: dict[str, str],
    table_path: tuple[str, ...],
) -> None:
    add_args = make_args(
        _DEFAULTS,
        manifest_file=declaration_toml,
        specs=["click >=8"],
        pypi=pypi,
        **selectors,
    )
    assert execute_add(add_args) == 0

    doc = tomlkit.loads(declaration_toml.read_text(encoding="utf-8"))
    table = doc
    for key in table_path:
        table = table[key]
    assert table[dependency_key]["click"] == ">=8"

    remove_args = make_args(
        _DEFAULTS,
        manifest_file=declaration_toml,
        specs=["click"],
        pypi=pypi,
        **selectors,
    )
    assert execute_remove(remove_args) == 0
    doc = tomlkit.loads(declaration_toml.read_text(encoding="utf-8"))
    table = doc
    for key in table_path:
        table = table[key]
    assert "click" not in table[dependency_key]


def test_add_preserves_and_replaces_only_selected_workspace_marker(
    tmp_path: Path,
    rich_console: Console,
) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "workspace-marker"
channels = ["conda-forge"]
platforms = ["osx-arm64"]

[workspace.dependencies]
numpy = { version = ">=2", build = "py314*" }

[dependencies]
numpy = { workspace = true }

[target.osx-arm64.dependencies]
numpy = { workspace = true, build = "py314h*" }
""",
        encoding="utf-8",
    )

    bare_args = make_args(_DEFAULTS, manifest_file=path, specs=["numpy"])
    assert execute_add(bare_args, console=rich_console) == 0
    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    assert doc["dependencies"]["numpy"].unwrap() == {"workspace": True}
    assert doc["target"]["osx-arm64"]["dependencies"]["numpy"].unwrap() == {
        "workspace": True,
        "build": "py314h*",
    }
    output = rich_console.file.getvalue()
    assert "[target.osx-arm64.dependencies] overrides" in output
    output = " ".join(output.split())
    assert (
        "Rerun using --platform osx-arm64 as the complete location selector." in output
    )

    explicit_args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["conda-forge::numpy=2.3.1=py314h*"],
    )
    assert execute_add(explicit_args, console=rich_console) == 0
    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    assert doc["dependencies"]["numpy"].unwrap() == {
        "version": "2.3.1",
        "build": "py314h*",
        "channel": "conda-forge",
    }
    assert doc["workspace"]["dependencies"]["numpy"].unwrap() == {
        "version": ">=2",
        "build": "py314*",
    }
    assert doc["target"]["osx-arm64"]["dependencies"]["numpy"].unwrap() == {
        "workspace": True,
        "build": "py314h*",
    }

    remove_args = make_args(_DEFAULTS, manifest_file=path, specs=["numpy"])
    assert execute_remove(remove_args, console=rich_console) == 0
    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    assert "numpy" not in doc["dependencies"]
    assert "numpy" in doc["workspace"]["dependencies"]
    assert "numpy" in doc["target"]["osx-arm64"]["dependencies"]


def test_remove_wrong_location_is_atomic_and_lists_every_selector(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "wrong-location"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
numpy = ">=2"

[target.linux-64.dependencies]
numpy = ">=2.1"

[feature.test.dependencies]
numpy = ">=2.2"

[feature.test.pypi-dependencies]
requests = ">=2"

[feature.test.target.linux-64.dependencies]
numpy = ">=2.3"

[environments.qa]
features = ["test"]

[environments.qa.dependencies]
numpy = ">=2.4"

[environments.qa.target.linux-64.dependencies]
numpy = ">=2.5"

[environments.other.dependencies]
click = ">=8"
""",
        encoding="utf-8",
    )
    before = path.read_bytes()
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["click", "numpy", "requests"],
        environment="other",
    )

    with pytest.raises(CondaWorkspacesError) as exc_info:
        execute_remove(args)

    assert path.read_bytes() == before
    hints = exc_info.value.hints
    expected = [
        "remove numpy",
        "remove --platform linux-64 numpy",
        "remove --feature test numpy",
        "remove --feature test --platform linux-64 numpy",
        "remove --environment qa numpy",
        "remove --environment qa --platform linux-64 numpy",
        "remove --pypi --feature test requests",
    ]
    for command in expected:
        assert any(command in hint for hint in hints)
    assert all(f"--file {path}" in hint for hint in hints)


def test_add_unknown_platform_fails_without_write(declaration_toml: Path) -> None:
    before = declaration_toml.read_bytes()
    args = make_args(
        _DEFAULTS,
        manifest_file=declaration_toml,
        specs=["click"],
        platform="win-64",
    )

    with pytest.raises(CondaWorkspacesError, match="win-64"):
        execute_add(args)

    assert declaration_toml.read_bytes() == before


def test_dependency_location_diagnostics_use_shell_quoting(tmp_path: Path) -> None:
    path = tmp_path / "manifest with spaces.toml"
    location = DependencyLocation(feature="qa tools", platform="linux-64")

    assert location.command(
        "remove",
        "package name",
        pypi=False,
        manifest_path=path,
    ) == quote_for_shell(
        "conda",
        "workspace",
        "--file",
        str(path),
        "remove",
        "--feature",
        "qa tools",
        "--platform",
        "linux-64",
        "package name",
    )
    assert location.selector() == quote_for_shell(
        "--feature",
        "qa tools",
        "--platform",
        "linux-64",
    )
    assert location.table_name("dependencies", ("tool", "conda")) == (
        '[tool.conda.feature."qa tools".target.linux-64.dependencies]'
    )


def test_remove_accepts_existing_stale_platform_target(tmp_path: Path) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "stale-target"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"

[target.win-64.dependencies]
pywin32 = "*"
""",
        encoding="utf-8",
    )
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["pywin32"],
        platform="win-64",
    )

    assert execute_remove(args) == 0

    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    assert "pywin32" not in doc["target"]["win-64"]["dependencies"]


@pytest.mark.parametrize(
    ("table", "command"),
    [
        pytest.param(
            "feature.test.target.win-64",
            "remove --feature test --platform win-64 pywin32",
            id="feature",
        ),
        pytest.param(
            "environments.qa.target.win-64",
            "remove --environment qa --platform win-64 pywin32",
            id="environment",
        ),
    ],
)
def test_remove_finds_stale_platform_at_another_location(
    tmp_path: Path,
    table: str,
    command: str,
) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        f"""\
[workspace]
name = "stale-target"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"

[{table}.dependencies]
pywin32 = "*"
""",
        encoding="utf-8",
    )
    before = path.read_bytes()
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["pywin32"],
        platform="win-64",
    )

    with pytest.raises(CondaWorkspacesError) as exc_info:
        execute_remove(args)

    assert path.read_bytes() == before
    assert any(command in hint for hint in exc_info.value.hints)


@pytest.mark.parametrize(
    "platform",
    ["linux-64", "linux-64-cuda"],
    ids=["backing-subdir", "rich-name"],
)
def test_add_target_accepts_rich_platform_name_or_subdir(
    tmp_path: Path,
    platform: str,
) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "rich-platform-target"
channels = ["conda-forge"]
platforms = [
  { name = "linux-64-cuda", platform = "linux-64", cuda = "12.0" },
]

[dependencies]
python = ">=3.10"
""",
        encoding="utf-8",
    )
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["click"],
        platform=platform,
    )

    assert execute_add(args) == 0

    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    assert doc["target"][platform]["dependencies"]["click"] == "*"


def test_add_target_accepts_feature_broadened_platform(tmp_path: Path) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "feature-platform"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"

[feature.windows]
platforms = ["win-64"]
""",
        encoding="utf-8",
    )
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["pywin32"],
        feature="windows",
        platform="win-64",
    )

    assert execute_add(args) == 0

    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    assert (
        doc["feature"]["windows"]["target"]["win-64"]["dependencies"]["pywin32"] == "*"
    )
    assert doc["environments"]["windows"]["features"] == ["windows"]


def test_add_feature_target_uses_affected_environment_scope(tmp_path: Path) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "feature-platform-scope"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"

[feature.windows]
platforms = ["win-64"]

[feature.mac]
platforms = ["osx-arm64"]

[environments.windows]
features = ["windows"]

[environments.mac]
features = ["mac"]
""",
        encoding="utf-8",
    )
    before = path.read_bytes()
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["pywin32"],
        feature="mac",
        platform="win-64",
    )

    with pytest.raises(CondaWorkspacesError, match="win-64"):
        execute_add(args)

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("features", "accepted"),
    [
        pytest.param('["windows"]', True, id="composed"),
        pytest.param("[]", False, id="unrelated"),
    ],
)
def test_add_environment_target_uses_resolved_platform_scope(
    tmp_path: Path,
    features: str,
    accepted: bool,
) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        f"""\
[workspace]
name = "environment-platform"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"

[feature.windows]
platforms = ["win-64"]

[environments.qa]
features = {features}
""",
        encoding="utf-8",
    )
    before = path.read_bytes()
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["pywin32"],
        environment="qa",
        platform="win-64",
    )

    if accepted:
        assert execute_add(args) == 0
        doc = tomlkit.loads(path.read_text(encoding="utf-8"))
        assert (
            doc["environments"]["qa"]["target"]["win-64"]["dependencies"]["pywin32"]
            == "*"
        )
    else:
        with pytest.raises(CondaWorkspacesError, match="win-64"):
            execute_add(args)
        assert path.read_bytes() == before


def test_add_preserves_inline_environment_target_toml(tmp_path: Path) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
environments = { qa = { target = { linux-64 = { dependencies = { numpy = "*" } } } } }

[workspace]
name = "inline-target"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"
""",
        encoding="utf-8",
    )

    conda_args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["numpy>=2"],
        environment="qa",
        platform="linux-64",
    )
    assert execute_add(conda_args) == 0

    pypi_args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["requests>=2"],
        environment="qa",
        platform="linux-64",
        pypi=True,
    )
    assert execute_add(pypi_args) == 0

    config = find_parser(path).parse(path)
    environment = config.environments["qa"]
    assert (
        str(environment.target_conda_dependencies["linux-64"]["numpy"].version) == ">=2"
    )
    assert environment.target_pypi_dependencies["linux-64"]["requests"].spec == ">=2"


def test_add_preserves_inline_feature_toml(tmp_path: Path) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
feature = { qa = { dependencies = { numpy = "*" } } }

[workspace]
name = "inline-feature"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"
""",
        encoding="utf-8",
    )
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["click"],
        feature="qa",
    )

    assert execute_add(args) == 0

    config = find_parser(path).parse(path)
    assert set(config.features["qa"].conda_dependencies) == {"numpy", "click"}


@pytest.mark.parametrize(
    ("selectors", "owner_key"),
    [
        pytest.param({"feature": "qa"}, "feature", id="feature"),
        pytest.param({"environment": "qa"}, "environments", id="environment"),
    ],
)
def test_add_creates_missing_containers_in_inline_pyproject(
    tmp_path: Path,
    selectors: dict[str, str],
    owner_key: str,
) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        'tool = { pixi = { workspace = { channels = ["conda-forge"], '
        'platforms = ["linux-64"] } } }\n\n'
        "[project]\n"
        'name = "inline-pyproject"\n',
        encoding="utf-8",
    )
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["click"],
        platform="linux-64",
        **selectors,
    )

    assert execute_add(args) == 0

    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    workspace = doc["tool"]["pixi"]
    assert (
        workspace[owner_key]["qa"]["target"]["linux-64"]["dependencies"]["click"] == "*"
    )
    find_parser(path).parse(path)


def test_remove_does_not_treat_workspace_pool_as_mutation_location(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "pool-only"
channels = ["conda-forge"]
platforms = ["linux-64"]

[workspace.dependencies]
numpy = ">=2"

[dependencies]
python = ">=3.10"
""",
        encoding="utf-8",
    )
    before = path.read_bytes()
    args = make_args(_DEFAULTS, manifest_file=path, specs=["numpy"])

    assert execute_remove(args) == 0

    assert path.read_bytes() == before


def test_pyproject_mutation_uses_parser_selected_namespace(
    tmp_path: Path,
    rich_console: Console,
) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        """\
[project]
name = "namespace-selection"

[tool.conda.workspace]

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.pixi.dependencies]
numpy = ">=2"

[tool.pixi.target.linux-64.dependencies]
numpy = ">=2.1"
""",
        encoding="utf-8",
    )
    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["numpy=2.3"],
    )

    assert execute_add(args, console=rich_console) == 0

    doc = tomlkit.loads(path.read_text(encoding="utf-8"))
    assert "dependencies" not in doc["tool"]["conda"]
    assert doc["tool"]["pixi"]["dependencies"]["numpy"] == "2.3.*"
    output = rich_console.file.getvalue()
    assert "[tool.pixi.target.linux-64.dependencies] overrides" in output
    assert "selected [tool.pixi.dependencies]" in output


def test_add_environment_dependencies_are_private_and_resolved(
    environment_toml: Path,
) -> None:
    mutations = [
        ("qa", "coverage", False),
        ("qa", "requests >=2", True),
        ("isolated", "click", False),
        ("default", "rich", False),
    ]
    for environment, spec, pypi in mutations:
        args = make_args(
            _DEFAULTS,
            manifest_file=environment_toml,
            specs=[spec],
            environment=environment,
            pypi=pypi,
        )
        assert execute_add(args) == 0

    doc = tomlkit.loads(environment_toml.read_text(encoding="utf-8"))
    assert doc["environments"]["qa"]["features"] == ["test", "lint"]
    assert doc["environments"]["qa"]["dependencies"]["coverage"] == "*"
    assert doc["environments"]["qa"]["pypi-dependencies"]["requests"] == ">=2"
    assert doc["feature"]["qa"]["dependencies"].unwrap() == {"same-name": "*"}
    assert doc["environments"]["isolated"]["no-default-feature"] is True
    assert doc["environments"]["default"]["dependencies"]["rich"] == "*"
    assert "default" not in doc["feature"]

    config = find_parser(environment_toml).parse(environment_toml)
    qa = config.environments["qa"]
    assert set(config.merged_conda_dependencies(qa)) == {
        "python",
        "pytest",
        "ruff",
        "coverage",
    }
    assert set(config.merged_pypi_dependencies(qa)) == {"requests"}
    isolated = config.environments["isolated"]
    assert set(config.merged_conda_dependencies(isolated)) == {"ruff", "click"}
    default = config.environments["default"]
    assert set(config.merged_conda_dependencies(default)) == {"python", "rich"}


def test_remove_environment_local_dependency(environment_toml: Path) -> None:
    add_args = make_args(
        _DEFAULTS,
        manifest_file=environment_toml,
        specs=["coverage"],
        environment="qa",
    )
    execute_add(add_args)

    remove_args = make_args(
        _DEFAULTS,
        manifest_file=environment_toml,
        specs=["coverage"],
        environment="qa",
    )
    assert execute_remove(remove_args) == 0

    config = find_parser(environment_toml).parse(environment_toml)
    qa = config.environments["qa"]
    assert "coverage" not in qa.conda_dependencies
    assert "pytest" in config.merged_conda_dependencies(qa)


def test_remove_inherited_environment_dependency_fails_without_write(
    environment_toml: Path,
) -> None:
    before = environment_toml.read_bytes()
    args = make_args(
        _DEFAULTS,
        manifest_file=environment_toml,
        specs=["pytest"],
        environment="qa",
    )

    with pytest.raises(CondaWorkspacesError, match="not declared directly") as exc_info:
        execute_remove(args)

    assert environment_toml.read_bytes() == before
    assert any("--feature test pytest" in hint for hint in exc_info.value.hints)


@pytest.mark.parametrize(
    "specs, extra_kwargs, expected_text",
    [
        (["python"], {}, "default"),
        (["pytest"], {"feature": "test"}, "feature 'test'"),
    ],
    ids=["default", "feature"],
)
def test_remove_prints_location(
    pixi_toml: Path,
    capsys: pytest.CaptureFixture[str],
    specs: list[str],
    extra_kwargs: dict,
    expected_text: str,
) -> None:
    args = make_args(
        _DEFAULTS,
        manifest_file=pixi_toml,
        specs=specs,
        **extra_kwargs,
    )
    execute_remove(args)
    out = capsys.readouterr().out
    assert expected_text in out
    assert "Removed 1" in out


@pytest.fixture
def sync_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A multi-env workspace: `default` + `test` (composing the `test` feature)."""
    content = """\
[workspace]
name = "sync-test"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"

[feature.test.dependencies]
pytest = ">=8.0"

[environments.default]
features = []

[environments.test]
features = ["test"]

[environments.test.dependencies]
coverage = "*"
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return path


@pytest.fixture
def stub_sync(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict]]:
    """Capture ``sync_environments`` invocations from add and remove.

    Returns a list populated on each call with the env names and the flag
    kwargs, so tests can assert what the auto-install pipeline would do
    without actually solving or installing.
    """
    calls: list[tuple[list[str], dict]] = []

    def fake_sync(
        config,
        ctx,
        env_names,
        *,
        no_install=False,
        force_reinstall=False,
        dry_run=False,
        prune=False,
        console,
    ) -> None:
        calls.append(
            (
                list(env_names),
                {
                    "no_install": no_install,
                    "force_reinstall": force_reinstall,
                    "dry_run": dry_run,
                    "prune": prune,
                },
            )
        )

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.add.sync_environments", fake_sync
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.remove.sync_environments", fake_sync
    )
    return calls


@pytest.mark.parametrize(
    "execute_fn, spec",
    [(execute_add, "numpy"), (execute_remove, "python")],
    ids=["add", "remove"],
)
def test_default_feature_syncs_all_envs(
    sync_workspace: Path,
    stub_sync: list[tuple[list[str], dict]],
    execute_fn,
    spec: str,
) -> None:
    """Editing the default feature selects each environment that composes it."""
    args = make_args(
        _DEFAULTS,
        manifest_file=sync_workspace,
        specs=[spec],
        no_lockfile_update=False,
    )
    assert execute_fn(args) == 0

    assert len(stub_sync) == 1
    env_names, flags = stub_sync[0]
    assert set(env_names) == {"default", "test"}
    assert flags == {
        "no_install": False,
        "force_reinstall": False,
        "dry_run": False,
        "prune": execute_fn is execute_remove,
    }


@pytest.mark.parametrize(
    ("execute_fn", "spec", "location"),
    [
        (execute_add, "coverage", {"feature": "test"}),
        (execute_remove, "pytest", {"feature": "test"}),
        (execute_add, "hypothesis", {"environment": "test"}),
        (execute_remove, "coverage", {"environment": "test"}),
    ],
    ids=["feature-add", "feature-remove", "environment-add", "environment-remove"],
)
def test_explicit_location_syncs_only_selected_environment(
    sync_workspace: Path,
    stub_sync: list[tuple[list[str], dict]],
    execute_fn,
    spec: str,
    location: dict[str, str],
) -> None:
    args = make_args(
        _DEFAULTS,
        manifest_file=sync_workspace,
        specs=[spec],
        no_lockfile_update=False,
        **location,
    )
    execute_fn(args)

    assert len(stub_sync) == 1
    env_names, _ = stub_sync[0]
    assert env_names == ["test"]


@pytest.mark.parametrize(
    "execute_fn, spec",
    [(execute_add, "numpy"), (execute_remove, "python")],
    ids=["add", "remove"],
)
def test_no_lockfile_update_skips_sync(
    sync_workspace: Path,
    stub_sync: list[tuple[list[str], dict]],
    execute_fn,
    spec: str,
) -> None:
    """``--no-lockfile-update`` short-circuits before ``sync_environments``."""
    args = make_args(_DEFAULTS, manifest_file=sync_workspace, specs=[spec])
    execute_fn(args)

    assert stub_sync == []


@pytest.mark.parametrize(
    ("execute_fn", "spec"),
    [(execute_add, "coverage"), (execute_remove, "pytest")],
    ids=["add", "remove"],
)
def test_feature_mutation_no_install_writes_complete_lockfile(
    sync_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    execute_fn,
    spec: str,
) -> None:
    """A selective manifest edit still locks every environment."""

    class FakeRecord:
        def __init__(self, name: str, url: str) -> None:
            self.name = name
            self.url = url

        def get(self, key: str, default: object = None) -> object:
            return default

    def fake_solve(self, platform, *, prefix):
        return [
            FakeRecord(
                "python",
                f"https://example.com/{self.name}-{platform}-python.conda",
            )
        ]

    monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", fake_solve)
    args = make_args(
        _DEFAULTS,
        manifest_file=sync_workspace,
        specs=[spec],
        feature="test",
        no_install=True,
        no_lockfile_update=False,
    )

    assert execute_fn(args) == 0

    data = load_yaml(sync_workspace.parent / "conda.lock")
    assert data["version"] == 1
    assert set(data["environments"]) == {"default", "test"}
    assert "linux-64" in data["environments"]["default"]["packages"]
    assert "linux-64" in data["environments"]["test"]["packages"]


@pytest.mark.parametrize(
    "extra_kwargs, expected_flags",
    [
        (
            {"no_install": True},
            {"no_install": True, "force_reinstall": False, "dry_run": False},
        ),
        (
            {"force_reinstall": True},
            {"no_install": False, "force_reinstall": True, "dry_run": False},
        ),
        (
            {"dry_run": True},
            {"no_install": False, "force_reinstall": False, "dry_run": True},
        ),
        (
            {"force_reinstall": True, "dry_run": True},
            {"no_install": False, "force_reinstall": True, "dry_run": True},
        ),
    ],
    ids=["no-install", "force-reinstall", "dry-run", "force-and-dry"],
)
@pytest.mark.parametrize(
    "execute_fn, spec",
    [(execute_add, "numpy"), (execute_remove, "python")],
    ids=["add", "remove"],
)
def test_flags_forwarded_to_sync(
    sync_workspace: Path,
    stub_sync: list[tuple[list[str], dict]],
    execute_fn,
    spec: str,
    extra_kwargs: dict,
    expected_flags: dict,
) -> None:
    """Flag kwargs pass straight through to ``sync_environments``."""
    args = make_args(
        _DEFAULTS,
        manifest_file=sync_workspace,
        specs=[spec],
        no_lockfile_update=False,
        **extra_kwargs,
    )
    execute_fn(args)

    _, flags = stub_sync[0]
    assert flags == {
        **expected_flags,
        "prune": execute_fn is execute_remove,
    }


@pytest.mark.parametrize(
    "execute_fn, spec, dependency, present",
    [
        (execute_add, "numpy", "numpy", True),
        (execute_remove, "python", "python", False),
    ],
    ids=["add", "remove"],
)
def test_dependency_dry_run_uses_prospective_config_without_writes(
    sync_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
    execute_fn,
    spec: str,
    dependency: str,
    present: bool,
) -> None:
    synced: list[tuple[WorkspaceConfig, bool]] = []

    def record_sync(config, ctx, env_names, *, dry_run=False, **kwargs) -> None:
        synced.append((config, dry_run))

    module = "add" if execute_fn is execute_add else "remove"
    monkeypatch.setattr(
        f"conda_workspaces.cli.workspace.{module}.sync_environments",
        record_sync,
    )
    before = snapshot_tree(sync_workspace.parent)

    result = execute_fn(
        make_args(
            _DEFAULTS,
            manifest_file=sync_workspace,
            specs=[spec],
            no_lockfile_update=False,
            dry_run=True,
        )
    )

    assert result == 0
    assert snapshot_tree(sync_workspace.parent) == before
    assert len(synced) == 1
    config, dry_run = synced[0]
    assert (dependency in config.features["default"].conda_dependencies) is present
    assert dry_run is True


def test_remove_no_match_skips_sync(
    sync_workspace: Path, stub_sync: list[tuple[list[str], dict]]
) -> None:
    """Removing a missing spec is a no-op — no manifest write, no sync."""
    args = make_args(
        _DEFAULTS,
        manifest_file=sync_workspace,
        specs=["nonexistent"],
        no_lockfile_update=False,
    )
    execute_remove(args)
    assert stub_sync == []


def test_add_no_default_feature_env_not_affected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_sync: list[tuple[list[str], dict]],
) -> None:
    """Envs with ``no-default-feature`` are excluded from default-feature syncs."""
    content = """\
[workspace]
name = "no-def"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"

[feature.lint.dependencies]
ruff = "*"

[environments]
default = []
lint = {features = ["lint"], no-default-feature = true}
"""
    path = tmp_path / "pixi.toml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    args = make_args(
        _DEFAULTS,
        manifest_file=path,
        specs=["numpy"],
        no_lockfile_update=False,
    )
    execute_add(args)

    env_names, _ = stub_sync[0]
    assert env_names == ["default"]
