"""Tests for conda_workspaces.cli.workspace.install."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conda.exceptions import CondaValueError

import conda_workspaces.cli.workspace.install as install_mod
from conda_workspaces.cli.workspace import workspace_context_from_args
from conda_workspaces.cli.workspace.install import (
    execute_install,
    install_from_lockfile_all,
)
from conda_workspaces.exceptions import (
    AttestationError,
    CondaWorkspacesError,
    LockfileNotFoundError,
    LockfileStaleError,
)
from conda_workspaces.models import LockfileStatus

from ..conftest import make_args

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console

_DEFAULTS = {
    "manifest_file": None,
    "environment": None,
    "force_reinstall": False,
    "dry_run": False,
    "locked": False,
    "frozen": False,
    "no_lock": False,
}
_RENDERED_LOCK = """\
version: 1
environments: {}
packages: []
"""


def _stub_lockfile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub lock rendering and writing for tests that don't inspect them."""
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: _RENDERED_LOCK,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.write_lockfile",
        lambda ctx, content: None,
    )


@pytest.fixture
def write_stub_lockfile() -> Callable[[Path], None]:
    """Return a writer for minimal lockfiles used with replaced installers."""

    def write(workspace: Path) -> None:
        (workspace / "conda.lock").write_text(_RENDERED_LOCK, encoding="utf-8")

    return write


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
@pytest.mark.parametrize("boundary", ["leaf", "parent"], ids=["leaf", "parent"])
def test_install_rejects_symlinked_manifest(
    pixi_workspace: Path,
    dry_run: bool,
    boundary: str,
) -> None:
    if boundary == "leaf":
        linked_boundary = pixi_workspace / "linked.toml"
        linked_boundary.symlink_to(pixi_workspace / "pixi.toml")
        linked_manifest = linked_boundary
    else:
        linked_boundary = pixi_workspace / "linked-parent"
        linked_boundary.symlink_to(pixi_workspace, target_is_directory=True)
        linked_manifest = linked_boundary / "pixi.toml"

    with pytest.raises(
        (CondaWorkspacesError, NotADirectoryError),
        match="symlink|symbolic link",
    ):
        execute_install(
            make_args(
                _DEFAULTS,
                manifest_file=linked_manifest,
                dry_run=dry_run,
            )
        )

    assert linked_boundary.is_symlink()


@pytest.mark.parametrize(
    ("dry_run", "expected_error"),
    [
        pytest.param(False, CondaWorkspacesError, id="install"),
        pytest.param(True, LockfileNotFoundError, id="dry-run"),
    ],
)
def test_install_frozen_rejects_symlinked_lockfile(
    pixi_workspace: Path,
    tmp_path: Path,
    dry_run: bool,
    expected_error: type[Exception],
) -> None:
    lockfile = pixi_workspace / "conda.lock"
    lockfile.unlink(missing_ok=True)
    outside = tmp_path / "outside.lock"
    outside.write_text(_RENDERED_LOCK, encoding="utf-8")
    lockfile.symlink_to(outside)

    with pytest.raises(expected_error):
        execute_install(
            make_args(
                _DEFAULTS,
                manifest_file=pixi_workspace / "pixi.toml",
                frozen=True,
                dry_run=dry_run,
            )
        )

    assert lockfile.is_symlink()
    assert outside.read_text(encoding="utf-8") == _RENDERED_LOCK


