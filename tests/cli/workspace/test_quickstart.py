"""Tests for conda_workspaces.cli.workspace.quickstart."""

from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from conda_workspaces.cli.workspace import quickstart as quickstart_module
from conda_workspaces.cli.workspace.quickstart import execute_quickstart
from conda_workspaces.exceptions import QuickstartCopyError

from ..conftest import make_args

if TYPE_CHECKING:
    import argparse
    from pathlib import Path


_DEFAULTS = {
    "specs": [],
    "manifest_format": "conda",
    "name": None,
    "channels": None,
    "platforms": None,
    "environment": "default",
    "force_reinstall": False,
    "locked": False,
    "frozen": False,
    "copy_from": None,
    "no_shell": False,
    "json": False,
    "yes": False,
    "dry_run": False,
    "quiet": False,
    "verbosity": 0,
    "debug": False,
    "trace": False,
}


class _RecordingRunner:
    """Callable that records each ``Namespace`` (and optional ``console``) it saw.

    Mirrors the ``execute_X(args, *, console=None)`` signature the real
    workspace sub-handlers expose, so tests can assert both the args
    ``quickstart`` forwards *and* the ``Console`` it routed output
    through (used to guard against ``--json`` leaking sub-handler
    Rich output onto real stdout).
    """

    def __init__(self, *, effect=None) -> None:
        self.calls: list[argparse.Namespace] = []
        self.consoles: list[Console | None] = []
        self._effect = effect

    def __call__(
        self, ns: argparse.Namespace, *, console: Console | None = None
    ) -> int:
        self.calls.append(ns)
        self.consoles.append(console)
        if self._effect is not None:
            self._effect(ns, console=console)
        return 0


@pytest.fixture
def orchestrated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Patch the four sub-handler imports inside ``quickstart`` with recorders.

    Returns a ``dict`` with:

    * ``runners``: the four recording closures (``init`` / ``add`` /
      ``install`` / ``shell``).
    * ``run``: a helper that builds a default ``Namespace`` and calls
      ``execute_quickstart`` with a ``StringIO``-backed console.
    * ``console``: the Rich console instance for output assertions.
    * ``root``: ``tmp_path`` (cwd at setup).
    """
    monkeypatch.chdir(tmp_path)

    def _touch_manifest(
        ns: argparse.Namespace, *, console: Console | None = None
    ) -> None:
        """``execute_init`` would write a manifest; simulate it."""
        del console
        fmt = getattr(ns, "manifest_format", "conda") or "conda"
        filename = {"conda": "conda.toml", "pixi": "pixi.toml"}.get(
            fmt, "pyproject.toml"
        )
        (tmp_path / filename).write_text("# stub", encoding="utf-8")

    runners = {
        "init": _RecordingRunner(effect=_touch_manifest),
        "add": _RecordingRunner(),
        "install": _RecordingRunner(),
        "shell": _RecordingRunner(),
    }
    monkeypatch.setattr(quickstart_module, "execute_init", runners["init"])
    monkeypatch.setattr(quickstart_module, "execute_add", runners["add"])
    monkeypatch.setattr(quickstart_module, "execute_install", runners["install"])
    monkeypatch.setattr(quickstart_module, "execute_shell", runners["shell"])

    console = Console(file=StringIO(), width=200, force_terminal=False)

    def run(**overrides) -> int:
        args = make_args(_DEFAULTS, **overrides)
        return execute_quickstart(args, console=console)

    return {"run": run, "runners": runners, "console": console, "root": tmp_path}


@pytest.mark.parametrize(
    ("specs", "expect_add", "expect_install"),
    [
        ([], False, True),
        (["python=3.14"], True, False),
        (["python=3.14", "numpy>=2.4"], True, False),
    ],
    ids=[
        "no-specs-runs-install",
        "single-spec-skips-install",
        "multi-spec-skips-install",
    ],
)
def test_quickstart_from_scratch(
    orchestrated: dict,
    specs: list[str],
    expect_add: bool,
    expect_install: bool,
) -> None:
    """init always runs; add replaces install when specs are provided."""
    result = orchestrated["run"](specs=specs)

    assert result == 0
    runners = orchestrated["runners"]
    assert len(runners["init"].calls) == 1
    assert len(runners["add"].calls) == (1 if expect_add else 0)
    assert len(runners["install"].calls) == (1 if expect_install else 0)
    assert len(runners["shell"].calls) == 1
    if expect_add:
        assert runners["add"].calls[0].specs == specs


def test_quickstart_copy_from_dir_skips_init(
    orchestrated: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--copy <dir>`` copies the manifest and skips the init step."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "pixi.toml").write_text("[workspace]\nname='src'\n", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.chdir(dest)

    result = orchestrated["run"](copy_from=source)

    assert result == 0
    runners = orchestrated["runners"]
    assert runners["init"].calls == []
    assert (dest / "pixi.toml").exists()
    assert len(runners["install"].calls) == 1
    assert len(runners["shell"].calls) == 1


def test_quickstart_copy_from_file(
    orchestrated: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--copy <manifest>`` accepts a direct file path."""
    manifest = tmp_path / "conda.toml"
    manifest.write_text("[workspace]\nname='upstream'\n", encoding="utf-8")

    dest = tmp_path / "copy"
    dest.mkdir()
    monkeypatch.chdir(dest)

    result = orchestrated["run"](copy_from=manifest)

    assert result == 0
    assert (dest / "conda.toml").exists()
    assert orchestrated["runners"]["init"].calls == []


