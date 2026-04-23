"""Tests for conda_workspaces.cli.main — parser configuration and dispatch."""

from __future__ import annotations

import argparse
import importlib

import pytest

from conda_workspaces.cli.main import (
    execute_task,
    execute_workspace,
    generate_task_parser,
    generate_workspace_parser,
)


def test_generate_workspace_parser_returns_parser() -> None:
    parser = generate_workspace_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    assert "workspace" in parser.prog


def test_generate_task_parser_returns_parser() -> None:
    parser = generate_task_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    assert "task" in parser.prog


@pytest.mark.parametrize(
    "subcmd",
    [
        "init",
        "install",
        "lock",
        "list",
        "envs",
        "info",
        "add",
        "remove",
        "clean",
        "activate",
        "shell",
    ],
)
def test_workspace_subcommands_registered(subcmd: str) -> None:
    parser = generate_workspace_parser()
    if subcmd in ("add", "remove"):
        args = parser.parse_args([subcmd, "numpy"])
    else:
        args = parser.parse_args([subcmd])
    assert args.subcmd == subcmd


@pytest.mark.parametrize(
    "subcmd",
    ["run", "list", "add", "remove", "export"],
)
def test_task_subcommands_registered(subcmd: str) -> None:
    parser = generate_task_parser()
    if subcmd == "run":
        args = parser.parse_args([subcmd, "test"])
    elif subcmd == "add":
        args = parser.parse_args([subcmd, "lint", "ruff check ."])
    elif subcmd == "remove":
        args = parser.parse_args([subcmd, "lint"])
    else:
        args = parser.parse_args([subcmd])
    assert args.subcmd == subcmd


def test_workspace_no_subcmd_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = generate_workspace_parser()
    args = parser.parse_args([])
    result = execute_workspace(args)
    assert result == 0
    captured = capsys.readouterr()
    assert "workspace" in captured.out.lower() or "usage" in captured.out.lower()


def test_task_no_subcmd_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = generate_task_parser()
    args = parser.parse_args([])
    result = execute_task(args)
    assert result == 0
    captured = capsys.readouterr()
    assert "task" in captured.out.lower() or "usage" in captured.out.lower()


def test_workspace_unknown_subcmd_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = generate_workspace_parser()
    args = parser.parse_args([])
    args.subcmd = "nonexistent"
    result = execute_workspace(args)
    assert result == 0


@pytest.mark.parametrize(
    "args, expected_attr, expected_value",
    [
        (["init", "--format", "conda"], "manifest_format", "conda"),
        (["init", "--format", "pyproject"], "manifest_format", "pyproject"),
        (["init", "--name", "myproj"], "name", "myproj"),
        (
            ["init", "-c", "defaults", "-c", "bioconda"],
            "channels",
            ["defaults", "bioconda"],
        ),
        (["install", "-e", "test"], "environment", "test"),
        (["install", "--force-reinstall"], "force_reinstall", True),
        (["envs", "--installed"], "installed", True),
        (["info", "-e", "test"], "environment", "test"),
        (["info"], "environment", None),
        (["add", "--pypi", "requests"], "pypi", True),
        (["add", "--feature", "dev", "numpy"], "feature", "dev"),
        (["remove", "--pypi", "requests"], "pypi", True),
        (["clean", "-e", "test"], "environment", "test"),
        (["activate", "-e", "docs"], "environment", "docs"),
        (["activate"], "environment", "default"),
    ],
    ids=[
        "init-format-conda",
        "init-format-pyproject",
        "init-name",
        "init-channels",
        "install-env",
        "install-force",
        "envs-installed",
        "info-named",
        "info-default",
        "add-pypi",
        "add-feature",
        "remove-pypi",
        "clean-env",
        "activate-named",
        "activate-default",
    ],
)
def test_workspace_parser_args(
    args: list[str], expected_attr: str, expected_value: object
) -> None:
    parser = generate_workspace_parser()
    parsed = parser.parse_args(args)
    assert getattr(parsed, expected_attr) == expected_value


