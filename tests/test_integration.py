"""Integration tests for end-to-end CLI workflows.

Exercises the full CLI path from ``conda workspace`` and ``conda task``
through conda's plugin dispatch, monkeypatching only at the system
boundary (solver, subprocess, conda run).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from conda.testing.fixtures import CondaCLIFixture

pytestmark = pytest.mark.integration


@pytest.fixture
def conda_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Write a conda.toml and chdir to its parent."""

    def _write(content: str) -> Path:
        path = tmp_path / "conda.toml"
        path.write_text(content, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        return path

    return _write


WORKSPACE_TOML = """\
[workspace]
name = "integ-test"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"

[environments]
default = []
"""


def test_workspace_install_dry_run(
    conda_toml,
    conda_cli: CondaCLIFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """Workspace install with --dry-run parses manifest and emits status."""
    monkeypatch.delenv("CI", raising=False)
    conda_toml(WORKSPACE_TOML)

    install_calls = []

    def fake_install(ctx, resolved, *, force_reinstall=False, dry_run=False):
        install_calls.append(
            {"env": resolved.name, "dry_run": dry_run, "force": force_reinstall}
        )

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        fake_install,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.generate_lockfile",
        lambda ctx, envs: None,
    )

    stdout, stderr, exit_code = conda_cli("workspace", "install", "--dry-run")

    assert exit_code == 0
    assert len(install_calls) == 1
    assert install_calls[0]["env"] == "default"
    assert install_calls[0]["dry_run"] is True
    assert "Installing" in stdout
    assert "Installed" in stdout


def test_task_run_with_dependencies(
    conda_toml,
    conda_cli: CondaCLIFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """Task run resolves dependencies and executes them in order."""
    conda_toml("""\
[tasks]
lint = "echo linting"
test = "echo testing"

[tasks.check]
cmd = "echo all done"
depends-on = ["lint", "test"]
""")

    executed_cmds = []

    def fake_run(self, cmd, env, cwd, conda_prefix=None, clean_env=False):
        executed_cmds.append(cmd)
        return 0

    monkeypatch.setattr(
        "conda_workspaces.cli.task.run.SubprocessShell.run",
        fake_run,
    )

    stdout, stderr, exit_code = conda_cli("task", "run", "check")

    assert exit_code == 0
    assert len(executed_cmds) == 3
    assert "echo linting" in executed_cmds[0]
    assert "echo testing" in executed_cmds[1]
    assert "echo all done" in executed_cmds[2]
    assert "Running" in stdout
    assert "Finished" in stdout


def test_task_run_with_templates(
    conda_toml,
    conda_cli: CondaCLIFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """Task run resolves Jinja2 template variables in commands."""
    from conda.base.context import context

    expected_subdir = context.subdir

    conda_toml("""\
[tasks]
info = "echo running on {{ conda.platform }}"
""")

    executed_cmds = []

    def fake_run(self, cmd, env, cwd, conda_prefix=None, clean_env=False):
        executed_cmds.append(cmd)
        return 0

    monkeypatch.setattr(
        "conda_workspaces.cli.task.run.SubprocessShell.run",
        fake_run,
    )

    stdout, stderr, exit_code = conda_cli("task", "run", "info")

    assert exit_code == 0
    assert len(executed_cmds) == 1
    assert expected_subdir in executed_cmds[0]
    assert "{{" not in executed_cmds[0]


def test_task_run_caching(
    conda_toml,
    conda_cli: CondaCLIFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Task with inputs/outputs is cached on second run."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')", encoding="utf-8")

    dist = tmp_path / "dist"
    dist.mkdir()

    conda_toml("""\
[tasks.build]
cmd = "cp src/main.py dist/main.py"
inputs = ["src/main.py"]
outputs = ["dist/main.py"]
""")

    run_count = 0

    def fake_run(self, cmd, env, cwd, conda_prefix=None, clean_env=False):
        nonlocal run_count
        run_count += 1
        (tmp_path / "dist" / "main.py").write_text("built", encoding="utf-8")
        return 0

    monkeypatch.setattr(
        "conda_workspaces.cli.task.run.SubprocessShell.run",
        fake_run,
    )

    stdout1, _, exit_code1 = conda_cli("task", "run", "build")
    assert exit_code1 == 0
    assert run_count == 1

    stdout2, _, exit_code2 = conda_cli("task", "run", "build")
    assert exit_code2 == 0
    assert run_count == 1
    assert "Skipped" in stdout2


def test_workspace_run_dispatches_command(
    conda_toml,
    conda_cli: CondaCLIFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_workspace_env,
):
    """Workspace run dispatches the command through conda run."""
    path = conda_toml(WORKSPACE_TOML)
    tmp_workspace_env(path.parent, "default")

    called_with = []

    def fake_conda_run(args, parser):
        called_with.append(args.executable_call)
        return 0

    monkeypatch.setattr("conda.cli.main_run.execute", fake_conda_run)

    stdout, stderr, exit_code = conda_cli(
        "workspace", "run", "-e", "default", "--", "echo", "hello"
    )

    assert exit_code == 0
    assert called_with == [["echo", "hello"]]


@pytest.mark.parametrize(
    "scenario, expected_verbs",
    [
        ("install", ["Installing", "Installed"]),
        ("task_run", ["Running", "Finished"]),
    ],
    ids=["workspace-install", "task-run"],
)
def test_rich_output_contains_status(
    conda_toml,
    conda_cli: CondaCLIFixture,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_verbs: list[str],
):
    """Rich output contains verb-based status messages."""
    monkeypatch.delenv("CI", raising=False)
    if scenario == "install":
        conda_toml(WORKSPACE_TOML)

        monkeypatch.setattr(
            "conda_workspaces.cli.workspace.sync.install_environment",
            lambda ctx, resolved, *, force_reinstall=False, dry_run=False: None,
        )
        monkeypatch.setattr(
            "conda_workspaces.cli.workspace.sync.generate_lockfile",
            lambda ctx, envs: None,
        )

        stdout, _, _ = conda_cli("workspace", "install", "--dry-run")

    elif scenario == "task_run":
        conda_toml("""\
[tasks]
lint = "echo ok"

[tasks.check]
cmd = "echo done"
depends-on = ["lint"]
""")

        monkeypatch.setattr(
            "conda_workspaces.cli.task.run.SubprocessShell.run",
            lambda self, cmd, env, cwd, **kw: 0,
        )

        stdout, _, _ = conda_cli("task", "run", "check")

    for verb in expected_verbs:
        assert verb in stdout, f"Expected '{verb}' in output: {stdout!r}"


PYPI_WORKSPACE_TOML = """\
[workspace]
name = "pypi-integ-test"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"

[pypi-dependencies]
six = ">=1.16"

[environments]
default = []
"""


def test_workspace_install_resolves_pypi_deps(
    conda_toml,
    conda_cli: CondaCLIFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """PyPI dependencies are translated via conda-pypi and reach the solver."""
    monkeypatch.delenv("CI", raising=False)
    try:
        from conda_pypi.translate import pypi_to_conda_name  # noqa: F401
    except ImportError:
        pytest.skip("conda-pypi not installed")

    import importlib.util

    if importlib.util.find_spec("conda_rattler_solver") is None:
        pytest.skip("conda-rattler-solver not installed")

    conda_toml(PYPI_WORKSPACE_TOML)

    resolved_envs = []

    def capture_install(ctx, resolved, *, force_reinstall=False, dry_run=False):
        resolved_envs.append(resolved)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        capture_install,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.generate_lockfile",
        lambda ctx, envs: None,
    )

    stdout, stderr, exit_code = conda_cli("workspace", "install", "--dry-run")

    assert exit_code == 0
    assert len(resolved_envs) == 1

    resolved = resolved_envs[0]
    assert "six" in resolved.pypi_dependencies
    dep = resolved.pypi_dependencies["six"]
    assert dep.name == "six"
    assert dep.spec == ">=1.16"

    conda_dep_names = {str(s.name) for s in resolved.conda_dependencies.values()}
    assert "python" in conda_dep_names


PYPI_EDITABLE_WORKSPACE_TOML = """\
[workspace]
name = "editable-integ-test"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"

[pypi-dependencies]
my-pkg = { path = ".", editable = true }

[environments]
default = []
"""


def test_workspace_install_resolves_editable_deps(
    conda_toml,
    conda_cli: CondaCLIFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Editable PyPI path deps are parsed and available in the resolved env."""
    monkeypatch.delenv("CI", raising=False)
    try:
        from conda_pypi.translate import pypi_to_conda_name  # noqa: F401
    except ImportError:
        pytest.skip("conda-pypi not installed")

    conda_toml(PYPI_EDITABLE_WORKSPACE_TOML)

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "my-pkg"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    resolved_envs = []

    def capture_install(ctx, resolved, *, force_reinstall=False, dry_run=False):
        resolved_envs.append(resolved)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.install_environment",
        capture_install,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.generate_lockfile",
        lambda ctx, envs: None,
    )

    stdout, stderr, exit_code = conda_cli("workspace", "install", "--dry-run")

    assert exit_code == 0
    assert len(resolved_envs) == 1

    resolved = resolved_envs[0]
    assert "my-pkg" in resolved.pypi_dependencies
    dep = resolved.pypi_dependencies["my-pkg"]
    assert dep.editable is True
    assert dep.path == "."
