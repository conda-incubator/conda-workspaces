"""Tests for conda_workspaces.cli.workspace.shell."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conda.common.compat import on_win

from conda_workspaces.cli.workspace.shell import execute_shell
from conda_workspaces.exceptions import (
    CondaWorkspacesError,
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
)

from ..conftest import make_args

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import CreateWorkspaceEnv

_DEFAULTS = {"manifest_file": None, "environment": "default", "cmd": [], "cwd": None}


@pytest.fixture
def shell_workspace(
    pixi_workspace: Path,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> Callable[[str], tuple[Path, Path]]:
    def create(name: str) -> tuple[Path, Path]:
        workspace = pixi_workspace / name
        workspace.mkdir(parents=True)
        (workspace / "pixi.toml").write_bytes(
            (pixi_workspace / "pixi.toml").read_bytes()
        )
        prefix = tmp_workspace_env(workspace, "default")
        return workspace, prefix

    return create


@pytest.mark.parametrize(
    "env_name, expected_path_part",
    [
        ("default", "default"),
        ("test", "test"),
    ],
    ids=["default-env", "named-env"],
)
def test_shell_spawns_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
    env_name: str,
    expected_path_part: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, env_name)

    spawn_calls: list[dict] = []

    def fake_spawn(*, prefix, command=None, shell_cls=None):
        spawn_calls.append({"prefix": prefix, "command": command})
        return 0

    monkeypatch.setattr("conda_spawn.main.spawn", fake_spawn)

    args = make_args(_DEFAULTS, environment=env_name)
    result = execute_shell(args)
    assert result == 0
    assert len(spawn_calls) == 1
    assert expected_path_part in str(spawn_calls[0]["prefix"])
    assert spawn_calls[0]["command"] is None


@pytest.mark.parametrize(
    "cmd_input, expected_command",
    [
        (["--", "python", "-c", "print(1)"], ["python", "-c", "print(1)"]),
        (["python"], ["python"]),
        (["--", "echo", "hello"], ["echo", "hello"]),
        ([], None),
    ],
    ids=["strips-dashdash", "no-dashdash", "dashdash-multi-arg", "empty-cmd"],
)
def test_shell_command_passthrough(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
    cmd_input: list[str],
    expected_command: list[str] | None,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    spawn_calls: list[dict] = []

    def fake_spawn(*, prefix, command=None, shell_cls=None):
        spawn_calls.append({"prefix": prefix, "command": command})
        return 0

    monkeypatch.setattr("conda_spawn.main.spawn", fake_spawn)

    args = make_args(_DEFAULTS, cmd=cmd_input)
    result = execute_shell(args)
    assert result == 0
    assert spawn_calls[0]["command"] == expected_command


@pytest.mark.parametrize(
    "env_name, exc_type",
    [
        ("default", EnvironmentNotInstalledError),
        ("nonexistent", EnvironmentNotFoundError),
    ],
    ids=["not-installed", "unknown-env"],
)
def test_shell_error(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    exc_type: type,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    args = make_args(_DEFAULTS, environment=env_name)
    with pytest.raises(exc_type):
        execute_shell(args)


def test_shell_propagates_exit_code(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env: CreateWorkspaceEnv,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    tmp_workspace_env(pixi_workspace, "default")

    def fake_spawn(*, prefix, command=None, shell_cls=None):
        return 42

    monkeypatch.setattr("conda_spawn.main.spawn", fake_spawn)

    args = make_args(_DEFAULTS)
    assert execute_shell(args) == 42


@pytest.mark.parametrize(
    "name",
    [
        "quoted'workspace",
        "dollar$workspace",
        "backtick`workspace",
        "percent%workspace",
        "bang!workspace",
        "quoted'parent/safe-workspace",
    ],
    ids=["quote", "dollar", "backtick", "percent", "bang", "parent-quote"],
)
@pytest.mark.parametrize("requested_cwd", [False, True], ids=["direct", "bootstrap"])
def test_shell_rejects_unsafe_activation_prefix(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    shell_workspace: Callable[[str], tuple[Path, Path]],
    name: str,
    requested_cwd: bool,
) -> None:
    workspace, _ = shell_workspace(name)
    monkeypatch.chdir(pixi_workspace)

    def unexpected_spawn(**kwargs: object) -> int:
        pytest.fail("Unsafe prefix reached shell startup")

    monkeypatch.setattr("conda_spawn.main.spawn", unexpected_spawn)
    args = make_args(
        _DEFAULTS,
        manifest_file=workspace / "pixi.toml",
        cwd=workspace if requested_cwd else None,
    )
    with pytest.raises(CondaWorkspacesError):
        execute_shell(args)
    assert Path.cwd() == pixi_workspace


@pytest.mark.skipif(on_win, reason="Bash activation script")
@pytest.mark.parametrize(
    "name", ["space project", "über projekt"], ids=["space", "unicode"]
)
def test_shell_preserves_safe_activation_paths(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    shell_workspace: Callable[[str], tuple[Path, Path]],
    name: str,
) -> None:
    from conda_spawn.shell import BashShell

    workspace, prefix = shell_workspace(name)
    monkeypatch.chdir(pixi_workspace)
    monkeypatch.setattr("conda_spawn.main.detect_shell_class", lambda: BashShell)
    scripts: list[str] = []

    def inspect_spawn(
        *,
        prefix: Path,
        command: list[str] | None = None,
        shell_cls: type[BashShell] | None = None,
    ) -> int:
        assert shell_cls is not None
        shell = shell_cls(prefix)
        assert Path(shell.executable()).is_absolute()
        assert Path.cwd() == workspace
        scripts.append(shell._spawn_script())
        return 0

    monkeypatch.setattr("conda_spawn.main.spawn", inspect_spawn)
    args = make_args(_DEFAULTS, manifest_file=workspace / "pixi.toml", cwd=workspace)
    assert execute_shell(args) == 0
    assert f"export CONDA_PREFIX='{prefix}'" in scripts[0]
    assert Path.cwd() == pixi_workspace


@pytest.mark.parametrize("shell_name", ["cmd", "powershell"])
@pytest.mark.parametrize("fail", [False, True], ids=["success", "failure"])
@pytest.mark.parametrize(
    "path_entry", ["none", "relative", "workspace", "symlink", "trusted-alias"]
)
def test_shell_pins_windows_shell_process_executable(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    shell_workspace: Callable[[str], tuple[Path, Path]],
    shell_name: str,
    fail: bool,
    path_entry: str,
) -> None:
    from conda_spawn.shell import CmdExeShell, PowershellShell

    workspace, _ = shell_workspace("restored")
    trusted = pixi_workspace / "trusted"
    trusted.mkdir()
    name = shell_name + (".exe" if on_win else "")
    executable = trusted / name
    executable.write_text("trusted shell")
    executable.chmod(0o755)
    shadow = workspace / name
    shadow.write_text("archive shell")
    shadow.chmod(0o755)
    if path_entry == "trusted-alias":
        actual = trusted / "actual-shell"
        executable.rename(actual)
        try:
            executable.symlink_to(actual)
        except OSError:
            pytest.skip("Creating symbolic links requires platform permission")
    monkeypatch.chdir(pixi_workspace)
    search_path = [str(trusted)]
    if path_entry == "relative":
        search_path.insert(0, "restored")
    elif path_entry == "workspace":
        search_path.insert(0, str(workspace))
    elif path_entry == "symlink":
        linked_bin = pixi_workspace / "linked-bin"
        linked_bin.mkdir()
        try:
            (linked_bin / name).symlink_to(shadow)
        except OSError:
            pytest.skip("Creating symbolic links requires platform permission")
        search_path.insert(0, str(linked_bin))
    monkeypatch.setenv("PATH", os.pathsep.join(search_path))
    shell_class = CmdExeShell if shell_name == "cmd" else PowershellShell
    monkeypatch.setattr("conda_spawn.main.detect_shell_class", lambda: shell_class)
    processes: list[tuple[str, Path]] = []

    class Process:
        def communicate(self) -> tuple[str, str]:
            if fail:
                raise OSError("Shell failed")
            return "", ""

        def wait(self) -> int:
            return 17

    def record_popen(argv: list[str], **kwargs: object) -> Process:
        processes.append((argv[0], Path.cwd()))
        assert not kwargs.get("shell")
        return Process()

    monkeypatch.setattr("conda_spawn.shell.subprocess.Popen", record_popen)
    args = make_args(_DEFAULTS, manifest_file=workspace / "pixi.toml", cwd=workspace)
    if fail:
        with pytest.raises(OSError, match="Shell failed"):
            execute_shell(args)
    else:
        assert execute_shell(args) == 17
    assert processes == [(str(executable), workspace)]
    assert Path.cwd() == pixi_workspace