@pytest.mark.parametrize(
    "subcmd, module_attr, func_name",
    [
        ("init", "conda_workspaces.cli.workspace.init", "execute_init"),
        ("install", "conda_workspaces.cli.workspace.install", "execute_install"),
        ("lock", "conda_workspaces.cli.workspace.lock", "execute_lock"),
        ("list", "conda_workspaces.cli.workspace.list", "execute_list"),
        ("info", "conda_workspaces.cli.workspace.info", "execute_info"),
        ("add", "conda_workspaces.cli.workspace.add", "execute_add"),
        ("remove", "conda_workspaces.cli.workspace.remove", "execute_remove"),
        ("clean", "conda_workspaces.cli.workspace.clean", "execute_clean"),
        ("activate", "conda_workspaces.cli.workspace.activate", "execute_activate"),
        ("shell", "conda_workspaces.cli.workspace.shell", "execute_shell"),
    ],
    ids=[
        "init",
        "install",
        "lock",
        "list",
        "info",
        "add",
        "remove",
        "clean",
        "activate",
        "shell",
    ],
)
def test_workspace_dispatches_to_subcommand(
    monkeypatch: pytest.MonkeyPatch,
    subcmd: str,
    module_attr: str,
    func_name: str,
) -> None:
    calls: list[str] = []

    def fake_handler(args):
        calls.append(subcmd)
        return 0

    mod = importlib.import_module(module_attr)
    monkeypatch.setattr(mod, func_name, fake_handler)

    args = argparse.Namespace(subcmd=subcmd)
    result = execute_workspace(args)
    assert result == 0
    assert calls == [subcmd]


@pytest.mark.parametrize(
    "subcmd, module_attr, func_name",
    [
        ("run", "conda_workspaces.cli.task.run", "execute_run"),
        ("list", "conda_workspaces.cli.task.list", "execute_list"),
        ("add", "conda_workspaces.cli.task.add", "execute_add"),
        ("remove", "conda_workspaces.cli.task.remove", "execute_remove"),
        ("export", "conda_workspaces.cli.task.export", "execute_export"),
    ],
    ids=["run", "list", "add", "remove", "export"],
)
def test_task_dispatches_to_subcommand(
    monkeypatch: pytest.MonkeyPatch,
    subcmd: str,
    module_attr: str,
    func_name: str,
) -> None:
    calls: list[str] = []

    def fake_handler(args):
        calls.append(subcmd)
        return 0

    mod = importlib.import_module(module_attr)
    monkeypatch.setattr(mod, func_name, fake_handler)

    args = argparse.Namespace(subcmd=subcmd)
    result = execute_task(args)
    assert result == 0
    assert calls == [subcmd]


def test_shell_accepts_environment_flag() -> None:
    parser = generate_workspace_parser()
    parsed = parser.parse_args(["shell", "-e", "test"])
    assert parsed.environment == "test"


@pytest.mark.parametrize(
    "argv",
    [
        ["init", "--json"],
        ["activate", "--json"],
        ["run", "--json", "--", "echo", "hi"],
        ["shell", "--json"],
    ],
    ids=["init", "activate", "run", "shell"],
)
def test_side_effect_subcommands_accept_json_silently(argv: list[str]) -> None:
    """Side-effect subcommands must tolerate ``--json`` without argparse errors.

    These subcommands register ``--json`` with ``help=SUPPRESS`` via
    :func:`_accept_json_silently` because they have no structured output
    to emit, but CI wrappers still pass ``--json`` globally; crashing
    with ``unrecognized arguments: --json`` is the wrong UX. See the
    ``--json contract`` section in ``AGENTS.md``.
    """
    parser = generate_workspace_parser()
    parsed = parser.parse_args(argv)
    assert parsed.subcmd == argv[0]


@pytest.mark.parametrize(
    "args, expected_attr, expected_value",
    [
        (["run", "test"], "task_name", "test"),
        (["run", "-e", "dev", "test"], "environment", "dev"),
        (["run", "--skip-deps", "test"], "skip_deps", True),
        (["run", "--templated", "test"], "templated", True),
        (["run", "--clean-env", "test"], "clean_env", True),
    ],
    ids=[
        "run-task-name",
        "run-environment",
        "run-skip-deps",
        "run-templated",
        "run-clean-env",
    ],
)
def test_task_parser_args(
    args: list[str], expected_attr: str, expected_value: object
) -> None:
    parser = generate_task_parser()
    parsed = parser.parse_args(args)
    assert getattr(parsed, expected_attr) == expected_value
