"""Tests for conda_workspaces.__main__ (``cw`` and ``ct`` entry points)."""

from __future__ import annotations

import argparse
import json
from contextlib import redirect_stdout
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from conda.base.context import context as conda_context
from conda.base.context import reset_context
from conda.reporters import render

from conda_workspaces.__main__ import main, main_task
from conda_workspaces.cli import main as cli_main_mod

if TYPE_CHECKING:
    from pathlib import Path


class _FakeParser:
    prog: str = ""

    def __init__(self, parsed: argparse.Namespace | None = None):
        self.captured_prog: list[str] = []
        self.parsed = parsed or argparse.Namespace()

    def parse_args(self, args):
        self.captured_prog.append(self.prog)
        return self.parsed


def test_main_no_args_shows_help() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_main_task_no_args_shows_help() -> None:
    with pytest.raises(SystemExit):
        main_task([])


def test_main_sets_prog_to_cw(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_parser = _FakeParser()

    monkeypatch.setattr(cli_main_mod, "generate_workspace_parser", lambda: fake_parser)
    monkeypatch.setattr(cli_main_mod, "execute_workspace", lambda parsed: 0)

    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 0
    assert fake_parser.captured_prog == ["cw"]


@pytest.mark.usefixtures("reset_conda_context")
def test_main_initializes_json_reporter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reset_context(argparse_args=argparse.Namespace(json=False))
    with redirect_stdout(StringIO()):
        render({"cached": "classic"})

    parsed = argparse.Namespace(json=True)
    fake_parser = _FakeParser(parsed)

    def execute_workspace(args: argparse.Namespace) -> int:
        assert args is parsed
        assert conda_context.json is True
        assert conda_context.console == "json"
        render({"success": True})
        return 0

    monkeypatch.setattr(cli_main_mod, "generate_workspace_parser", lambda: fake_parser)
    monkeypatch.setattr(cli_main_mod, "execute_workspace", execute_workspace)

    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out) == {"success": True}


@pytest.mark.usefixtures("reset_conda_context")
def test_main_renders_json_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["install", "--json", "--frozen"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert json.loads(captured.out)["exception_name"] == "WorkspaceNotFoundError"
    assert captured.err == ""


def test_main_task_sets_prog_to_ct(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_parser = _FakeParser()

    monkeypatch.setattr(cli_main_mod, "generate_task_parser", lambda: fake_parser)
    monkeypatch.setattr(cli_main_mod, "execute_task", lambda parsed: 0)

    with pytest.raises(SystemExit) as exc_info:
        main_task([])

    assert exc_info.value.code == 0
    assert fake_parser.captured_prog == ["ct"]


@pytest.mark.parametrize(
    "exit_code",
    [0, 1, 42],
    ids=["success", "failure", "custom"],
)
def test_main_exits_with_execute_return_code(
    monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    fake_parser = _FakeParser()

    monkeypatch.setattr(cli_main_mod, "generate_workspace_parser", lambda: fake_parser)
    monkeypatch.setattr(cli_main_mod, "execute_workspace", lambda parsed: exit_code)

    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == exit_code
