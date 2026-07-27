"""Tests for conda_workspaces.cli.workspace.info."""

from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from conda.exceptions import ArgumentError, CondaValueError
from rich.console import Console

from conda_workspaces.cli.workspace.info import execute_info
from conda_workspaces.models import LockfileStatus

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import CreateWorkspaceEnv

_DEFAULTS = {
    "manifest_file": None,
    "environment": None,
    "json": False,
    "packages": False,
}


def test_info_workspace_overview(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS)
    result = execute_info(args, console=rich_console)
    assert result == 0
    out = rich_console.file.getvalue()
    assert "Manifest" in out
    assert "Environments" in out
    assert "default" in out
    assert "test" in out
    assert "conda-forge" in out


def test_info_explicit_manifest_allows_symlinked_parent(
    pixi_workspace: Path,
    tmp_path: Path,
    rich_console: Console,
) -> None:
    linked_workspace = tmp_path / "linked-workspace"
    linked_workspace.symlink_to(pixi_workspace, target_is_directory=True)

    result = execute_info(
        make_args(
            _DEFAULTS,
            manifest_file=linked_workspace / "pixi.toml",
        ),
        console=rich_console,
    )

    assert result == 0
    assert "default" in rich_console.file.getvalue()


def test_info_does_not_emit_manifest_terminal_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = "spoof\x1b[2J\x1b]8;;https://example.invalid\x1b\\"
    manifest = tmp_path / "conda.toml"
    manifest.write_text(
        "[workspace]\n"
        f"name = {json.dumps(payload)}\n"
        f"description = {json.dumps(payload)}\n"
        'channels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n',
        encoding="utf-8",
    )
    console = Console(file=StringIO(), force_terminal=True)
    monkeypatch.chdir(tmp_path)

    result = execute_info(make_args(_DEFAULTS), console=console)

    assert result == 0
    output = console.file.getvalue()
    assert "\x1b[2J" not in output
    assert "\x1b]8;;https://example.invalid" not in output
    assert r"\x1b[2J" in output


@pytest.mark.parametrize(
    "selector",
    ["directory", "missing"],
    ids=["directory", "missing"],
)
def test_info_rejects_non_file_manifest_selector(
    tmp_path: Path,
    selector: str,
) -> None:
    manifest_file = tmp_path if selector == "directory" else tmp_path / "missing.toml"

    with pytest.raises(CondaValueError, match="must name an existing"):
        execute_info(make_args(_DEFAULTS, manifest_file=manifest_file))


@pytest.mark.parametrize(
    "env_name, expected_fragments",
    [
        (
            "default",
            ["Environment", "default", "Installed", "no", "conda-forge", "python"],
        ),
        (
            "test",
            ["Environment", "test", "python", "pytest"],
        ),
    ],
    ids=["default-text", "named-env"],
)
def test_info_env_details(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    env_name: str,
    expected_fragments: list[str],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, environment=env_name)
    result = execute_info(args, console=rich_console)
    assert result == 0
    out = rich_console.file.getvalue()
    for fragment in expected_fragments:
        assert fragment in out


def test_info_installed_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default", pkg_count=3)

    args = make_args(_DEFAULTS, environment="default")
    execute_info(args, console=rich_console)
    out = rich_console.file.getvalue()
    assert "Installed" in out and "yes" in out
    assert "Packages" in out and "3" in out


def test_info_json_workspace(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, json=True)
    execute_info(args, console=rich_console)
    out = rich_console.file.getvalue()
    data = json.loads(out)
    assert data["name"] == "cli-test"
    assert "environments" in data
    assert "channels" in data
    assert [detail["name"] for detail in data["environment_details"]] == [
        "default",
        "test",
    ]
    assert all("packages" not in detail for detail in data["environment_details"])


def test_info_json_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, environment="default", json=True)
    execute_info(args, console=rich_console)
    out = rich_console.file.getvalue()
    data = json.loads(out)
    assert data["name"] == "default"
    assert data["features"] == []
    assert data["no_default_feature"] is False
    assert "conda_dependencies" in data
    assert "channels" in data