def test_quickstart_copy_missing_path_raises(
    orchestrated: dict, tmp_path: Path
) -> None:
    missing = tmp_path / "no-such-directory"
    with pytest.raises(QuickstartCopyError, match="does not exist"):
        orchestrated["run"](copy_from=missing)


def test_quickstart_copy_with_format_warns(
    orchestrated: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--copy`` + ``--format`` emits a warning and still succeeds."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "pixi.toml").write_text("[workspace]\nname='src'\n", encoding="utf-8")

    dest = tmp_path / "dst"
    dest.mkdir()
    monkeypatch.chdir(dest)

    result = orchestrated["run"](copy_from=source, manifest_format="pixi")

    assert result == 0
    rendered = orchestrated["console"].file.getvalue()
    assert "--format is ignored" in rendered


def test_quickstart_no_shell_skips_shell(orchestrated: dict) -> None:
    result = orchestrated["run"](no_shell=True)
    assert result == 0
    assert orchestrated["runners"]["shell"].calls == []


def test_quickstart_json_suppresses_shell_and_emits_payload(
    orchestrated: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--json`` implies no shell and prints a structured result."""
    result = orchestrated["run"](specs=["python=3.14"], json=True, environment="dev")

    assert result == 0
    assert orchestrated["runners"]["shell"].calls == []

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["environment"] == "dev"
    assert payload["specs_added"] == ["python=3.14"]
    assert payload["shell_spawned"] is False
    assert payload["manifest"] == "conda.toml"


def test_quickstart_json_does_not_forward_flag_to_subhandlers(
    orchestrated: dict,
) -> None:
    """``--json`` is owned by quickstart; sub-handlers never see it.

    Quickstart silences their Rich output via a throwaway Console
    instead of asking them to honour ``--json`` themselves, so
    neither ``init`` nor ``add`` / ``install`` should observe the
    attribute on the ``Namespace`` they receive.
    """
    orchestrated["run"](specs=["python=3.14"], json=True)

    for name in ("init", "add"):
        ns = orchestrated["runners"][name].calls[0]
        assert not hasattr(ns, "json"), (
            f"execute_{name} unexpectedly received --json; quickstart"
            " should own the JSON surface and keep sub-handlers in"
            " human-output mode"
        )


def test_quickstart_json_routes_subhandlers_through_silent_console(
    orchestrated: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rich output from sub-handlers must not leak onto stdout under --json.

    Simulates the real sub-handlers by having the fake ``init`` /
    ``add`` runners print status lines through the ``Console`` they
    receive; under ``--json`` that console must be a StringIO-backed
    sink, so stdout only carries the final JSON payload.
    """

    def _noisy(ns, *, console):  # type: ignore[no-untyped-def]
        del ns
        if console is not None:
            console.print("[bold cyan]Created[/bold cyan] workspace stub")

    orchestrated["runners"]["init"]._effect = _noisy
    orchestrated["runners"]["add"]._effect = _noisy

    orchestrated["run"](specs=["python=3.14"], json=True)

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line]
    assert len(lines) == 1, f"expected a single JSON line on stdout, got: {lines!r}"
    payload = json.loads(lines[0])
    assert payload["specs_added"] == ["python=3.14"]


def test_quickstart_dry_run_skips_side_effects(
    orchestrated: dict, tmp_path: Path
) -> None:
    """``--dry-run`` runs no sub-handlers and writes no files."""
    result = orchestrated["run"](dry_run=True, specs=["python"])

    assert result == 0
    runners = orchestrated["runners"]
    assert runners["init"].calls == []
    assert runners["add"].calls == []
    assert runners["install"].calls == []
    assert runners["shell"].calls == []
    assert not (tmp_path / "conda.toml").exists()


def test_quickstart_dry_run_with_copy_reports_but_does_not_write(
    orchestrated: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "conda.toml").write_text("[workspace]\nname='x'\n", encoding="utf-8")

    dest = tmp_path / "dst"
    dest.mkdir()
    monkeypatch.chdir(dest)

    result = orchestrated["run"](dry_run=True, copy_from=source)

    assert result == 0
    assert not (dest / "conda.toml").exists()
    rendered = orchestrated["console"].file.getvalue()
    assert "Would copy" in rendered


@pytest.mark.parametrize(
    ("subhandler", "inputs", "expected"),
    [
        (
            "install",
            {"force_reinstall": True, "locked": True},
            {"force_reinstall": True, "locked": True},
        ),
        (
            "init",
            {
                "name": "demo",
                "channels": ["conda-forge", "bioconda"],
                "platforms": ["linux-64", "osx-arm64"],
                "manifest_format": "pixi",
            },
            {
                "name": "demo",
                "channels": ["conda-forge", "bioconda"],
                "platforms": ["linux-64", "osx-arm64"],
                "manifest_format": "pixi",
            },
        ),
    ],
    ids=["install-flags", "init-flags"],
)
def test_quickstart_forwards_sub_handler_flags(
    orchestrated: dict,
    subhandler: str,
    inputs: dict,
    expected: dict,
) -> None:
    """Flags defined on init / install are threaded to the matching sub-handler."""
    result = orchestrated["run"](**inputs)

    assert result == 0
    ns = orchestrated["runners"][subhandler].calls[0]
    for key, value in expected.items():
        assert getattr(ns, key) == value
