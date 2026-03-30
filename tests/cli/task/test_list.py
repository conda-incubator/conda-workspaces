"""Tests for ``conda task list``."""

from __future__ import annotations

import argparse
import io
import json
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

from conda_workspaces.cli.task.list import execute_list


def _make_console() -> tuple[Console, io.StringIO]:
    """Create a console that writes to a string buffer."""
    buf = io.StringIO()
    return Console(file=buf, force_terminal=True), buf


def _list_args(file: Path, *, use_json: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        file=file, json=use_json, quiet=False, verbose=0, dry_run=False
    )


def test_list_tasks(sample_yaml):
    console, buf = _make_console()
    result = execute_list(_list_args(sample_yaml), console=console)
    assert result == 0
    output = buf.getvalue()
    assert "build" in output
    assert "configure" in output
    assert "test" in output
    assert "_setup" not in output


def test_list_no_tasks(tmp_path):
    path = tmp_path / "conda.toml"
    path.write_text("[tasks]\n")

    console, buf = _make_console()
    result = execute_list(_list_args(path), console=console)
    assert result == 0
    assert "No tasks" in buf.getvalue()


def test_list_json(sample_yaml):
    console, buf = _make_console()
    result = execute_list(_list_args(sample_yaml, use_json=True), console=console)
    assert result == 0
    data = json.loads(buf.getvalue())
    assert "tasks" in data
    assert "build" in data["tasks"]
    assert "file" in data
    assert "_setup" not in data["tasks"]


def test_list_shows_task_name(tmp_path):
    path = tmp_path / "conda.toml"
    path.write_text('[tasks]\nbuild = "cmake --build ."\n')

    console, buf = _make_console()
    execute_list(_list_args(path), console=console)
    output = buf.getvalue()
    assert "build" in output
    assert "Description" not in output


def test_list_shows_description_when_present(tmp_path):
    path = tmp_path / "conda.toml"
    path.write_text(
        '[tasks]\nbuild = "make"\n\n[tasks.test]\n'
        'cmd = "pytest"\ndescription = "Run tests"\n'
    )

    console, buf = _make_console()
    execute_list(_list_args(path), console=console)
    output = buf.getvalue()
    assert "Run tests" in output


def test_list_json_alias(tmp_path):
    path = tmp_path / "conda.toml"
    path.write_text(
        '[tasks]\nlint = "ruff check ."\ntest = "pytest"\n\n'
        '[tasks.check]\ndepends-on = ["lint", "test"]\ndescription = "Run all"\n'
    )

    console, buf = _make_console()
    execute_list(_list_args(path, use_json=True), console=console)

    data = json.loads(buf.getvalue())
    assert data["tasks"]["check"].get("alias") is True
    assert "depends_on" in data["tasks"]["check"]
