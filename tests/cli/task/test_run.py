"""Tests for ``conda task run``."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import conda_workspaces.cli.task.run as run_mod
from conda_workspaces.cli.task.run import _resolve_task_args, execute_run
from conda_workspaces.exceptions import CondaWorkspacesError, TaskExecutionError
from conda_workspaces.models import Task, TaskArg


def _run_args(
    task_file: Path, task_name: str = "greet", **overrides
) -> argparse.Namespace:
    """Build an argparse.Namespace suitable for execute_run."""
    defaults = dict(
        file=task_file,
        task_name=task_name,
        task_args=[],
        skip_deps=False,
        dry_run=False,
        quiet=False,
        verbosity=0,
        clean_env=False,
        cwd=None,
        environment=None,
        templated=False,
        json=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class FakeShell:
    """Test double for SubprocessShell that records calls."""

    def __init__(self, return_code: int = 0):
        self.calls: list[tuple] = []
        self.return_code = return_code

    def run(self, cmd, env, cwd, conda_prefix=None, clean_env=False):
        self.calls.append((cmd, env, cwd, conda_prefix, clean_env))
        return self.return_code


@pytest.fixture
def fake_shell(monkeypatch):
    """Patch SubprocessShell and return the FakeShell instance."""
    shell = FakeShell()
    monkeypatch.setattr(run_mod, "SubprocessShell", lambda: shell)
    return shell


@pytest.fixture
def workspace_task_file(tmp_path):
    """A conda.toml with both a workspace definition and a simple task."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[workspace]\nname = "test"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64", "osx-arm64"]\n\n'
        '[tasks]\ngreet = "echo hello"\n'
    )
    return task_file


@pytest.fixture
def env_prefix_stub(monkeypatch):
    """Patch _env_prefix_or_none to return deterministic prefixes.

    Returns a dict mapping env names to prefix paths; add entries
    before calling execute_run.
    """
    prefixes: dict[str, Path] = {}

    def _stub(args, env_name=None):
        if env_name is None:
            env_name = getattr(args, "environment", None)
        if env_name and env_name in prefixes:
            return prefixes[env_name]
        return None

    monkeypatch.setattr(run_mod, "_env_prefix_or_none", _stub)
    return prefixes


@pytest.mark.parametrize(
    ("args_def", "cli_args", "expected"),
    [
        ([], [], {}),
        ([TaskArg(name="path", default="tests/")], [], {"path": "tests/"}),
        ([TaskArg(name="path", default="tests/")], ["src/"], {"path": "src/"}),
        (
            [TaskArg(name="a"), TaskArg(name="b", default="y")],
            ["x"],
            {"a": "x", "b": "y"},
        ),
    ],
    ids=["no-args", "default", "override", "mixed"],
)
def test_resolve_task_args(args_def, cli_args, expected):
    task = Task(name="t", cmd="echo", args=args_def)
    assert _resolve_task_args(task, cli_args) == expected


def test_resolve_task_args_missing_required():
    task = Task(name="t", cmd="echo", args=[TaskArg(name="path")])
    with pytest.raises(CondaWorkspacesError, match="Missing required argument"):
        _resolve_task_args(task, [])


def test_execute_run_dry_run_single(tmp_path, capsys):
    """Dry-run of a single task shows 'Would run' with command."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text('[tasks]\ngreet = "echo hello"\n')

    result = execute_run(_run_args(task_file, dry_run=True))
    assert result == 0
    output = capsys.readouterr().out
    assert "Would run" in output
    assert "greet" in output
    assert "echo hello" in output


def test_execute_run_dry_run_with_deps(tmp_path, capsys):
    """Dry-run with deps shows a tree with 'Would run' labels."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks]\nsetup = "echo setup"\n\n'
        '[tasks.build]\ncmd = "echo build"\ndepends-on = ["setup"]\n'
    )
    result = execute_run(_run_args(task_file, task_name="build", dry_run=True))
    assert result == 0
    output = capsys.readouterr().out
    assert "Would run" in output
    assert "build" in output
    assert "setup" in output