@pytest.mark.parametrize(
    ("env_arg", "expected_installed", "output_fragment"),
    [
        ("default", {"default"}, "Installed"),
        (None, {"default", "test"}, "Installed"),
    ],
    ids=["single-env", "all-envs"],
)
def test_install_envs(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env_arg: str | None,
    expected_installed: set[str],
    output_fragment: str,
    replace_lockfile_install_plan,
    replace_publication_writer,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    monkeypatch.delenv("CI", raising=False)

    calls: list[str] = []
    events: list[str] = []

    def fake_install(phase, ctx, name, kwargs):
        calls.append(name)
        action = "preflight" if phase == "prepare" else "install"
        events.append(f"{action}-{name}")

    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        fake_install,
    )

    lock_calls: list[dict] = []
    write_calls: list[str] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: (
            lock_calls.append(resolved_envs),
            events.append("render"),
            _RENDERED_LOCK,
        )[-1],
    )
    replace_publication_writer(
        lambda path, content, write: (
            write_calls.append(content),
            events.append("write"),
            write(content),
        ),
    )

    args = make_args(_DEFAULTS, environment=env_arg)
    result = execute_install(args)
    assert result == 0
    assert set(calls) == expected_installed
    assert output_fragment in capsys.readouterr().out
    assert len(lock_calls) == 1
    assert set(lock_calls[0]) == {"default", "test"}
    assert write_calls == [_RENDERED_LOCK]
    assert events[0] == "render"
    expected_order = [env_arg] if env_arg else ["default", "test"]
    preflights = [f"preflight-{name}" for name in expected_order]
    installs = [f"install-{name}" for name in expected_order]
    assert events == ["render", *preflights, "write", *installs]


@pytest.mark.parametrize(
    "force, dry_run",
    [
        (True, False),
        (False, True),
        (True, True),
    ],
    ids=["force", "dry-run", "force-dry-run"],
)
def test_install_flags_forwarded(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    force: bool,
    dry_run: bool,
    replace_lockfile_install_plan,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    monkeypatch.delenv("CI", raising=False)
    _stub_lockfile(monkeypatch)

    installed: list[tuple[str, bool]] = []

    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda phase, ctx, name, kwargs: installed.append((name, phase == "prepare")),
    )

    args = make_args(
        _DEFAULTS,
        environment="default",
        force_reinstall=force,
        dry_run=dry_run,
    )
    execute_install(args)
    if dry_run:
        assert installed == [("default", True)]
    else:
        assert installed == [("default", True), ("default", False)]


def test_install_dry_run_previews_lockfile(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_lockfile_install_plan,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    monkeypatch.delenv("CI", raising=False)

    lock_calls: list[tuple[dict, bool]] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: (
            lock_calls.append((resolved_envs, kwargs["dry_run"])),
            _RENDERED_LOCK,
        )[-1],
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda *_args: None,
    )

    args = make_args(_DEFAULTS, environment="default", dry_run=True)
    execute_install(args)
    assert len(lock_calls) == 1
    assert set(lock_calls[0][0]) == {"default", "test"}
    assert lock_calls[0][1] is True


@pytest.mark.parametrize(
    "env_arg, expected_names, output_fragment",
    [
        ("default", {"default"}, "Installed"),
        (None, {"default", "test"}, "Installed"),
    ],
    ids=["single-env", "all-envs"],
)
def test_install_frozen(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write_stub_lockfile: Callable[[Path], None],
    env_arg: str | None,
    expected_names: set[str],
    output_fragment: str,
    replace_lockfile_install_plan,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    write_stub_lockfile(pixi_workspace)

    locked_calls: list[str] = []
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        lambda phase, ctx, name, kwargs: locked_calls.append(name),
    )

    args = make_args(_DEFAULTS, environment=env_arg, frozen=True)
    result = execute_install(args)
    assert result == 0
    assert set(locked_calls) == expected_names
    assert output_fragment in capsys.readouterr().out


def test_install_frozen_uses_one_lock_snapshot_for_all_environments(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_stub_lockfile: Callable[[Path], None],
    replace_lockfile_install_plan,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    write_stub_lockfile(pixi_workspace)
    lockfile = pixi_workspace / "conda.lock"
    snapshots: list[tuple[str, bool, dict]] = []

    def record_install(phase, ctx, name, kwargs) -> None:
        snapshots.append((name, phase == "prepare", kwargs["lockfile_data"]))
        if len(snapshots) == 1:
            lockfile.write_text(
                "version: 1\nenvironments:\n  changed: {}\npackages: []\n",
                encoding="utf-8",
            )

    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        record_install,
    )

    assert execute_install(make_args(_DEFAULTS, frozen=True)) == 0
    assert [(name, dry_run) for name, dry_run, _ in snapshots] == [
        ("default", True),
        ("test", True),
        ("default", False),
        ("test", False),
    ]
    assert all(data is snapshots[0][2] for _, _, data in snapshots)
    assert snapshots[0][2]["environments"] == {}


