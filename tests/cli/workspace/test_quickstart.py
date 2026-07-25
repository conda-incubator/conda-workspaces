"""Tests for conda_workspaces.cli.workspace.quickstart."""

from __future__ import annotations

import json
import sys
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from conda.exceptions import ArgumentError, InvalidMatchSpec
from rich.console import Console

from conda_workspaces.cli.workspace import quickstart as quickstart_module
from conda_workspaces.cli.workspace.quickstart import execute_quickstart
from conda_workspaces.exceptions import (
    LockfileNotFoundError,
    LockfileStaleError,
    ManifestExistsError,
    QuickstartCopyError,
)

from ..conftest import make_args

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable
    from pathlib import Path

    from tests.conftest import SnapshotTree

pytestmark = pytest.mark.usefixtures("configure_conda_channels")

_DEFAULTS = {
    "specs": [],
    "manifest_format": "conda",
    "name": None,
    "channel": None,
    "override_channels": False,
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
    orchestrated: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configure_conda_channels: Callable[..., None],
) -> None:
    """``--copy <dir>`` copies the manifest and skips the init step."""
    configure_conda_channels([])
    source = tmp_path / "source"
    source.mkdir()
    content = "[workspace]\nname='src'\nchannels=['source-channel']\n"
    (source / "pixi.toml").write_text(content, encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.chdir(dest)

    result = orchestrated["run"](
        copy_from=source,
        channel=["ignored"],
        override_channels=True,
    )

    assert result == 0
    runners = orchestrated["runners"]
    assert runners["init"].calls == []
    assert (dest / "pixi.toml").read_text(encoding="utf-8") == content
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


def test_quickstart_copy_rejects_override_without_channel(
    orchestrated: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "conda.toml").write_text(
        "[workspace]\nname='source'\nchannels=['source-channel']\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ArgumentError,
        match="At least one -c / --channel flag must be supplied",
    ):
        orchestrated["run"](
            copy_from=source,
            override_channels=True,
        )

    assert not (tmp_path / "conda.toml").exists()


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
        sys.stdout.write("plain status\n")

    orchestrated["runners"]["init"]._effect = _noisy
    orchestrated["runners"]["add"]._effect = _noisy

    orchestrated["run"](specs=["python=3.14"], json=True)

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line]
    assert len(lines) == 1, f"expected a single JSON line on stdout, got: {lines!r}"
    payload = json.loads(lines[0])
    assert payload["specs_added"] == ["python=3.14"]


@pytest.mark.parametrize(
    ("specs", "handler"),
    [([], "install"), (["python"], "add")],
    ids=["install", "add"],
)
def test_quickstart_dry_run_validates_staged_manifest(
    orchestrated: dict,
    tmp_path: Path,
    configure_conda_channels: Callable[..., None],
    specs: list[str],
    handler: str,
) -> None:
    """``--dry-run`` delegates validation against a temporary manifest."""
    configure_conda_channels(
        ["defaults", "Internal"],
        channel=["Staging", "Second"],
    )
    staged_contents: list[str] = []

    def inspect_manifest(ns, *, console):  # type: ignore[no-untyped-def]
        del console
        staged_contents.append(ns.manifest_file.read_text(encoding="utf-8"))

    orchestrated["runners"][handler]._effect = inspect_manifest
    result = orchestrated["run"](
        channel=["Staging", "Second"],
        dry_run=True,
        specs=specs,
    )

    assert result == 0
    runners = orchestrated["runners"]
    assert runners["init"].calls == []
    assert len(runners[handler].calls) == 1
    assert runners[handler].calls[0].dry_run is True
    assert runners["shell"].calls == []
    assert staged_contents
    assert (
        'channels = ["Staging", "Second", "defaults", "Internal"]' in staged_contents[0]
    )
    assert not (tmp_path / "conda.toml").exists()