def test_execute_run_dry_run_alias(tmp_path, capsys):
    """Alias tasks appear as root of the dry-run tree."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks]\nlint = "ruff check ."\ntest = "pytest"\n\n'
        '[tasks.check]\ndepends-on = ["lint", "test"]\n'
    )

    result = execute_run(_run_args(task_file, task_name="check", dry_run=True))
    assert result == 0
    output = capsys.readouterr().out
    assert "Would run" in output
    assert "check" in output
    assert "lint" in output
    assert "test" in output


def test_execute_run_alias_done(tmp_path, capsys, fake_shell):
    """Alias tasks show Finished after dependencies finish executing."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks]\nlint = "ruff check ."\ntest = "pytest"\n\n'
        '[tasks.check]\ndepends-on = ["lint", "test"]\n'
    )

    result = execute_run(_run_args(task_file, task_name="check"))

    assert result == 0
    output = capsys.readouterr().out
    assert "Running" in output
    assert "Finished" in output
    assert "check" in output


def test_execute_run_alias_quiet(tmp_path, capsys, fake_shell):
    """Quiet mode suppresses all output for alias tasks."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks]\nlint = "ruff check ."\n\n[tasks.check]\ndepends-on = ["lint"]\n'
    )

    result = execute_run(_run_args(task_file, task_name="check", quiet=True))

    assert result == 0
    assert capsys.readouterr().out == ""


def test_execute_run_list_command(tmp_path, capsys):
    """List-form commands are joined for dry-run display."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text('[tasks]\nbuild = "cmake --build ."\n')

    result = execute_run(_run_args(task_file, task_name="build", dry_run=True))
    assert result == 0
    output = capsys.readouterr().out
    assert "cmake --build ." in output


def test_execute_run_single_zero_chrome(tmp_path, capsys, fake_shell):
    """Single task execution produces no status output (zero chrome)."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text('[tasks]\ngreet = "echo hello"\n')

    result = execute_run(_run_args(task_file))

    assert result == 0
    assert capsys.readouterr().out == ""
    assert len(fake_shell.calls) == 1


def test_execute_run_quiet(tmp_path, capsys, fake_shell):
    """Quiet mode suppresses output."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text('[tasks]\ngreet = "echo hello"\n')

    result = execute_run(_run_args(task_file, quiet=True))

    assert result == 0
    assert capsys.readouterr().out == ""


def test_execute_run_failure(tmp_path, fake_shell):
    """Non-zero exit raises TaskExecutionError."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text('[tasks]\nfail = "exit 1"\n')

    fake_shell.return_code = 1
    with pytest.raises(TaskExecutionError, match="fail"):
        execute_run(_run_args(task_file, task_name="fail"))


def test_execute_run_failure_with_deps(tmp_path, capsys, fake_shell):
    """Failed task in a dep chain shows Failed status."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks]\nsetup = "echo setup"\n\n'
        '[tasks.build]\ncmd = "make"\ndepends-on = ["setup"]\n'
    )

    def selective_fail(cmd, env, cwd, conda_prefix=None, clean_env=False):
        fake_shell.calls.append((cmd, env, cwd, conda_prefix, clean_env))
        return 1 if cmd == "make" else 0

    fake_shell.run = selective_fail
    with pytest.raises(TaskExecutionError, match="build"):
        execute_run(_run_args(task_file, task_name="build"))

    output = capsys.readouterr().out
    assert "Failed" in output
    assert "build" in output


def test_execute_run_dep_chain_markers(tmp_path, capsys, fake_shell):
    """Dep chain shows Running and Finished for each task."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks]\nsetup = "echo setup"\n\n'
        '[tasks.build]\ncmd = "echo build"\ndepends-on = ["setup"]\n'
    )

    result = execute_run(_run_args(task_file, task_name="build"))

    assert result == 0
    output = capsys.readouterr().out
    assert "Running" in output
    assert "Finished" in output
    assert "setup" in output
    assert "build" in output


def test_execute_run_dep_chain_verbose(tmp_path, capsys, fake_shell):
    """Verbose mode adds command text to Running status."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks]\nsetup = "echo setup"\n\n'
        '[tasks.build]\ncmd = "echo build"\ndepends-on = ["setup"]\n'
    )

    result = execute_run(_run_args(task_file, task_name="build", verbosity=1))

    assert result == 0
    output = capsys.readouterr().out
    assert "echo setup" in output
    assert "echo build" in output


