"""Tests for ``conda task export``."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest
from conda.exceptions import CondaSystemExit

from conda_workspaces.cli.task.export import execute_export
from conda_workspaces.manifests import detect_and_parse_tasks
from conda_workspaces.manifests.toml import CondaTomlParser

if TYPE_CHECKING:
    from pathlib import Path

export_mod = "conda_workspaces.cli.task.export"


def _export_args(file: Path, output: Path | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        file=file,
        output=output,
        quiet=False,
        verbosity=0,
        json=False,
        dry_run=False,
    )


def test_export_to_stdout(sample_yaml, capsys):
    result = execute_export(_export_args(sample_yaml))
    assert result == 0
    output = capsys.readouterr().out
    assert "[tasks]" in output
    assert "build" in output
    assert "configure" in output


def test_export_to_file(sample_yaml, tmp_path):
    out_path = tmp_path / "exported.toml"
    result = execute_export(_export_args(sample_yaml, out_path))
    assert result == 0
    assert out_path.exists()

    tasks = CondaTomlParser().parse_tasks(out_path)
    assert "build" in tasks
    assert "test" in tasks
    assert "lint" in tasks


def test_export_from_pixi_toml(tmp_path):
    """Export from pixi.toml produces valid conda.toml."""
    pixi = tmp_path / "pixi.toml"
    pixi.write_text(
        '[tasks]\nbuild = "make build"\n'
        'test = { cmd = "pytest", depends-on = ["build"] }\n'
        "\n[target.win-64.tasks]\n"
        'build = "nmake build"\n'
    )

    out_path = tmp_path / "conda.toml"
    result = execute_export(_export_args(pixi, out_path))
    assert result == 0

    tasks = CondaTomlParser().parse_tasks(out_path)
    assert "build" in tasks
    assert "test" in tasks
    assert tasks["test"].depends_on[0].task == "build"
    assert tasks["build"].platforms is not None
    assert "win-64" in tasks["build"].platforms


def test_export_new_file_skips_confirm(
    sample_yaml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing to a new file does not prompt for confirmation."""
    confirm_calls: list[str] = []
    monkeypatch.setattr(
        f"{export_mod}.confirm_yn", lambda msg: confirm_calls.append(msg)
    )

    out_path = tmp_path / "new.toml"
    result = execute_export(_export_args(sample_yaml, out_path))
    assert result == 0
    assert out_path.exists()
    assert confirm_calls == []


def test_export_existing_file_prompts(
    sample_yaml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Overwriting an existing file asks for confirmation."""
    confirm_calls: list[str] = []
    monkeypatch.setattr(
        f"{export_mod}.confirm_yn", lambda msg: confirm_calls.append(msg)
    )

    out_path = tmp_path / "existing.toml"
    out_path.write_text("old content", encoding="utf-8")

    result = execute_export(_export_args(sample_yaml, out_path))
    assert result == 0
    assert len(confirm_calls) == 1
    assert "Overwrite" in confirm_calls[0]
    assert "old content" not in out_path.read_text(encoding="utf-8")


def test_export_overwrite_abort(
    sample_yaml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aborting the overwrite prompt leaves the file unchanged."""

    def raise_abort(*args, **kwargs):
        raise CondaSystemExit()

    monkeypatch.setattr(f"{export_mod}.confirm_yn", raise_abort)

    out_path = tmp_path / "keep.toml"
    out_path.write_text("keep me", encoding="utf-8")

    result = execute_export(_export_args(sample_yaml, out_path))
    assert result == 0
    assert out_path.read_text(encoding="utf-8") == "keep me"


def test_export_rejects_symlinked_output(
    sample_yaml: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.toml"
    outside.write_text("keep me", encoding="utf-8")
    output = tmp_path / "exported.toml"
    output.symlink_to(outside)

    with pytest.raises(ValueError, match="symbolic link"):
        execute_export(_export_args(sample_yaml, output))

    assert outside.read_text(encoding="utf-8") == "keep me"


def test_export_rejects_output_changed_during_rendering(
    sample_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "exported.toml"
    output.write_text("# original\n", encoding="utf-8")
    concurrent = "# concurrent\n"

    def replace_output(*args, **kwargs):
        output.write_text(concurrent, encoding="utf-8")
        return detect_and_parse_tasks(*args, **kwargs)

    monkeypatch.setattr(f"{export_mod}.detect_and_parse_tasks", replace_output)
    monkeypatch.setattr(f"{export_mod}.confirm_yn", lambda _message: None)

    with pytest.raises(ValueError, match="changed before writing"):
        execute_export(_export_args(sample_yaml, output))

    assert output.read_text(encoding="utf-8") == concurrent