def test_install_prevalidates_all_environments_before_mutation(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_stub_lockfile: Callable[[Path], None],
    replace_lockfile_install_plan,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    write_stub_lockfile(pixi_workspace)
    calls: list[tuple[str, str]] = []

    def reject_second_preview(phase, ctx, name, kwargs) -> None:
        calls.append((name, phase))
        if name == "test" and phase == "prepare":
            raise CondaWorkspacesError("invalid test environment")

    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        reject_second_preview,
        preflight_prefix_identity=(7, 11),
    )

    with pytest.raises(CondaWorkspacesError, match="invalid test environment"):
        execute_install(make_args(_DEFAULTS, frozen=True, force_reinstall=True))

    assert calls == [("default", "prepare"), ("test", "prepare")]


def test_install_force_reinstall_rejects_explicit_prefix(
    pixi_workspace: Path,
    rich_console: Console,
) -> None:
    config, ctx = workspace_context_from_args(
        make_args(_DEFAULTS, manifest_file=pixi_workspace / "pixi.toml")
    )

    with pytest.raises(CondaWorkspacesError, match="explicit prefix"):
        install_from_lockfile_all(
            ctx,
            config,
            "default",
            console=rich_console,
            prefix=pixi_workspace / "custom-prefix",
            force_reinstall=True,
        )


def test_install_verifies_the_exact_lockfile_buffer_before_parsing(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    replace_lockfile_install_plan,
) -> None:
    config, ctx = workspace_context_from_args(
        make_args(_DEFAULTS, manifest_file=pixi_workspace / "pixi.toml")
    )
    lockfile_bytes = _RENDERED_LOCK.encode("utf-8")
    events: list[tuple[str, bytes]] = []
    original_load = install_mod.load_lockfile_data

    def verify(value: bytes) -> None:
        events.append(("verify", value))

    def load(value: bytes):
        events.append(("load", value))
        return original_load(value)

    monkeypatch.setattr(install_mod, "load_lockfile_data", load)
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        lambda phase, ctx, name, kwargs: None,
    )

    assert (
        install_from_lockfile_all(
            ctx,
            config,
            "default",
            console=rich_console,
            dry_run=True,
            read_lockfile=lambda: lockfile_bytes,
            verify_lockfile=verify,
        )
        == 0
    )

    assert [event for event, _ in events] == ["verify", "load"]
    assert all(value is lockfile_bytes for _, value in events)


def test_install_verification_failure_precedes_plan_preparation(
    pixi_workspace: Path,
    rich_console: Console,
    replace_lockfile_install_plan,
) -> None:
    config, ctx = workspace_context_from_args(
        make_args(_DEFAULTS, manifest_file=pixi_workspace / "pixi.toml")
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        lambda *args, **kwargs: pytest.fail(
            "prepared a plan after verification failed"
        ),
    )

    def reject(value: bytes) -> None:
        raise AttestationError("invalid attestation")

    with pytest.raises(AttestationError, match="invalid attestation"):
        install_from_lockfile_all(
            ctx,
            config,
            "default",
            console=rich_console,
            read_lockfile=lambda: _RENDERED_LOCK.encode("utf-8"),
            verify_lockfile=reject,
        )


