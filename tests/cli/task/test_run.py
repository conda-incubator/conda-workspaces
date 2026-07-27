"""Tests for ``conda task run``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from conda.base.context import Context
from conda.utils import quote_for_shell

import conda_workspaces.cli.task.run as run_mod
from conda_workspaces.cli.task.run import _resolve_task_args, execute_run
from conda_workspaces.context import WorkspaceContext
from conda_workspaces.exceptions import (
    CondaWorkspacesError,
    EnvironmentNotFoundError,
    EnvironmentNotInstalledError,
    TaskExecutionError,
    WorkspaceParseError,
)
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

    def _stub(args, env_name=None, *, required=False):
        if env_name is None:
            env_name = getattr(args, "environment", None)
        if env_name is not None and env_name in prefixes:
            return prefixes[env_name]
        if env_name is not None and required:
            raise EnvironmentNotInstalledError(env_name)
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


def test_execute_run_target_alias_default_env(
    workspace_task_file,
    fake_shell,
    env_prefix_stub,
    tmp_path,
):
    """A target alias provides the fallback environment for its commands."""
    workspace_task_file.write_text(
        '[workspace]\nname = "test"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n'
        '[tasks]\nsetup = "echo setup"\nlint = "echo lint"\n\n'
        '[tasks.check]\ndepends-on = ["setup", "lint"]\n'
        'default-environment = "myenv"\n'
    )
    selected_prefix = tmp_path / ".conda" / "envs" / "myenv"
    env_prefix_stub["myenv"] = selected_prefix

    assert execute_run(_run_args(workspace_task_file, task_name="check")) == 0
    assert [(call[0], call[3]) for call in fake_shell.calls] == [
        ("echo lint", selected_prefix),
        ("echo setup", selected_prefix),
    ]


@pytest.mark.parametrize(
    ("task_body", "expected"),
    [
        ('[tasks]\nbuild = "cmake --build ."\n', "cmake --build ."),
        ('[tasks.build]\ncmd = ["cmake", "--build", "."]\n', "cmake --build ."),
    ],
    ids=["shell-string", "argv-list"],
)
def test_execute_run_dry_run_command_display(tmp_path, capsys, task_body, expected):
    """Dry-run shows shell strings and list-form argv commands readably."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(task_body)

    result = execute_run(_run_args(task_file, task_name="build", dry_run=True))
    assert result == 0
    output = capsys.readouterr().out
    assert expected in output


