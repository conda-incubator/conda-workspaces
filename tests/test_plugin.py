"""Tests for conda_workspaces.plugin."""

from __future__ import annotations

import pytest

from conda_workspaces import env_spec, lockfile
from conda_workspaces.plugin import (
    conda_environment_exporters,
    conda_environment_specifiers,
    conda_pre_commands,
    conda_subcommands,
)


def test_conda_subcommands_yields_workspace_and_task() -> None:
    items = {sub.name: sub for sub in conda_subcommands()}
    assert "workspace" in items
    assert "task" in items
    for sub in items.values():
        assert sub.summary
        assert callable(sub.action)
        assert callable(sub.configure_parser)


@pytest.mark.parametrize(
    ("name", "expected_cls_name"),
    [
        (env_spec.FORMAT, "CondaWorkspaceSpec"),
        (lockfile.FORMAT, "CondaLockLoader"),
    ],
    ids=["conda-workspaces", "conda-workspaces-lock-v1"],
)
def test_conda_environment_specifiers(name: str, expected_cls_name: str) -> None:
    items = {s.name: s for s in conda_environment_specifiers()}
    assert name in items
    assert items[name].environment_spec.__name__ == expected_cls_name


def test_conda_environment_exporters_yields_lockfile_and_manifests() -> None:
    """The hook yields the ``conda.lock`` exporter plus one per manifest format."""
    items = {exp.name: exp for exp in conda_environment_exporters()}
    assert set(items) == {
        lockfile.FORMAT,
        "conda-toml",
        "pixi-toml",
        "pyproject-toml",
    }

    lock_exp = items[lockfile.FORMAT]
    assert lock_exp.aliases == lockfile.ALIASES
    assert lock_exp.default_filenames == lockfile.DEFAULT_FILENAMES
    assert callable(lock_exp.multiplatform_export)

    for name, filename in [
        ("conda-toml", "conda.toml"),
        ("pixi-toml", "pixi.toml"),
        ("pyproject-toml", "pyproject.toml"),
    ]:
        exp = items[name]
        assert exp.default_filenames == (filename,)
        assert callable(exp.multiplatform_export)


def test_conda_pre_commands_yields_install_hint() -> None:
    items = list(conda_pre_commands())
    assert len(items) == 1
    hook = items[0]
    assert hook.name == "conda-workspaces-install-hint"
    assert "install" in hook.run_for
    assert callable(hook.action)