@pytest.mark.parametrize("mode", ["locked", "frozen"], ids=["locked", "frozen"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
@pytest.mark.parametrize(
    "attestation_name",
    [None, "release.sigstore.json"],
    ids=["default-sidecar", "explicit-sidecar"],
)
def test_execute_install_wires_workspace_verification(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_stub_lockfile: Callable[[Path], None],
    replace_lockfile_install_plan,
    mode: str,
    dry_run: bool,
    attestation_name: str | None,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    write_stub_lockfile(pixi_workspace)
    lockfile = pixi_workspace / "conda.lock"
    explicit_sidecar = (
        pixi_workspace / attestation_name if attestation_name is not None else None
    )
    expected_sidecar = explicit_sidecar or Path(f"{lockfile}.sigstore.json")
    events: list[tuple[str, object]] = []

    if mode == "locked":
        monkeypatch.setattr(
            install_mod,
            "lockfile_status",
            lambda ctx, config: LockfileStatus(status=LockfileStatus.UP_TO_DATE),
        )
        monkeypatch.setattr(
            install_mod,
            "check_lockfile_satisfiability",
            lambda config, data, platform: LockfileStatus(
                status=LockfileStatus.UP_TO_DATE
            ),
        )

    def read_bundle(path: Path) -> bytes:
        events.append(("read-bundle", path))
        return b'{"bundle":true}'

    class Verification:
        evidence: Verification

        def __init__(self) -> None:
            self.evidence = self

        def require_authorized(self) -> None:
            events.append(("authorize", None))

    def verify_bundle(bundle, snapshot, expected_signer):
        events.append(("verify", snapshot))
        assert bundle == b'{"bundle":true}'
        assert snapshot.manifest_path == pixi_workspace / "pixi.toml"
        assert snapshot.manifest_bytes == (pixi_workspace / "pixi.toml").read_bytes()
        assert snapshot.manifest_format == "pixi-toml"
        assert snapshot.lockfile_path == lockfile
        assert snapshot.lockfile_bytes == lockfile.read_bytes()
        assert expected_signer.identity == "release@example.com"
        assert expected_signer.issuer == "https://issuer.example"
        return Verification()

    def record_plan(phase, ctx, name, kwargs) -> None:
        events.append((phase, name))

    monkeypatch.setattr(install_mod, "read_attestation_bundle", read_bundle)
    monkeypatch.setattr(install_mod, "verify_workspace_snapshot", verify_bundle)
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        record_plan,
    )

    assert (
        execute_install(
            make_args(
                _DEFAULTS,
                environment="default",
                dry_run=dry_run,
                verify=True,
                cert_identity="release@example.com",
                cert_oidc_issuer="https://issuer.example",
                attestation=explicit_sidecar,
                **{mode: True},
            )
        )
        == 0
    )

    assert events[0] == ("read-bundle", expected_sidecar)
    assert [name for name, _ in events[:3]] == [
        "read-bundle",
        "verify",
        "authorize",
    ]
    expected_plans = [("prepare", "default")]
    if not dry_run:
        expected_plans.append(("execute", "default"))
    assert events[3:] == expected_plans


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
def test_execute_install_verification_failure_precedes_plans(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_stub_lockfile: Callable[[Path], None],
    replace_lockfile_install_plan,
    dry_run: bool,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    write_stub_lockfile(pixi_workspace)
    monkeypatch.setattr(install_mod, "read_attestation_bundle", lambda path: b"bundle")

    def reject_verification(*args) -> None:
        raise AttestationError("invalid attestation")

    monkeypatch.setattr(
        install_mod,
        "verify_workspace_snapshot",
        reject_verification,
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        lambda *args: pytest.fail("prepared a plan after verification failed"),
    )

    with pytest.raises(AttestationError, match="invalid attestation"):
        execute_install(
            make_args(
                _DEFAULTS,
                environment="default",
                frozen=True,
                dry_run=dry_run,
                verify=True,
                cert_identity="release@example.com",
                cert_oidc_issuer="https://issuer.example",
            )
        )


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"verify": True}, id="verify-without-lock-mode"),
        pytest.param(
            {"attestation": Path("bundle.json")},
            id="attestation-without-verify",
        ),
        pytest.param(
            {"cert_identity": "signer@example.com"},
            id="identity-without-verify",
        ),
        pytest.param(
            {"verify": True, "frozen": True},
            id="verify-without-policy",
        ),
        pytest.param(
            {
                "verify": True,
                "locked": True,
                "cert_identity": "signer@example.com",
            },
            id="partial-policy",
        ),
    ],
)
def test_install_rejects_invalid_attestation_option_combinations(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, object],
) -> None:
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises((CondaValueError, AttestationError), match="require|together"):
        execute_install(make_args(_DEFAULTS, **options))