def test_execute_run_verbose_with_io(tmp_path, capsys, fake_shell, monkeypatch):
    """Verbose mode prints inputs/outputs for tasks in a dep chain."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks]\nsetup = "echo ready"\n\n'
        '[tasks.build]\ncmd = "make"\ninputs = ["src/*.py"]\n'
        'outputs = ["dist/"]\ndepends-on = ["setup"]\n'
    )

    monkeypatch.setattr(run_mod, "is_cached", lambda *a, **kw: False)
    result = execute_run(_run_args(task_file, task_name="build", verbosity=1))

    assert result == 0
    output = capsys.readouterr().out
    assert "inputs:" in output
    assert "outputs:" in output


def test_execute_run_cached_in_dep_chain(tmp_path, capsys, fake_shell, monkeypatch):
    """Cached tasks in a dep chain show Skipped status."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks.lint]\ncmd = "ruff"\ninputs = ["src/*.py"]\noutputs = [".lint"]\n\n'
        '[tasks.build]\ncmd = "make"\ndepends-on = ["lint"]\n'
    )

    monkeypatch.setattr(run_mod, "is_cached", lambda *a, **kw: True)
    result = execute_run(_run_args(task_file, task_name="build"))

    assert result == 0
    output = capsys.readouterr().out
    assert "Skipped" in output
    assert "cached" in output


def test_execute_run_cached_single_shows_marker(
    tmp_path, capsys, fake_shell, monkeypatch
):
    """Cached single task (no deps) shows Skipped status."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks.build]\ncmd = "make"\ninputs = ["src/*.py"]\noutputs = ["dist/"]\n'
    )

    monkeypatch.setattr(run_mod, "is_cached", lambda *a, **kw: True)
    result = execute_run(_run_args(task_file, task_name="build"))

    assert result == 0
    output = capsys.readouterr().out
    assert "Skipped" in output
    assert "cached" in output


def test_execute_run_with_cwd_override(tmp_path, capsys, fake_shell):
    """--cwd overrides the task's working directory."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text('[tasks]\ngreet = "echo hello"\n')
    subdir = tmp_path / "sub"
    subdir.mkdir()

    result = execute_run(_run_args(task_file, cwd=subdir))

    assert result == 0
    assert len(fake_shell.calls) == 1
    assert Path(fake_shell.calls[0][2]) == subdir


def test_execute_run_saves_cache(tmp_path, fake_shell, monkeypatch):
    """Successful run with inputs/outputs saves to cache."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[tasks.build]\ncmd = "make"\ninputs = ["src/*.py"]\noutputs = ["dist/"]\n'
    )

    save_calls: list[tuple] = []
    monkeypatch.setattr(run_mod, "is_cached", lambda *a, **kw: False)
    monkeypatch.setattr(run_mod, "save_cache", lambda *a, **kw: save_calls.append(a))
    result = execute_run(_run_args(task_file, task_name="build", quiet=True))

    assert result == 0
    assert len(save_calls) == 1


def test_execute_run_defaults_to_workspace_env(
    workspace_task_file, fake_shell, env_prefix_stub, tmp_path
):
    """When no -e flag is given, tasks run in the workspace default env."""
    default_prefix = tmp_path / ".conda" / "envs" / "default"
    env_prefix_stub["default"] = default_prefix

    result = execute_run(_run_args(workspace_task_file, environment=None))
    assert result == 0
    assert fake_shell.calls[0][3] == default_prefix


def test_execute_run_explicit_env_overrides_default(
    workspace_task_file, fake_shell, env_prefix_stub, tmp_path
):
    """The -e flag overrides the workspace default env."""
    env_prefix_stub["default"] = tmp_path / ".conda" / "envs" / "default"
    test_prefix = tmp_path / ".conda" / "envs" / "myenv"
    env_prefix_stub["myenv"] = test_prefix

    result = execute_run(_run_args(workspace_task_file, environment="myenv"))
    assert result == 0
    assert fake_shell.calls[0][3] == test_prefix