def test_quickstart_dry_run_with_copy_reports_but_does_not_write(
    orchestrated: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configure_conda_channels: Callable[..., None],
) -> None:
    configure_conda_channels([])
    source = tmp_path / "src"
    source.mkdir()
    (source / "conda.toml").write_text(
        "[workspace]\nname='x'\nchannels=['source-channel']\n",
        encoding="utf-8",
    )

    dest = tmp_path / "dst"
    dest.mkdir()
    monkeypatch.chdir(dest)
    staged_contents: list[str] = []

    def inspect_manifest(ns, *, console):  # type: ignore[no-untyped-def]
        del console
        staged_contents.append(ns.manifest_file.read_text(encoding="utf-8"))

    orchestrated["runners"]["install"]._effect = inspect_manifest

    result = orchestrated["run"](
        dry_run=True,
        copy_from=source,
        channel=["ignored"],
        override_channels=True,
    )

    assert result == 0
    assert not (dest / "conda.toml").exists()
    assert len(orchestrated["runners"]["install"].calls) == 1
    assert staged_contents and "channels=['source-channel']" in staged_contents[0]
    rendered = orchestrated["console"].file.getvalue()
    assert "Would copy" in rendered


@pytest.mark.parametrize(
    ("overrides", "lock_content", "expected_error"),
    [
        ({"specs": ["???"]}, None, InvalidMatchSpec),
        ({"frozen": True}, None, LockfileNotFoundError),
        (
            {"locked": True},
            "version: 1\nenvironments: {}\npackages: []\n",
            LockfileStaleError,
        ),
    ],
    ids=["malformed-spec", "frozen-missing-lock", "locked-stale-lock"],
)
def test_quickstart_dry_run_validates_prospective_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
    overrides: dict,
    lock_content: str | None,
    expected_error: type[Exception],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    if lock_content is not None:
        (tmp_path / "conda.lock").write_text(lock_content, encoding="utf-8")
    before = snapshot_tree(tmp_path)

    with pytest.raises(expected_error):
        execute_quickstart(
            make_args(
                _DEFAULTS,
                dry_run=True,
                no_shell=True,
                **overrides,
            ),
            console=Console(file=StringIO(), force_terminal=False),
        )

    assert snapshot_tree(tmp_path) == before


@pytest.mark.parametrize("specs", [[], ["python"]], ids=["install", "add"])
def test_quickstart_dry_run_validates_requested_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
    specs: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    blocked_prefix = tmp_path / ".conda" / "envs" / "default"
    blocked_prefix.parent.mkdir(parents=True)
    blocked_prefix.write_bytes(b"blocked")
    before = snapshot_tree(tmp_path)

    with pytest.raises(FileExistsError, match="not a directory"):
        execute_quickstart(
            make_args(_DEFAULTS, dry_run=True, no_shell=True, specs=specs),
            console=Console(file=StringIO(), force_terminal=False),
        )

    assert snapshot_tree(tmp_path) == before


@pytest.mark.parametrize(
    ("copy_manifest", "expected_error"),
    [(False, ManifestExistsError), (True, QuickstartCopyError)],
    ids=["init", "copy"],
)
def test_quickstart_dry_run_rejects_dangling_manifest_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
    copy_manifest: bool,
    expected_error: type[Exception],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = workspace / "conda.toml"
    outside = tmp_path / "outside.toml"
    manifest.symlink_to(outside)
    copy_from = None
    if copy_manifest:
        source = tmp_path / "source"
        source.mkdir()
        (source / "conda.toml").write_text(
            "[workspace]\nname = 'source'\n",
            encoding="utf-8",
        )
        copy_from = source
    monkeypatch.chdir(workspace)
    before = snapshot_tree(tmp_path)

    with pytest.raises(expected_error, match="already exists"):
        execute_quickstart(
            make_args(
                _DEFAULTS,
                copy_from=copy_from,
                dry_run=True,
                no_shell=True,
            ),
            console=Console(file=StringIO(), force_terminal=False),
        )

    assert snapshot_tree(tmp_path) == before
    assert manifest.is_symlink()
    assert not outside.exists()


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
                "platforms": ["linux-64", "osx-arm64"],
                "manifest_format": "pixi",
            },
            {
                "name": "demo",
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