def test_install_fetches_every_environment_before_mutation(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    platforms = ("linux-64", "osx-arm64", "win-64")
    package_tables = "".join(f"      {platform}: []\n" for platform in platforms)
    (pixi_workspace / "conda.lock").write_text(
        "version: 1\n"
        "environments:\n"
        "  default:\n"
        "    channels: []\n"
        "    packages:\n"
        f"{package_tables}"
        "  test:\n"
        "    channels: []\n"
        "    packages:\n"
        f"{package_tables}"
        "packages: []\n",
        encoding="utf-8",
    )
    fetches: list[int] = []

    def fetch_records(urls):
        fetches.append(len(list(urls)))
        if len(fetches) == 2:
            raise RuntimeError("test package fetch failed")
        return []

    mutations: list[str] = []
    monkeypatch.setattr(
        "conda.misc.get_package_records_from_explicit",
        fetch_records,
    )
    monkeypatch.setattr(
        "conda.misc.install_explicit_packages",
        lambda **kwargs: mutations.append(kwargs["prefix"]),
    )

    with pytest.raises(RuntimeError, match="test package fetch failed"):
        execute_install(make_args(_DEFAULTS, frozen=True))

    assert fetches == [0, 0]
    assert mutations == []
    assert not (pixi_workspace / ".pixi" / "envs" / "default").exists()


def test_install_revalidates_guard_after_lock_snapshot(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    write_stub_lockfile: Callable[[Path], None],
    replace_lockfile_install_plan,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    write_stub_lockfile(pixi_workspace)

    config, ctx = workspace_context_from_args(make_args(_DEFAULTS))
    changed = [False]
    original_read = (pixi_workspace / "conda.lock").read_bytes()

    def read_and_replace_generation(*args, **kwargs):
        changed[0] = True
        return original_read

    def validate_workspace() -> None:
        if changed[0]:
            raise CondaWorkspacesError("workspace root changed")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.read_regular_file_bytes",
        read_and_replace_generation,
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        lambda *args, **kwargs: pytest.fail("used an unguarded lock snapshot"),
    )

    with pytest.raises(CondaWorkspacesError, match="workspace root changed"):
        install_from_lockfile_all(
            ctx,
            config,
            None,
            console=rich_console,
            validate_workspace=validate_workspace,
        )


@pytest.mark.parametrize(
    "mode",
    ["frozen", "locked", "current", "ci"],
    ids=["frozen", "locked", "up-to-date", "ci"],
)
@pytest.mark.parametrize(
    "force_reinstall",
    [False, True],
    ids=["ordinary", "force"],
)
def test_install_lockfile_paths_forward_dry_run(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write_stub_lockfile: Callable[[Path], None],
    mode: str,
    force_reinstall: bool,
    replace_lockfile_install_plan,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    if mode == "ci":
        monkeypatch.setenv("CI", "true")
    else:
        monkeypatch.delenv("CI", raising=False)
    write_stub_lockfile(pixi_workspace)
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.lockfile_status",
        lambda ctx, config: LockfileStatus(status=LockfileStatus.UP_TO_DATE),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.check_lockfile_satisfiability",
        lambda config, data, platform: LockfileStatus(status=LockfileStatus.UP_TO_DATE),
    )
    calls: list[tuple[str, bool, bool]] = []

    def record_install(phase, ctx, name, kwargs) -> None:
        calls.append((name, phase == "prepare", kwargs["replace_existing"]))

    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        record_install,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.sync_environments",
        lambda *args, **kwargs: pytest.fail("used the solver instead of the lockfile"),
    )
    kwargs = {mode: True} if mode in {"frozen", "locked"} else {}

    result = execute_install(
        make_args(
            _DEFAULTS,
            environment="default",
            dry_run=True,
            force_reinstall=force_reinstall,
            **kwargs,
        )
    )

    assert result == 0
    assert calls == [("default", True, force_reinstall)]
    assert "Would install" in capsys.readouterr().out