@pytest.mark.parametrize(
    ("filename", "namespace"),
    [
        pytest.param("conda.toml", "", id="conda-toml"),
        pytest.param("pixi.toml", "", id="pixi-toml"),
        pytest.param("pyproject.toml", "tool.conda", id="pyproject-conda"),
        pytest.param("pyproject.toml", "tool.pixi", id="pyproject-pixi"),
    ],
)
def test_info_json_environment_details_include_dependency_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    filename: str,
    namespace: str,
) -> None:
    prefix = f"{namespace}." if namespace else ""
    content = f"""\
[{prefix}workspace]
name = "snapshot"
channels = ["conda-forge"]
platforms = [{{ name = "linux-64-cuda", platform = "linux-64" }}]

[{prefix}workspace.dependencies]
python = ">=3.12"

[{prefix}dependencies]
python = {{ workspace = true, build = "py*" }}
shared = ">=1"

[{prefix}target.linux-64.dependencies]
shared = ">=2"

[{prefix}feature.test.dependencies]
pytest = ">=8"

[{prefix}feature.test.pypi-dependencies]
requests = ">=2"
vcs = {{ git = "https://example.com/repo.git", branch = "main" }}

[{prefix}feature.test.target.linux-64-cuda.dependencies]
shared = ">=3"

[{prefix}environments.test]
features = ["test"]

[{prefix}environments.test.dependencies]
private = ">=1"

[{prefix}environments.test.target.linux-64-cuda.dependencies]
shared = ">=4"

[{prefix}environments.isolated]
no-default-feature = true

[{prefix}environments.isolated.dependencies]
standalone = ">=1"
"""
    (tmp_path / filename).write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    execute_info(make_args(_DEFAULTS, json=True), console=rich_console)

    data = json.loads(rich_console.file.getvalue())
    details = {detail["name"]: detail for detail in data["environment_details"]}
    test = details["test"]
    assert test["features"] == ["test"]
    assert test["no_default_feature"] is False
    assert test["installed"] is False
    assert test["channels"] == ["conda-forge"]
    assert test["platforms"] == ["linux-64-cuda"]

    resolution = test["resolutions"][0]
    assert resolution["platform"] == "linux-64-cuda"
    assert resolution["subdir"] == "linux-64"
    conda = resolution["conda_dependencies"]
    pypi = resolution["pypi_dependencies"]
    assert conda["python"]["provenance"] == {
        "table": f"[{prefix}dependencies]",
        "inherited_from": f"[{prefix}workspace.dependencies]",
    }
    assert conda["shared"]["spec"].endswith(">=4")
    assert conda["shared"]["provenance"]["table"] == (
        f"[{prefix}environments.test.target.linux-64-cuda.dependencies]"
    )
    assert conda["pytest"]["provenance"]["table"] == (
        f"[{prefix}feature.test.dependencies]"
    )
    assert conda["private"]["provenance"]["table"] == (
        f"[{prefix}environments.test.dependencies]"
    )
    assert pypi["requests"]["provenance"]["table"] == (
        f"[{prefix}feature.test.pypi-dependencies]"
    )
    assert pypi["requests"]["spec"] == {"version": ">=2"}
    assert pypi["vcs"]["spec"] == {
        "git": "https://example.com/repo.git",
        "branch": "main",
    }

    isolated = details["isolated"]
    assert isolated["features"] == []
    assert isolated["no_default_feature"] is True
    isolated_conda = isolated["resolutions"][0]["conda_dependencies"]
    assert set(isolated_conda) == {"standalone"}


def test_info_json_packages_are_opt_in(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")
    packages = [
        {"name": "numpy", "version": "2.0.0", "build": "py312_0"},
        {"name": "python", "version": "3.12.0", "build": "h123_0"},
    ]
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.info.list_installed_packages",
        lambda ctx, env_name: packages,
    )

    execute_info(
        make_args(_DEFAULTS, json=True, packages=True),
        console=rich_console,
    )

    data = json.loads(rich_console.file.getvalue())
    details = {detail["name"]: detail for detail in data["environment_details"]}
    assert details["default"]["packages"] == packages
    assert details["test"]["packages"] == []


