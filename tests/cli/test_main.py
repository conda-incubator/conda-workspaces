"""Tests for conda_workspaces.cli.main — parser configuration and dispatch."""

from __future__ import annotations

import argparse
import importlib

import pytest

from conda_workspaces.cli.main import execute, generate_parser


def test_generate_parser_returns_parser() -> None:
    parser = generate_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    assert "workspace" in parser.prog


@pytest.mark.parametrize(
    "subcmd",
    [
        "init", "install", "lock", "list", "info",
        "add", "remove", "clean", "run", "activate", "shell",
    ],
)
def test_subcommands_registered(subcmd: str) -> None:
    parser = generate_parser()
    if subcmd == "add":
        args = parser.parse_args([subcmd, "numpy"])
    elif subcmd == "remove":
        args = parser.parse_args([subcmd, "numpy"])
    elif subcmd == "run":
        args = parser.parse_args([subcmd, "echo", "hi"])
    else:
        args = parser.parse_args([subcmd])
    assert args.subcmd == subcmd


def test_no_subcmd_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    parser = generate_parser()
    args = parser.parse_args([])
    result = execute(args)
    assert result == 0
    captured = capsys.readouterr()
    assert "workspace" in captured.out.lower() or "usage" in captured.out.lower()


def test_unknown_subcmd_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """An unrecognised subcmd falls through to the else branch."""
    parser = generate_parser()
    args = parser.parse_args([])
    args.subcmd = "nonexistent"
    result = execute(args)
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
        (["list", "--installed"], "installed", True),
        (["info", "-e", "test"], "environment", "test"),
        (["info"], "environment", None),
        (["add", "--pypi", "requests"], "pypi", True),
        (["add", "--feature", "dev", "numpy"], "feature", "dev"),
        (["remove", "--pypi", "requests"], "pypi", True),
        (["clean", "-e", "test"], "environment", "test"),
        (["run", "-e", "test", "pytest"], "environment", "test"),
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
        "list-installed",
        "info-named",
        "info-default",
        "add-pypi",
        "add-feature",
        "remove-pypi",
        "clean-env",
        "run-env",
        "activate-named",
        "activate-default",
    ],
)
def test_parser_args(
    args: list[str], expected_attr: str, expected_value: object
) -> None:
    parser = generate_parser()
    parsed = parser.parse_args(args)
    assert getattr(parsed, expected_attr) == expected_value


@pytest.mark.parametrize(
    "subcmd, module_attr, func_name",
    [
        ("init", "conda_workspaces.cli.init", "execute_init"),
        ("install", "conda_workspaces.cli.install", "execute_install"),
        ("lock", "conda_workspaces.cli.lock", "execute_lock"),
        ("list", "conda_workspaces.cli.list", "execute_list"),
        ("info", "conda_workspaces.cli.info", "execute_info"),
        ("add", "conda_workspaces.cli.add", "execute_add"),
        ("remove", "conda_workspaces.cli.remove", "execute_remove"),
        ("clean", "conda_workspaces.cli.clean", "execute_clean"),
        ("run", "conda_workspaces.cli.run", "execute_run"),
        ("activate", "conda_workspaces.cli.activate", "execute_activate"),
        ("shell", "conda_workspaces.cli.shell", "execute_shell"),
    ],
    ids=[
        "init", "install", "lock", "list", "info",
        "add", "remove", "clean", "run", "activate", "shell",
    ],
)
def test_execute_dispatches_to_subcommand(
    monkeypatch: pytest.MonkeyPatch,
    subcmd: str,
    module_attr: str,
    func_name: str,
) -> None:
    """Each subcommand dispatches to the correct execute_* function."""
    calls: list[str] = []

    def fake_handler(args):
        calls.append(subcmd)
        return 0

    mod = importlib.import_module(module_attr)
    monkeypatch.setattr(mod, func_name, fake_handler)

    args = argparse.Namespace(subcmd=subcmd)
    result = execute(args)
    assert result == 0
    assert calls == [subcmd]


def test_shell_accepts_environment_flag() -> None:
    parser = generate_parser()
    parsed = parser.parse_args(["shell", "-e", "test"])
    assert parsed.environment == "test"