@pytest.mark.parametrize(
    "prefix_identity",
    [(7, 11), None],
    ids=["existing-prefix", "absent-prefix"],
)
def test_install_from_lockfile_all_force_reinstall(
    pixi_workspace: Path,
    rich_console: Console,
    write_stub_lockfile: Callable[[Path], None],
    prefix_identity: tuple[int, int] | None,
    replace_lockfile_install_plan,
) -> None:
    write_stub_lockfile(pixi_workspace)
    config, ctx = workspace_context_from_args(
        make_args(_DEFAULTS, manifest_file=pixi_workspace / "pixi.toml")
    )
    events: list[tuple[str, object]] = []

    def record_install(phase, ctx, name, kwargs) -> None:
        if phase == "prepare":
            events.append(("prepare", kwargs["replace_existing"]))
        elif phase == "remove":
            events.append(("remove", kwargs["expected_prefix_identity"]))
        else:
            events.append(("execute", None))

    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        record_install,
        preflight_prefix_identity=prefix_identity,
    )

    assert (
        install_from_lockfile_all(
            ctx,
            config,
            "default",
            console=rich_console,
            force_reinstall=True,
        )
        == 0
    )

    expected = [("prepare", True)]
    if prefix_identity is not None:
        expected.append(("remove", prefix_identity))
    expected.append(("execute", None))
    assert events == expected