def test_info_text_packages_reports_uninstalled_environments(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    execute_info(make_args(_DEFAULTS, packages=True), console=rich_console)

    out = rich_console.file.getvalue()
    assert "Packages in default" in out
    assert "Packages in test" in out
    assert out.count("(not installed)") == 2


def test_info_rejects_packages_for_one_environment(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(
        ArgumentError,
        match="--packages is only available for the workspace overview",
    ):
        execute_info(
            make_args(
                _DEFAULTS,
                environment="default",
                packages=True,
            )
        )


@pytest.mark.parametrize(
    ("json_output", "assertions"),
    [
        pytest.param(
            False,
            lambda out: "Known Platforms" in out and "win-64" in out,
            id="text-row",
        ),
        pytest.param(
            True,
            lambda out: (
                json.loads(out)["platforms"] == ["linux-64", "osx-arm64"]
                and set(json.loads(out)["known_platforms"])
                == {"linux-64", "osx-arm64", "win-64"}
            ),
            id="json-key",
        ),
    ],
)
def test_info_workspace_known_platforms_when_broadened(
    broadened_platform_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    json_output: bool,
    assertions,
) -> None:
    """A feature-declared platform surfaces in both text and JSON workspace info.

    ``conda workspace lock --platform <p> --output <fragment>`` can
    solve for platforms no workspace-level ``platforms`` entry names
    as long as a feature declares them, so the reachable set must be
    visible alongside the workspace set.
    """
    monkeypatch.chdir(broadened_platform_workspace)

    args = make_args(_DEFAULTS, json=json_output)
    execute_info(args, console=rich_console)
    assert assertions(rich_console.file.getvalue())


def test_info_workspace_hides_known_platforms_when_equal(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    """``Known Platforms`` row is omitted when equal to ``Platforms`` (no noise)."""
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS)
    execute_info(args, console=rich_console)
    out = rich_console.file.getvalue()
    assert "Known Platforms" not in out


def test_info_shows_pypi_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
) -> None:
    """PyPI dependencies appear in text output."""
    content = """\
[workspace]
name = "pypi-info"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"

[pypi-dependencies]
requests = ">=2.28"
"""
    (tmp_path / "pixi.toml").write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = make_args(_DEFAULTS, environment="default")
    execute_info(args, console=rich_console)
    out = rich_console.file.getvalue()
    assert "PyPI dependencies" in out
    assert "requests" in out


def test_info_json_redacts_pypi_dependency_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "conda.toml"
    manifest.write_text(
        """\
[workspace]
name = "private-pypi"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[pypi-dependencies]
vcs = { git = "HTTPS://user:password@example.test/team/repo.git?token=secret#main" }
artifact = { url = "https://p.example/t%252Fsecret/pkg.whl?x=secret#hash" }
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    env_console = Console(file=StringIO(), width=200, highlight=False)
    execute_info(
        make_args(_DEFAULTS, environment="default", json=True),
        console=env_console,
    )
    env_data = json.loads(env_console.file.getvalue())
    assert env_data["pypi_dependencies"] == {
        "artifact": "artifact @ https://p.example/pkg.whl",
        "vcs": "vcs @ git+HTTPS://example.test/team/repo.git",
    }

    workspace_console = Console(file=StringIO(), width=200, highlight=False)
    execute_info(
        make_args(_DEFAULTS, json=True),
        console=workspace_console,
    )
    resolution = workspace_console.file.getvalue()
    workspace_data = json.loads(resolution)
    pypi = workspace_data["environment_details"][0]["resolutions"][0][
        "pypi_dependencies"
    ]
    assert pypi["artifact"]["spec"] == {"url": "https://p.example/pkg.whl"}
    assert pypi["vcs"]["spec"] == {"git": "HTTPS://example.test/team/repo.git"}
    assert "password" in manifest.read_text(encoding="utf-8")


@pytest.mark.parametrize("environment", [None, "default"], ids=["workspace", "env"])
def test_info_json_redacts_credentials_in_arbitrary_match_spec_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: str | None,
) -> None:
    manifest = tmp_path / "conda.toml"
    manifest.write_text(
        """\
[workspace]
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = { build = "https://user:LEAKME@example.test/build" }
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    console = Console(file=StringIO(), width=200, highlight=False)

    execute_info(
        make_args(_DEFAULTS, environment=environment, json=True),
        console=console,
    )

    output = console.file.getvalue()
    assert "LEAKME" not in output
    assert "user" not in output


def test_info_json_redacts_relative_channel_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "conda.toml"
    manifest.write_text(
        """\
[workspace]
channels = ["t/SENSITIVE-VALUE/private"]
platforms = ["linux-64", "osx-arm64", "win-64"]
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    for environment in (None, "default"):
        console = Console(file=StringIO(), width=200, highlight=False)
        execute_info(
            make_args(_DEFAULTS, environment=environment, json=True),
            console=console,
        )
        output = console.file.getvalue()
        data = json.loads(output)

        assert "SENSITIVE-VALUE" not in output
        assert data["channels"] == ["https://conda.anaconda.org/private"]


@pytest.mark.parametrize(
    ("lockfile_status_value", "expected_text"),
    [
        pytest.param(
            LockfileStatus(status=LockfileStatus.UP_TO_DATE),
            "up-to-date",
            id="up-to-date",
        ),
        pytest.param(
            LockfileStatus(status=LockfileStatus.OUT_OF_DATE, reason="dep missing"),
            "out-of-date",
            id="out-of-date",
        ),
        pytest.param(
            LockfileStatus(status=LockfileStatus.MISSING),
            "missing",
            id="missing",
        ),
    ],
)
def test_info_shows_lockfile_status(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    lockfile_status_value: LockfileStatus,
    expected_text: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.info.lockfile_status",
        lambda ctx, config: lockfile_status_value,
    )
    console = Console(file=StringIO(), width=200, highlight=False)
    args = make_args(_DEFAULTS)
    execute_info(args, console=console)
    out = console.file.getvalue()
    assert expected_text in out
    if lockfile_status_value.status == LockfileStatus.OUT_OF_DATE:
        assert "dep missing" in out


@pytest.mark.parametrize(
    ("lockfile_status_value", "expected_status", "expect_reason"),
    [
        pytest.param(
            LockfileStatus(status=LockfileStatus.UP_TO_DATE),
            "up-to-date",
            False,
            id="up-to-date",
        ),
        pytest.param(
            LockfileStatus(status=LockfileStatus.OUT_OF_DATE, reason="dep missing"),
            "out-of-date",
            True,
            id="out-of-date",
        ),
    ],
)
def test_info_json_includes_lockfile_status(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    lockfile_status_value: LockfileStatus,
    expected_status: str,
    expect_reason: bool,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.info.lockfile_status",
        lambda ctx, config: lockfile_status_value,
    )
    console = Console(file=StringIO(), width=200, highlight=False)
    args = make_args(_DEFAULTS, json=True)
    execute_info(args, console=console)
    out = console.file.getvalue()
    data = json.loads(out)
    assert data["lockfile_status"] == expected_status
    if expect_reason:
        assert data["lockfile_reason"] == "dep missing"
    else:
        assert "lockfile_reason" not in data