@pytest.mark.parametrize(
    ("task_body", "task_name", "task_args", "expected_cmd"),
    [
        (
            (
                '[tasks.test]\ncmd = "python -m pytest {{ target }}"\n'
                'args = [{ arg = "target" }]\n'
            ),
            "test",
            ["tests/unit; echo ARG_PWN"],
            f"python -m pytest {quote_for_shell('tests/unit; echo ARG_PWN')}",
        ),
        (
            '[tasks.build]\ncmd = ["python", "-c", "print(1); echo ARRAY_PWN"]\n',
            "build",
            [],
            ["python", "-c", "print(1); echo ARRAY_PWN"],
        ),
    ],
    ids=["shell-template-arg-quoted", "argv-list-preserved"],
)
def test_execute_run_command_contract(
    tmp_path,
    fake_shell,
    task_body,
    task_name,
    task_args,
    expected_cmd,
):
    """Task run quotes shell-template args and preserves argv-list commands."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(task_body)

    result = execute_run(_run_args(task_file, task_name=task_name, task_args=task_args))

    assert result == 0
    assert fake_shell.calls[0][0] == expected_cmd


@pytest.mark.parametrize(
    "command_form",
    ["shell-template-arg", "argv-list"],
    ids=["shell-template-arg", "argv-list"],
)
def test_execute_run_treats_task_values_as_data(tmp_path, command_form):
    """Task values with shell metacharacters are passed as data."""
    recorder = (
        "from pathlib import Path; import sys; Path('seen.txt').write_text(sys.argv[1])"
    )
    pwned = "from pathlib import Path; Path('pwned.txt').write_text('bad')"
    payload = f'safe & python -c "{pwned}"'
    task_file = tmp_path / "conda.toml"
    if command_form == "shell-template-arg":
        command = f'python -c "{recorder}" {{{{ value }}}}'
        task_file.write_text(
            "[tasks.probe]\n"
            f"cmd = {json.dumps(command)}\n"
            'args = [{ arg = "value" }]\n'
        )
        task_args = [payload]
    else:
        task_file.write_text(
            f"[tasks.probe]\ncmd = {json.dumps(['python', '-c', recorder, payload])}\n"
        )
        task_args = []

    result = execute_run(
        _run_args(task_file, task_name="probe", task_args=task_args, cwd=tmp_path)
    )

    assert result == 0
    assert (tmp_path / "seen.txt").read_text() == payload
    assert not (tmp_path / "pwned.txt").exists()


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


@pytest.mark.parametrize(
    "environment_source",
    ["selected", "current"],
    ids=["selected", "current"],
)
def test_execute_run_saves_cache_for_effective_environment(
    workspace_task_file,
    fake_shell,
    env_prefix_stub,
    tmp_path,
    monkeypatch,
    environment_source,
):
    """Successful run caches against the environment that executes it."""
    effective_prefix = tmp_path / ".conda" / "envs" / environment_source
    if environment_source == "selected":
        workspace_task_file.write_text(
            '[workspace]\nname = "test"\nchannels = ["conda-forge"]\n'
            'platforms = ["linux-64"]\n\n[environments]\nmyenv = []\n\n'
            '[tasks.build]\ncmd = "make"\ndefault-environment = "myenv"\n'
            'inputs = ["src/*.py"]\noutputs = ["dist/"]\n'
        )
        env_prefix_stub["myenv"] = effective_prefix
        shell_prefix = effective_prefix
    else:
        workspace_task_file.write_text(
            '[tasks.build]\ncmd = "make"\ninputs = ["src/*.py"]\noutputs = ["dist/"]\n'
        )
        monkeypatch.setattr(
            Context,
            "target_prefix",
            property(lambda self: str(effective_prefix)),
        )
        shell_prefix = None

    cache_calls: list[tuple[str, dict]] = []

    def is_cached(*args, **kwargs):
        cache_calls.append(("load", kwargs))
        return False

    def save_cache(*args, **kwargs):
        cache_calls.append(("save", kwargs))

    monkeypatch.setattr(run_mod, "is_cached", is_cached)
    monkeypatch.setattr(run_mod, "save_cache", save_cache)
    result = execute_run(_run_args(workspace_task_file, task_name="build", quiet=True))

    assert result == 0
    assert cache_calls == [
        ("load", {"conda_prefix": effective_prefix}),
        ("save", {"conda_prefix": effective_prefix}),
    ]
    assert fake_shell.calls[0][3] == shell_prefix


@pytest.mark.parametrize("installed", [True, False], ids=["installed", "uninstalled"])
def test_execute_run_defaults_to_installed_workspace_env(
    workspace_task_file, fake_shell, env_prefix_stub, tmp_path, installed
):
    """The default workspace environment is used only when installed."""
    default_prefix = tmp_path / ".conda" / "envs" / "default"
    if installed:
        env_prefix_stub["default"] = default_prefix

    result = execute_run(_run_args(workspace_task_file, environment=None))
    assert result == 0
    assert fake_shell.calls[0][3] == (default_prefix if installed else None)


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


@pytest.mark.parametrize("dry_run", [False, True], ids=["execute", "dry-run"])
@pytest.mark.parametrize(
    "selector",
    ["cli", "task-default", "dependency", "adhoc"],
    ids=["cli", "task-default", "dependency", "adhoc"],
)
def test_execute_run_templates_use_selected_environment(
    workspace_task_file,
    fake_shell,
    env_prefix_stub,
    tmp_path,
    rich_console,
    selector,
    dry_run,
):
    template = (
        "echo {{ conda.prefix }} {{ conda.environment_name }} "
        "{{ conda.environment.name }}"
    )
    workspace = (
        '[workspace]\nname = "test"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n[environments]\nmyenv = []\n\n'
    )
    if selector == "cli":
        task_body = f'[tasks]\ngreet = "{template}"\n'
        overrides = {"environment": "myenv"}
    elif selector == "task-default":
        task_body = (
            f'[tasks.greet]\ncmd = "{template}"\ndefault-environment = "myenv"\n'
        )
        overrides = {}
    elif selector == "dependency":
        task_body = (
            "[tasks.setup]\n"
            'cmd = "echo {{ prefix }} {{ name }} {{ nested }}"\n'
            'args = [{ arg = "prefix" }, { arg = "name" }, '
            '{ arg = "nested" }]\n\n'
            '[tasks.greet]\ncmd = "echo hello"\n'
            'depends-on = [{ task = "setup", environment = "myenv", '
            'args = [{ prefix = "{{ conda.prefix }}", '
            'name = "{{ conda.environment_name }}", '
            'nested = "{{ conda.environment.name }}" }] }]\n'
        )
        overrides = {}
    else:
        task_body = '[tasks]\ngreet = "echo hello"\n'
        overrides = {
            "environment": "myenv",
            "task_name": template,
            "templated": True,
        }
    workspace_task_file.write_text(f"{workspace}{task_body}")
    selected_prefix = tmp_path / ".conda" / "envs" / "myenv"
    env_prefix_stub["myenv"] = selected_prefix
    expected_command = f"echo {selected_prefix} myenv myenv"

    result = execute_run(
        _run_args(workspace_task_file, dry_run=dry_run, **overrides),
        console=rich_console,
    )

    assert result == 0
    if dry_run:
        assert expected_command in rich_console.file.getvalue()
        assert fake_shell.calls == []
    else:
        assert (expected_command, selected_prefix) in [
            (call[0], call[3]) for call in fake_shell.calls
        ]


@pytest.mark.parametrize(
    ("workspace", "env_name", "error", "message"),
    [
        (True, "missing", EnvironmentNotFoundError, "Environment 'missing'"),
        (True, "myenv", EnvironmentNotInstalledError, "Environment 'myenv'"),
        (False, "myenv", WorkspaceParseError, r"No \[workspace\] table found"),
    ],
    ids=["undefined", "uninstalled", "tasks-only"],
)
@pytest.mark.parametrize(
    "selector",
    [
        "cli-task",
        "cli-adhoc",
        "task-default",
        "dependency",
        "transitive-dependency",
        "transitive-task-default",
    ],
    ids=[
        "cli-task",
        "cli-adhoc",
        "task-default",
        "dependency",
        "transitive-dependency",
        "transitive-task-default",
    ],
)
def test_execute_run_rejects_unavailable_selected_env(
    tmp_path,
    fake_shell,
    monkeypatch,
    selector,
    workspace,
    env_name,
    error,
    message,
):
    """Explicit environment selectors never fall back to the current shell."""
    if selector == "transitive-task-default":
        task_body = (
            f'[tasks.setup]\ncmd = "echo setup"\ndefault-environment = "{env_name}"\n\n'
            '[tasks.build]\ncmd = "echo build"\ndepends-on = ["setup"]\n\n'
            '[tasks.greet]\ncmd = "echo hello"\ndepends-on = ["build"]\n'
        )
        overrides = {}
    elif selector == "transitive-dependency":
        task_body = (
            '[tasks]\nsetup = "echo setup"\n\n'
            '[tasks.build]\ncmd = "echo build"\n'
            f'depends-on = [{{ task = "setup", environment = "{env_name}" }}]\n\n'
            '[tasks.greet]\ncmd = "echo hello"\ndepends-on = ["build"]\n'
        )
        overrides = {}
    elif selector == "dependency":
        task_body = (
            '[tasks]\nsetup = "echo setup"\n\n'
            '[tasks.greet]\ncmd = "echo hello"\n'
            f'depends-on = [{{ task = "setup", environment = "{env_name}" }}]\n'
        )
        overrides = {}
    elif selector == "task-default":
        task_body = (
            f'[tasks.greet]\ncmd = "echo hello"\ndefault-environment = "{env_name}"\n'
        )
        overrides = {}
    elif selector == "cli-task":
        task_body = '[tasks]\ngreet = "echo hello"\n'
        overrides = {"environment": env_name}
    else:
        task_body = '[tasks]\ngreet = "echo hello"\n'
        overrides = {"environment": env_name, "task_name": "echo goodbye"}

    task_file = tmp_path / "conda.toml"
    workspace_body = (
        '[workspace]\nname = "test"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n[environments]\nmyenv = []\n\n'
        if workspace
        else ""
    )
    task_file.write_text(f"{workspace_body}{task_body}")
    monkeypatch.setattr(WorkspaceContext, "env_exists", lambda self, name: False)

    with pytest.raises(error, match=message):
        execute_run(_run_args(task_file, **overrides))

    assert fake_shell.calls == []


@pytest.mark.parametrize(
    "environment_declaration",
    [
        'depends-on = [{ task = "setup", environment = "myenv" }]',
        'depends-on = ["setup"]',
    ],
    ids=["dependency-edge", "task-default"],
)
def test_execute_run_uses_transitive_dependency_selected_env(
    workspace_task_file,
    fake_shell,
    env_prefix_stub,
    tmp_path,
    environment_declaration,
):
    """A transitive dependency uses its explicitly selected environment."""
    task_default = (
        '\ndefault-environment = "myenv"'
        if environment_declaration == 'depends-on = ["setup"]'
        else ""
    )
    workspace_task_file.write_text(
        '[workspace]\nname = "test"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n'
        f'[tasks.setup]\ncmd = "echo setup"{task_default}\n\n'
        '[tasks.build]\ncmd = "echo build"\n'
        f"{environment_declaration}\n\n"
        '[tasks.greet]\ncmd = "echo hello"\ndepends-on = ["build"]\n'
    )
    selected_prefix = tmp_path / ".conda" / "envs" / "myenv"
    env_prefix_stub["myenv"] = selected_prefix

    result = execute_run(_run_args(workspace_task_file))

    assert result == 0
    assert [(call[0], call[3]) for call in fake_shell.calls] == [
        ("echo setup", selected_prefix),
        ("echo build", None),
        ("echo hello", None),
    ]


def test_execute_run_rejects_conflicting_shared_dependency_envs(
    workspace_task_file,
    fake_shell,
):
    """A shared task cannot execute once in two selected environments."""
    workspace_task_file.write_text(
        '[workspace]\nname = "test"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n'
        "[environments]\npy310 = []\npy311 = []\n\n"
        '[tasks]\nsetup = "echo setup"\n\n'
        '[tasks.build]\ncmd = "echo build"\n'
        'depends-on = [{ task = "setup", environment = "py310" }]\n\n'
        '[tasks.lint]\ncmd = "echo lint"\n'
        'depends-on = [{ task = "setup", environment = "py311" }]\n\n'
        '[tasks.greet]\ncmd = "echo hello"\ndepends-on = ["build", "lint"]\n'
    )

    with pytest.raises(
        CondaWorkspacesError,
        match="conflicting environments 'py310' and 'py311'",
    ):
        execute_run(_run_args(workspace_task_file))

    assert fake_shell.calls == []


@pytest.mark.parametrize(
    ("alias_definition", "dependency", "message"),
    [
        (
            'depends-on = ["setup"]',
            '{ task = "check", environment = "myenv" }',
            "cannot select environment 'myenv' for alias task 'check'",
        ),
        (
            'depends-on = ["setup"]\ndefault-environment = "myenv"',
            '"check"',
            "Alias task 'check' cannot use default-environment",
        ),
    ],
    ids=["dependency-edge", "nested-default"],
)
@pytest.mark.parametrize("environment", [None, "myenv"], ids=["implicit", "cli"])
def test_execute_run_rejects_environment_selector_on_dependency_alias(
    tmp_path,
    fake_shell,
    env_prefix_stub,
    alias_definition,
    dependency,
    message,
    environment,
):
    """An alias selector cannot silently choose a shell for its commands."""
    task_file = tmp_path / "conda.toml"
    task_file.write_text(
        '[workspace]\nname = "test"\nchannels = ["conda-forge"]\n'
        'platforms = ["linux-64"]\n\n[environments]\nmyenv = []\n\n'
        '[tasks]\nsetup = "echo setup"\n\n'
        f"[tasks.check]\n{alias_definition}\n\n"
        f'[tasks.greet]\ncmd = "echo hello"\ndepends-on = [{dependency}]\n'
    )
    env_prefix_stub["myenv"] = tmp_path / ".conda" / "envs" / "myenv"

    with pytest.raises(CondaWorkspacesError, match=message):
        execute_run(_run_args(task_file, environment=environment))

    assert fake_shell.calls == []