def test_install_locked_validates_freshness(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--locked fails when lockfile is older than the manifest."""
    monkeypatch.chdir(pixi_workspace)

    lock_file = pixi_workspace / "conda.lock"
    lock_file.write_text("version: 1\n", encoding="utf-8")
    time.sleep(0.05)

    manifest = pixi_workspace / "pixi.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")

    args = make_args(_DEFAULTS, locked=True)
    with pytest.raises(LockfileStaleError):
        execute_install(args)


def test_install_default_uses_lockfile_when_satisfiable(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_stub_lockfile: Callable[[Path], None],
    replace_lockfile_install_plan,
) -> None:
    """Default install uses lockfile when it satisfies the manifest."""
    monkeypatch.chdir(pixi_workspace)
    write_stub_lockfile(pixi_workspace)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.lockfile_status",
        lambda ctx, config: LockfileStatus(status=LockfileStatus.UP_TO_DATE),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.check_lockfile_satisfiability",
        lambda config, data, platform: LockfileStatus(status=LockfileStatus.UP_TO_DATE),
    )

    locked_calls: list[str] = []
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        lambda phase, ctx, name, kwargs: locked_calls.append(name),
    )

    sync_calls: list[str] = []
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda phase, ctx, name, kwargs: sync_calls.append(name),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: _RENDERED_LOCK,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.write_lockfile",
        lambda ctx, content: None,
    )

    args = make_args(_DEFAULTS)
    result = execute_install(args)
    assert result == 0
    assert len(locked_calls) > 0
    assert len(sync_calls) == 0


def test_install_default_solves_when_not_satisfiable(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_lockfile_install_plan,
) -> None:
    """Default install falls back to solve when lockfile is not satisfiable."""
    monkeypatch.chdir(pixi_workspace)
    monkeypatch.delenv("CI", raising=False)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.lockfile_status",
        lambda ctx, config: LockfileStatus(
            status=LockfileStatus.OUT_OF_DATE, reason="dep missing"
        ),
    )

    locked_calls: list[str] = []
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        lambda phase, ctx, name, kwargs: locked_calls.append(name),
    )

    sync_calls: list[str] = []
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda phase, ctx, name, kwargs: sync_calls.append(name),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: _RENDERED_LOCK,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.write_lockfile",
        lambda ctx, content: None,
    )

    args = make_args(_DEFAULTS)
    result = execute_install(args)
    assert result == 0
    assert len(locked_calls) == 0
    assert len(sync_calls) > 0


def test_install_lockfile_reason_does_not_emit_terminal_controls(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    rich_console: Console,
    replace_lockfile_install_plan,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    monkeypatch.delenv("CI", raising=False)
    reason = "[bold]\x1b]52;c;QUJD\x07\x9b31m"
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.lockfile_status",
        lambda ctx, config: LockfileStatus(
            status=LockfileStatus.OUT_OF_DATE,
            reason=reason,
        ),
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda phase, ctx, name, kwargs: None,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: _RENDERED_LOCK,
    )

    result = execute_install(
        make_args(_DEFAULTS, dry_run=True),
        console=rich_console,
    )

    assert result == 0
    output = rich_console.file.getvalue()
    assert "\x1b" not in output
    assert "\x07" not in output
    assert "\x9b" not in output
    assert r"\x1b" in output
    assert r"\x07" in output
    assert r"\x9b" in output
    assert "[bold]" in output


def test_install_no_lock_forces_solve(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_lockfile_install_plan,
    replace_publication_writer,
) -> None:
    """--no-lock forces a full solve even when lockfile is satisfiable."""
    monkeypatch.chdir(pixi_workspace)

    sync_calls: list[str] = []
    events: list[str] = []
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda phase, ctx, name, kwargs: (
            sync_calls.append(name),
            events.append(f"{'preflight' if phase == 'prepare' else 'install'}-{name}"),
        ),
    )
    lock_calls: list[dict] = []
    write_calls: list[str] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        lambda ctx, resolved_envs, **kwargs: (
            lock_calls.append(resolved_envs),
            events.append("render"),
            _RENDERED_LOCK,
        )[-1],
    )
    replace_publication_writer(
        lambda path, content, write: (
            write_calls.append(content),
            events.append("write"),
            write(content),
        ),
    )

    args = make_args(_DEFAULTS, environment="test", no_lock=True)
    result = execute_install(args)
    assert result == 0
    assert sync_calls == ["test", "test"]
    assert set(lock_calls[0]) == {"default", "test"}
    assert write_calls == [_RENDERED_LOCK]
    assert events == ["render", "preflight-test", "write", "install-test"]


def test_install_rejects_manifest_changed_during_lock_render(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_lockfile_install_plan,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    monkeypatch.delenv("CI", raising=False)
    manifest = pixi_workspace / "pixi.toml"
    lockfile = pixi_workspace / "conda.lock"
    installs: list[tuple[str, bool]] = []

    def render_and_change_manifest(*args: object, **kwargs: object) -> str:
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + "\n# concurrent change\n",
            encoding="utf-8",
        )
        return _RENDERED_LOCK

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        render_and_change_manifest,
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        lambda phase, ctx, name, kwargs: installs.append((name, phase == "prepare")),
    )

    with pytest.raises(CondaWorkspacesError, match="manifest changed"):
        execute_install(make_args(_DEFAULTS, environment="default"))

    assert not lockfile.exists()
    assert installs == [("default", True)]


@pytest.mark.parametrize(
    ("has_lockfile", "satisfiable", "expected_error", "expected_locked"),
    [
        pytest.param(True, False, LockfileStaleError, False, id="unsatisfiable"),
        pytest.param(False, None, LockfileNotFoundError, False, id="missing"),
        pytest.param(True, True, None, True, id="satisfiable"),
    ],
)
def test_install_ci_mode(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_lockfile: bool,
    satisfiable: bool | None,
    expected_error: type[Exception] | None,
    expected_locked: bool,
    replace_lockfile_install_plan,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    monkeypatch.setenv("CI", "true")

    if has_lockfile:
        (pixi_workspace / "conda.lock").write_text("version: 1\n", encoding="utf-8")

    def fake_lockfile_status(ctx, config):
        if not has_lockfile:
            return LockfileStatus(status=LockfileStatus.MISSING)
        if satisfiable:
            return LockfileStatus(status=LockfileStatus.UP_TO_DATE)
        return LockfileStatus(status=LockfileStatus.OUT_OF_DATE, reason="dep missing")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.lockfile_status",
        fake_lockfile_status,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.install.check_lockfile_satisfiability",
        lambda config, data, platform: LockfileStatus(
            status=(
                LockfileStatus.UP_TO_DATE if satisfiable else LockfileStatus.OUT_OF_DATE
            ),
            reason=None if satisfiable else "dep missing",
        ),
    )

    locked_calls: list[str] = []
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.install",
        lambda phase, ctx, name, kwargs: locked_calls.append(name),
    )

    args = make_args(_DEFAULTS)
    if expected_error is not None:
        with pytest.raises(expected_error):
            execute_install(args)
    else:
        result = execute_install(args)
        assert result == 0
        assert expected_locked == (len(locked_calls) > 0)
