"""Tests for conda_workspaces.cli.workspace.quickstart."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conda.base.context import reset_context
from conda.common.compat import on_win
from conda.exceptions import ArgumentError, InvalidMatchSpec
from conda.utils import quote_for_shell
from rich.console import Console

from conda_workspaces.archive import WorkspaceArchive, create_archive
from conda_workspaces.cli.main import execute_workspace, generate_workspace_parser
from conda_workspaces.cli.workspace import quickstart as quickstart_module
from conda_workspaces.cli.workspace.quickstart import execute_quickstart
from conda_workspaces.exceptions import (
    CondaWorkspacesError,
    EnvironmentNameInvalidError,
    LockfileNotFoundError,
    LockfileStaleError,
    ManifestExistsError,
    QuickstartCopyError,
)
from conda_workspaces.models import ArchiveConfig
from conda_workspaces.receipts import ArchiveReceipt

from ..conftest import make_args

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Iterator

    from pytest_httpserver import HTTPServer

    from tests.conftest import SnapshotTree

pytestmark = pytest.mark.usefixtures("configure_conda_channels")

_DEFAULTS = {
    "specs": [],
    "manifest_format": None,
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
        (["https://example.org/pkg-1.0-0.conda"], True, False),
        (["https://example.org/pkg-1.0-0.tar.bz2"], True, False),
        (["https://conda.anaconda.org/conda-forge::numpy"], True, False),
        (["https://conda.anaconda.org/conda-forge::numpy.tar.gz"], True, False),
        (["numpy[url=https://example.org/numpy-2.0-py_0.conda]"], True, False),
        (["numpy[url=https://example.org/workspace.tar.gz]"], True, False),
        (["conda-forge::numpy[url=https://example.org/numpy.conda]"], True, False),
        (["https://example.org/pkg-1.0-0.CONDA"], True, False),
        (
            ["https://example.org/pkg-1.0-0.CONDA#d41d8cd98f00b204e9800998ecf8427e"],
            True,
            False,
        ),
    ],
    ids=[
        "no-specs-runs-install",
        "single-spec-skips-install",
        "multi-spec-skips-install",
        "conda-package-url",
        "bz2-package-url",
        "url-channel-matchspec",
        "url-channel-archive-shaped-name",
        "url-selector-matchspec",
        "url-selector-archive-shaped-name",
        "named-channel-url-selector",
        "uppercase-package-extension",
        "uppercase-package-extension-hash",
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
        assert runners["add"].calls[0].environment == "default"


@pytest.mark.parametrize(
    "source",
    [
        "https://reader:password@example.org:invalid/channel::numpy",
        "https://reader:password@[invalid/channel::numpy",
        "https://reader:password@example.org/%FF.conda",
        "https://reader:password@example.org/%FF::numpy",
    ],
    ids=[
        "invalid-port",
        "invalid-ipv6",
        "invalid-package-encoding",
        "invalid-channel-encoding",
    ],
)
def test_quickstart_rejects_malformed_url_specs_before_writes(
    orchestrated: dict,
    source: str,
) -> None:
    before = set(orchestrated["root"].iterdir())
    with pytest.raises(ArgumentError, match="Invalid source URL") as error:
        orchestrated["run"](specs=[source])

    assert "password" not in str(error.value)
    assert set(orchestrated["root"].iterdir()) == before
    assert all(not runner.calls for runner in orchestrated["runners"].values())


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


def test_quickstart_specs_install_selected_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_sync(config, ctx, env_names, **kwargs):  # type: ignore[no-untyped-def]
        del config
        kwargs["publish_lockfile"]("version: 1\nenvironments: {}\npackages: []\n")
        for name in env_names:
            ctx.env_prefix(name).mkdir(parents=True)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.add.sync_environments",
        fake_sync,
    )

    result = execute_quickstart(
        make_args(
            _DEFAULTS,
            specs=["python=3.14"],
            environment="dev",
            no_shell=True,
        ),
        console=Console(file=StringIO(), force_terminal=False),
    )

    assert result == 0
    manifest = tmp_path / "conda.toml"
    config = quickstart_module.ManifestParser.for_format_alias("conda").parse(manifest)
    assert str(config.environments["dev"].conda_dependencies["python"]) == "python=3.14"
    assert {path.name for path in (tmp_path / ".conda" / "envs").iterdir()} == {"dev"}


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


def test_quickstart_dry_run_redacts_configured_channel_credentials(
    orchestrated: dict,
    configure_conda_channels: Callable[..., None],
) -> None:
    configured = (
        "https://user:password@packages.example.test/t/secret/team/channel"
        "?token=secret#metadata"
    )
    configure_conda_channels([configured])
    staged_contents: list[str] = []

    def inspect_manifest(ns, *, console):  # type: ignore[no-untyped-def]
        del console
        staged_contents.append(ns.manifest_file.read_text(encoding="utf-8"))

    orchestrated["runners"]["install"]._effect = inspect_manifest

    assert orchestrated["run"](dry_run=True) == 0
    assert len(staged_contents) == 1
    assert "user:password" not in staged_contents[0]
    assert "/t/secret/" not in staged_contents[0]
    assert "token=secret" not in staged_contents[0]
    assert "https://packages.example.test/team/channel" in staged_contents[0]


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


@pytest.mark.parametrize(
    ("overrides", "expected_error", "message"),
    [
        ({"locked": True}, ArgumentError, "cannot be combined"),
        ({"frozen": True}, ArgumentError, "cannot be combined"),
        (
            {"environment": "../outside"},
            EnvironmentNameInvalidError,
            "not valid",
        ),
    ],
    ids=["locked", "frozen", "invalid-environment"],
)
def test_quickstart_rejects_invalid_specs_options_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
    overrides: dict,
    expected_error: type[Exception],
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    before = snapshot_tree(tmp_path)

    with pytest.raises(expected_error, match=message):
        execute_quickstart(
            make_args(
                _DEFAULTS,
                specs=["python=3.14"],
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

    with pytest.raises(
        CondaWorkspacesError,
        match="Environment prefix is not a regular directory",
    ):
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


@pytest.fixture
def remote_quickstart(
    downloadable_archive: WorkspaceArchive,
    httpserver: HTTPServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[WorkspaceArchive, HTTPServer, Path]]:
    """Serve a real locked workspace while keeping the caller separate."""
    reset_context(())
    route = "/" + downloadable_archive.path.name
    httpserver.expect_request(route).respond_with_data(
        downloadable_archive.path.read_bytes()
    )
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    yield downloadable_archive, httpserver, caller
    reset_context(())


@pytest.mark.parametrize("receipt", ["none", "sibling", "local", "remote"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["install", "preview"])
def test_quickstart_archive_installs_published_lock(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    capsys: pytest.CaptureFixture[str],
    receipt: str,
    dry_run: bool,
) -> None:
    archive, server, caller = remote_quickstart
    route = "/" + archive.path.name
    source = server.url_for(route)
    options = ["quickstart", source, "--json"]
    if receipt in {"sibling", "remote"}:
        server.expect_request(route + ".receipt.json").respond_with_data(
            ArchiveReceipt.default_path(archive.path).read_bytes()
        )
    if receipt != "none":
        options.append("--receipt")
        if receipt == "remote":
            options.append(server.url_for(route + ".receipt.json"))
        elif receipt == "local":
            options.append(str(ArchiveReceipt.default_path(archive.path)))
    if dry_run:
        options.append("--dry-run")
    args = generate_workspace_parser().parse_args(options)
    reset_context((), argparse_args=args)

    assert execute_workspace(args) == 0

    payload = json.loads(capsys.readouterr().out)
    target = caller / "example"
    assert payload["source"] == source
    assert payload["workspace"] == str(target)
    assert payload["artifact"] == "example.tar.gz"
    assert payload["environment"] == "default"
    assert payload["verified"] is (receipt != "none")
    assert payload["installed"] is not dry_run
    assert payload["shell_spawned"] is False
    assert payload["primed_packages"] == 0
    assert payload["return_code"] == 0
    assert target.exists() is not dry_run
    assert not (caller / "conda.toml").exists()
    if not dry_run:
        original = archive.path.parent / "workspace"
        assert (target / "conda.toml").read_bytes() == (
            original / "conda.toml"
        ).read_bytes()
        assert (target / "conda.lock").read_bytes() == (
            original / "conda.lock"
        ).read_bytes()
        assert (target / "app.py").read_text() == 'print("hello")\n'
        assert (
            target / ".conda" / "envs" / "default" / "conda-meta" / "history"
        ).is_file()
    expected_requests = (
        [route, route + ".receipt.json"]
        if receipt in {"sibling", "remote"}
        else [route]
    )
    assert [request.path for request, _ in server.log] == expected_requests


@pytest.mark.parametrize("no_shell", [False, True], ids=["shell", "no-shell"])
def test_quickstart_archive_uses_restored_workspace(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    monkeypatch: pytest.MonkeyPatch,
    no_shell: bool,
) -> None:
    archive, server, caller = remote_quickstart
    original_manifest = b"[workspace]\nname = 'caller'\nchannels = []\n"
    (caller / "conda.toml").write_bytes(original_manifest)
    target = caller / "new workspace"
    spawned = []

    def spawn(*, prefix: Path, shell_cls=None, command=None) -> int:
        spawned.append((prefix, Path.cwd(), command))
        return 0

    monkeypatch.setattr("conda_spawn.main.spawn", spawn)
    options = [
        "quickstart",
        server.url_for("/" + archive.path.name),
        "--target",
        str(target),
    ]
    if no_shell:
        options.append("--no-shell")
    args = generate_workspace_parser().parse_args(options)
    reset_context((), argparse_args=args)

    assert execute_workspace(args) == 0

    prefix = target / ".conda" / "envs" / "default"
    assert (prefix / "conda-meta" / "history").is_file()
    assert spawned == ([] if no_shell else [(prefix, target, None)])
    assert Path.cwd() == caller
    assert (caller / "conda.toml").read_bytes() == original_manifest
    assert not (caller / ".conda").exists()


@pytest.mark.parametrize(
    "options",
    [
        ["numpy"],
        ["https://example.org/second.tar.gz"],
        ["--copy", "."],
        ["--clone", "."],
        ["--name", "other"],
        ["--platform", "linux-64"],
        ["--format", "conda"],
        ["--format", "pixi"],
        ["--channel", "conda-forge"],
        ["--override-channels"],
        ["--force-reinstall"],
        ["--frozen"],
    ],
    ids=[
        "package",
        "second-archive",
        "copy",
        "clone",
        "name",
        "platform",
        "conda-format",
        "pixi-format",
        "channel",
        "override-channels",
        "reinstall",
        "frozen",
    ],
)
def test_quickstart_archive_rejects_conflicting_options_before_download(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    options: list[str],
) -> None:
    archive, server, caller = remote_quickstart
    args = generate_workspace_parser().parse_args(
        ["quickstart", server.url_for("/" + archive.path.name), *options]
    )
    reset_context((), argparse_args=args)

    with pytest.raises(ArgumentError):
        execute_quickstart(args, console=Console(file=StringIO()))

    assert server.log == []
    assert list(caller.iterdir()) == []


@pytest.mark.parametrize(
    "options",
    [
        ["--target", "new"],
        ["--receipt"],
        ["--receipt", "receipt.json"],
        ["--require-sha256"],
        ["--allow-large-archive"],
        ["--archive"],
    ],
    ids=["target", "sibling-receipt", "receipt", "require-sha256", "large", "archive"],
)
def test_quickstart_archive_options_require_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    args = generate_workspace_parser().parse_args(["quickstart", *options])

    with pytest.raises(ArgumentError):
        execute_quickstart(args, console=Console(file=StringIO()))

    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("kind", ["missing", "mismatch"], ids=["missing", "mismatch"])
def test_quickstart_archive_requested_receipt_aborts_before_install(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    kind: str,
) -> None:
    archive, server, caller = remote_quickstart
    route = "/" + archive.path.name
    receipt = json.loads(ArchiveReceipt.default_path(archive.path).read_text())
    if kind == "mismatch":
        receipt["subject"][0]["digest"]["sha256"] = "0" * 64
    server.expect_request(route + ".receipt.json").respond_with_data(
        json.dumps(receipt), status=404 if kind == "missing" else 200
    )
    args = generate_workspace_parser().parse_args(
        ["quickstart", server.url_for(route), "--receipt", "--no-shell"]
    )
    reset_context((), argparse_args=args)

    with pytest.raises(CondaWorkspacesError):
        execute_quickstart(args, console=Console(file=StringIO()))

    assert list(caller.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    [".tar.gz", "...tar.gz", "CON.tar.gz"],
    ids=["empty", "parent", "windows-device"],
)
def test_quickstart_archive_rejects_unsafe_derived_directory(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    name: str,
) -> None:
    archive, server, caller = remote_quickstart
    server.expect_request("/" + name).respond_with_data(archive.path.read_bytes())
    args = generate_workspace_parser().parse_args(
        ["quickstart", server.url_for("/" + name), "--no-shell"]
    )
    reset_context((), argparse_args=args)

    with pytest.raises(CondaWorkspacesError):
        execute_quickstart(args, console=Console(file=StringIO()))

    assert list(caller.iterdir()) == []


def test_quickstart_archive_preserves_existing_destination(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
) -> None:
    archive, server, caller = remote_quickstart
    target = caller / "example"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_bytes(b"existing work")
    args = generate_workspace_parser().parse_args(
        ["quickstart", server.url_for("/" + archive.path.name), "--no-shell"]
    )
    reset_context((), argparse_args=args)

    with pytest.raises(CondaWorkspacesError):
        execute_quickstart(args, console=Console(file=StringIO()))

    assert marker.read_bytes() == b"existing work"
    assert not (target / "conda.toml").exists()


@pytest.fixture
def named_remote_quickstart(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
) -> tuple[WorkspaceArchive, HTTPServer, Path]:
    archive, server, caller = remote_quickstart
    original = archive.path.parent / "workspace"
    manifest = original / "conda.toml"
    manifest.write_text(manifest.read_text() + "\n[environments]\nresearch = []\n")
    lock = original / "conda.lock"
    data = json.loads(lock.read_text())
    data["environments"]["research"] = data["environments"]["default"]
    lock.write_text(json.dumps(data))
    selected = WorkspaceArchive.create(
        workspace=original, output=archive.path.parent / "selected.tar.gz", receipt=True
    )
    server.expect_request("/selected.tar.gz").respond_with_data(
        selected.path.read_bytes()
    )
    return selected, server, caller


def test_quickstart_archive_installs_only_selected_environment(
    named_remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, server, caller = named_remote_quickstart
    spawned = []

    def spawn(*, prefix: Path, shell_cls=None, command=None) -> int:
        spawned.append((prefix, Path.cwd()))
        return 0

    monkeypatch.setattr("conda_spawn.main.spawn", spawn)
    args = generate_workspace_parser().parse_args(
        ["quickstart", server.url_for("/" + archive.path.name), "-e", "research"]
    )
    reset_context((), argparse_args=args)

    assert execute_workspace(args) == 0

    target = caller / "selected"
    prefix = target / ".conda" / "envs" / "research"
    assert (prefix / "conda-meta" / "history").is_file()
    assert not (target / ".conda" / "envs" / "default").exists()
    assert spawned == [(prefix, target)]
    assert not (caller / "automatic-task.txt").exists()
    assert not (target / "automatic-task.txt").exists()


@pytest.mark.parametrize("failure", ["return", "raise"], ids=["status", "exception"])
@pytest.mark.parametrize("json_output", [False, True], ids=["human", "json"])
def test_quickstart_archive_preserves_workspace_on_install_failure(
    named_remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
    json_output: bool,
) -> None:
    archive, server, caller = named_remote_quickstart
    target = caller / "preserved workspace"
    original_install = quickstart_module.execute_install

    def fail_install(args, *, console=None) -> int:
        assert args.locked is True
        assert args.environment == "research"
        assert args.manifest_file == target / "conda.toml"
        assert (target / "app.py").is_file()
        if failure == "raise":
            raise CondaWorkspacesError("Package installation failed")
        return 7

    def unexpected_shell(*, prefix, shell_cls=None, command=None) -> int:
        pytest.fail("Shell must not start after installation failure")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.execute_install", fail_install
    )
    monkeypatch.setattr("conda_spawn.main.spawn", unexpected_shell)
    options = [
        "quickstart",
        server.url_for("/" + archive.path.name),
        "--target",
        str(target),
        "-e",
        "research",
    ]
    if json_output:
        options.append("--json")
    args = generate_workspace_parser().parse_args(options)
    reset_context((), argparse_args=args)
    stream = StringIO()
    if failure == "raise":
        with pytest.raises(CondaWorkspacesError) as error:
            execute_quickstart(args, console=Console(file=stream))
        diagnostic = str(error.value)
        captured = capsys.readouterr()
        if json_output:
            assert captured.out == ""
    else:
        assert execute_quickstart(args, console=Console(file=stream)) == 7
        captured = capsys.readouterr()
        if json_output:
            payload = json.loads(captured.out)
            assert payload["success"] is False
            assert payload["installed"] is False
            assert payload["workspace"] == str(target)
            assert payload["return_code"] == 7
            assert payload["shell_spawned"] is False
            diagnostic = "\n".join(payload["recovery"])
        else:
            diagnostic = stream.getvalue() + captured.err
    assert str(target) in diagnostic
    assert "conda workspace install --locked -e research" in diagnostic
    assert "conda workspace shell -e research" in diagnostic
    assert quote_for_shell("cd", *(["/d"] if on_win else []), str(target)) in diagnostic
    assert (target / "conda.toml").is_file()
    assert (target / "conda.lock").read_bytes() == (
        archive.path.parent / "workspace" / "conda.lock"
    ).read_bytes()
    assert Path.cwd() == caller

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.execute_install", original_install
    )
    monkeypatch.chdir(target)
    resume = generate_workspace_parser().parse_args(
        ["install", "--locked", "-e", "research"]
    )
    reset_context((), argparse_args=resume)
    assert execute_workspace(resume) == 0
    assert (
        target / ".conda" / "envs" / "research" / "conda-meta" / "history"
    ).is_file()


@pytest.mark.parametrize("failure", ["return", "raise"], ids=["status", "exception"])
def test_quickstart_archive_reports_installed_workspace_on_shell_failure(
    named_remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    archive, server, caller = named_remote_quickstart
    target = caller / "selected"

    def fail_shell(*, prefix, shell_cls=None, command=None) -> int:
        if failure == "raise":
            raise OSError("Cannot start shell")
        return 9

    monkeypatch.setattr("conda_spawn.main.spawn", fail_shell)
    args = generate_workspace_parser().parse_args(
        ["quickstart", server.url_for("/" + archive.path.name), "-e", "research"]
    )
    reset_context((), argparse_args=args)
    stream = StringIO()
    if failure == "raise":
        with pytest.raises(CondaWorkspacesError) as error:
            execute_quickstart(args, console=Console(file=stream))
        diagnostic = str(error.value)
    else:
        assert execute_quickstart(args, console=Console(file=stream)) == 9
        diagnostic = stream.getvalue()
    assert "install" in diagnostic.lower()
    assert "conda workspace shell -e research" in diagnostic
    assert (
        target / ".conda" / "envs" / "research" / "conda-meta" / "history"
    ).is_file()
    assert Path.cwd() == caller


def test_quickstart_archive_can_disambiguate_bz2(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
) -> None:
    archive, server, caller = remote_quickstart
    bz2 = WorkspaceArchive.create(
        workspace=archive.path.parent / "workspace",
        output=archive.path.parent / "workspace.tar.bz2",
    )
    server.expect_request("/workspace.tar.bz2").respond_with_data(bz2.path.read_bytes())
    args = generate_workspace_parser().parse_args(
        ["quickstart", server.url_for("/workspace.tar.bz2"), "--archive", "--no-shell"]
    )
    reset_context((), argparse_args=args)

    assert execute_workspace(args) == 0

    assert (
        caller / "workspace" / ".conda" / "envs" / "default" / "conda-meta" / "history"
    ).is_file()


@pytest.mark.parametrize("json_mode", ["flag", "console", "environment"])
@pytest.mark.parametrize("token", ["secret", "secret:1"], ids=["plain", "colon"])
@pytest.mark.parametrize("scheme", ["http", "HTTP"], ids=["lowercase", "uppercase"])
def test_quickstart_archive_json_redacts_source_credentials(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    json_mode: str,
    token: str,
    scheme: str,
) -> None:
    archive, server, _ = remote_quickstart
    source = server.url_for("/" + archive.path.name).replace(
        "http://", f"{scheme}://reader:password@"
    )

    def unexpected_shell(*, prefix, shell_cls=None, command=None) -> int:
        pytest.fail("JSON mode must not start a shell")

    monkeypatch.setattr("conda_spawn.main.spawn", unexpected_shell)
    options = ["quickstart", source + "?token=" + token]
    if json_mode == "flag":
        options.append("--json")
    elif json_mode == "console":
        options.extend(["--console", "json"])
    else:
        monkeypatch.setenv("CONDA_JSON", "true")
    args = generate_workspace_parser().parse_args(options)
    reset_context((), argparse_args=args)

    assert execute_workspace(args) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["artifact"] == archive.path.name
    for secret in ["password", "secret"]:
        assert secret not in captured.out
        assert secret not in captured.err


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_quickstart_archive_failure_does_not_fall_back_to_package_install(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    failure: str,
) -> None:
    _, server, caller = remote_quickstart
    server.expect_request("/unusable.tar.gz").respond_with_data(
        b"not an archive", status=404 if failure == "missing" else 200
    )
    args = generate_workspace_parser().parse_args(
        ["quickstart", server.url_for("/unusable.tar.gz"), "--no-shell"]
    )
    reset_context((), argparse_args=args)

    with pytest.raises(CondaWorkspacesError):
        execute_quickstart(args, console=Console(file=StringIO()))

    assert list(caller.iterdir()) == []
    assert [request.path for request, _ in server.log] == ["/unusable.tar.gz"]


def test_quickstart_archive_rejects_explicit_caller_manifest(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
) -> None:
    archive, server, caller = remote_quickstart
    manifest = caller / "conda.toml"
    manifest.write_text("[workspace]\nname = 'caller'\nchannels = []\n")
    args = generate_workspace_parser().parse_args(
        ["--file", str(manifest), "quickstart", server.url_for("/" + archive.path.name)]
    )
    reset_context((), argparse_args=args)

    with pytest.raises(ArgumentError, match="--file"):
        execute_quickstart(args, console=Console(file=StringIO()))

    assert server.log == []
    assert not (caller / "example").exists()


def test_quickstart_parser_default_format_creates_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    installed = []

    def install(args, *, console=None) -> int:
        assert (tmp_path / "conda.toml").is_file()
        installed.append(args.environment)
        return 0

    monkeypatch.setattr(quickstart_module, "execute_install", install)
    args = generate_workspace_parser().parse_args(["quickstart", "--no-shell"])

    assert execute_quickstart(args, console=Console(file=StringIO())) == 0

    assert installed == ["default"]
    assert "[workspace]" in (tmp_path / "conda.toml").read_text()


def test_quickstart_archive_leaves_unverified_bundled_packages_unused(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive, server, caller = remote_quickstart
    package = archive.path.parent / "unused-1.0-0.conda"
    package.write_bytes(b"not a valid conda package")
    bundled = archive.path.parent / "bundled.tar.gz"
    create_archive(
        archive.path.parent / "workspace",
        bundled,
        ArchiveConfig(),
        bundle_packages=[package],
    )
    server.expect_request("/bundled.tar.gz").respond_with_data(bundled.read_bytes())
    args = generate_workspace_parser().parse_args(
        ["quickstart", server.url_for("/bundled.tar.gz"), "--json"]
    )
    reset_context((), argparse_args=args)

    assert execute_workspace(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["installed"] is True
    assert payload["verified"] is False
    assert payload["primed_packages"] == 0
    assert payload["cache_priming_skipped"] is True
    assert (
        caller / "bundled" / ".conda" / "envs" / "default" / "conda-meta" / "history"
    ).is_file()


@pytest.mark.parametrize(
    "path", ["/download", "/workspace.zip"], ids=["no-suffix", "zip"]
)
@pytest.mark.parametrize("token", ["secret", "secret:1"], ids=["plain", "colon"])
@pytest.mark.parametrize("scheme", ["http", "HTTP"], ids=["lowercase", "uppercase"])
def test_quickstart_rejects_unknown_url_without_side_effects(
    remote_quickstart: tuple[WorkspaceArchive, HTTPServer, Path],
    path: str,
    token: str,
    scheme: str,
) -> None:
    _, server, caller = remote_quickstart
    source = server.url_for(path).replace("http://", f"{scheme}://reader:password@")
    args = generate_workspace_parser().parse_args(
        ["quickstart", source + "?token=" + token, "--no-shell"]
    )
    reset_context((), argparse_args=args)

    with pytest.raises(ArgumentError) as error:
        execute_quickstart(args, console=Console(file=StringIO()))

    message = str(error.value)
    assert ".conda" in message
    assert ".tar" in message
    assert "password" not in message
    assert "secret" not in message
    assert server.log == []
    assert list(caller.iterdir()) == []
