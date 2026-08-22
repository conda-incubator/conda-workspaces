"""Tests for conda_workspaces.cli.workspace.lock."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from conda.exceptions import CondaValueError
from rich.console import Console

from conda_workspaces.attestations import AttestationOutput
from conda_workspaces.cli.workspace.lock import execute_lock
from conda_workspaces.exceptions import (
    AttestationError,
    CondaWorkspacesError,
    EnvironmentNotFoundError,
    PlatformError,
)

from ..conftest import make_args

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import SnapshotTree

_DEFAULTS = {
    "manifest_file": None,
    "environment": None,
    "platform": None,
    "skip_unsolvable": False,
    "merge": None,
    "output": None,
    "dry_run": False,
    "sign": False,
    "attestation": None,
}

_RENDERED_LOCK = "version: 1\nenvironments: {}\npackages: []\n"


@pytest.fixture
def capture_generate_lockfile(monkeypatch: pytest.MonkeyPatch):
    """Patch ``generate_lockfile`` and return a list of captured kwargs.

    Each call is recorded as a dict with ``resolved_envs`` (dict of
    ``ResolvedEnvironment``), ``platforms``, ``progress``,
    ``skip_unsolvable``, and ``on_skip`` so tests can assert the CLI
    forwards ``--platform`` / ``--skip-unsolvable`` correctly.
    """
    calls: list[dict] = []

    def fake_generate(
        ctx,
        resolved_envs,
        *,
        config=None,
        platforms=None,
        progress=None,
        skip_unsolvable=False,
        on_skip=None,
        output_path=None,
        dry_run=False,
        publish_lockfile=None,
    ):
        calls.append(
            {
                "resolved_envs": resolved_envs,
                "config": config,
                "platforms": platforms,
                "progress": progress,
                "skip_unsolvable": skip_unsolvable,
                "on_skip": on_skip,
                "output_path": output_path,
                "dry_run": dry_run,
                "publish_lockfile": publish_lockfile,
            }
        )

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.generate_lockfile", fake_generate
    )
    return calls


@pytest.mark.parametrize(
    ("env_arg", "expected_keys", "output_name"),
    [
        ("default", {"default"}, "conda.lock"),
        (None, {"default", "test"}, None),
    ],
    ids=["single-env", "all-envs"],
)
@pytest.mark.parametrize(
    ("dry_run", "output_fragment"),
    [(False, "Updated"), (True, "Would update")],
    ids=["write", "dry-run"],
)
def test_lock_envs(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    capture_generate_lockfile: list[dict],
    env_arg: str | None,
    expected_keys: set[str],
    output_name: str | None,
    dry_run: bool,
    output_fragment: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    output_path = pixi_workspace / output_name if output_name else None

    result = execute_lock(
        make_args(
            _DEFAULTS,
            environment=env_arg,
            output=output_path,
            dry_run=dry_run,
        )
    )
    assert result == 0
    assert len(capture_generate_lockfile) == 1
    assert set(capture_generate_lockfile[0]["resolved_envs"].keys()) == expected_keys
    assert capture_generate_lockfile[0]["config"] is not None
    assert capture_generate_lockfile[0]["platforms"] is None
    assert capture_generate_lockfile[0]["output_path"] == output_path
    assert capture_generate_lockfile[0]["dry_run"] is dry_run
    assert output_fragment in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["generate", "merge"], ids=["generate", "merge"])
def test_lock_signs_exact_canonical_publication(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    signed: list[bytes] = []

    def publish(*args, publish_lockfile=None, **kwargs) -> None:
        assert publish_lockfile is not None
        publish_lockfile(_RENDERED_LOCK)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.generate_lockfile",
        publish,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.merge_lockfiles",
        publish,
    )

    def sign(snapshot) -> str:
        signed.append(snapshot.lockfile_bytes)
        return '{"bundle":true}'

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.sign_workspace_snapshot",
        sign,
    )
    fragment = pixi_workspace / "fragment.lock"
    fragment.write_text(_RENDERED_LOCK, encoding="utf-8")

    assert (
        execute_lock(
            make_args(
                _DEFAULTS,
                sign=True,
                merge=[str(fragment)] if mode == "merge" else None,
            )
        )
        == 0
    )

    assert (pixi_workspace / "conda.lock").read_text(encoding="utf-8") == (
        _RENDERED_LOCK
    )
    assert (pixi_workspace / "conda.lock.sigstore.json").read_text(
        encoding="utf-8"
    ) == '{"bundle":true}\n'
    assert signed == [_RENDERED_LOCK.encode("utf-8")]


@pytest.mark.parametrize(
    "options",
    [
        pytest.param(
            {"attestation": Path("bundle.json")},
            id="attestation-without-sign",
        ),
        pytest.param(
            {
                "sign": True,
                "environment": "default",
                "output": Path("fragment.lock"),
            },
            id="environment",
        ),
        pytest.param(
            {
                "sign": True,
                "platform": ["linux-64"],
                "output": Path("fragment.lock"),
            },
            id="platform",
        ),
        pytest.param(
            {
                "sign": True,
                "skip_unsolvable": True,
                "output": Path("fragment.lock"),
            },
            id="skip-unsolvable",
        ),
        pytest.param(
            {"sign": True, "output": Path("fragment.lock")},
            id="noncanonical-output",
        ),
    ],
)
def test_lock_rejects_invalid_signing_combinations(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
    options: dict[str, object],
) -> None:
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(CondaValueError, match="--attestation|--sign"):
        execute_lock(make_args(_DEFAULTS, **options))

    assert capture_generate_lockfile == []


def test_lock_sign_dry_run_validates_without_signing(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    sidecar = pixi_workspace / "conda.lock.sigstore.json"
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.sign_workspace_snapshot",
        lambda snapshot: pytest.fail("requested OIDC during dry-run"),
    )

    assert execute_lock(make_args(_DEFAULTS, sign=True, dry_run=True)) == 0
    assert not sidecar.exists()
    assert len(capture_generate_lockfile) == 1


@pytest.mark.parametrize(
    "unsafe_output",
    ["manifest", "lockfile", "hardlink", "symlink"],
    ids=["manifest", "lockfile", "hardlink", "symlink"],
)
def test_lock_sign_dry_run_rejects_unsafe_attestation_output(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
    unsafe_output: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    manifest = pixi_workspace / "pixi.toml"
    lockfile = pixi_workspace / "conda.lock"
    sidecar = pixi_workspace / "bundle.sigstore.json"
    if unsafe_output == "manifest":
        sidecar = manifest
    elif unsafe_output == "lockfile":
        sidecar = lockfile
    elif unsafe_output == "hardlink":
        lockfile.write_text("previous lock", encoding="utf-8")
        sidecar.hardlink_to(lockfile)
    else:
        sidecar.symlink_to(pixi_workspace / "elsewhere.json")

    with pytest.raises(AttestationError, match="Attestation output"):
        execute_lock(
            make_args(
                _DEFAULTS,
                sign=True,
                attestation=sidecar,
                dry_run=True,
            )
        )

    assert capture_generate_lockfile == []


@pytest.mark.parametrize(
    ("failure", "message", "expected_sidecar"),
    [
        pytest.param(
            "sign",
            "signing failed",
            "previous bundle\n",
            id="signing",
        ),
        pytest.param(
            "publish",
            "changed before publication",
            "concurrent bundle\n",
            id="publication",
        ),
    ],
)
@pytest.mark.parametrize(
    "previous_lock",
    ["previous lock\n", None],
    ids=["existing-lock", "missing-lock"],
)
def test_lock_sign_failure_restores_previous_lock_and_preserves_sidecar(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    message: str,
    expected_sidecar: str,
    previous_lock: str | None,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    lockfile = pixi_workspace / "conda.lock"
    sidecar = pixi_workspace / "conda.lock.sigstore.json"
    if previous_lock is not None:
        lockfile.write_text(previous_lock, encoding="utf-8")
    sidecar.write_text("previous bundle\n", encoding="utf-8")

    def publish(*args, publish_lockfile=None, **kwargs) -> None:
        assert publish_lockfile is not None
        publish_lockfile(_RENDERED_LOCK)

    def sign(snapshot) -> str:
        assert snapshot.lockfile_bytes == _RENDERED_LOCK.encode("utf-8")
        if failure == "sign":
            raise AttestationError("signing failed")
        sidecar.write_text("concurrent bundle\n", encoding="utf-8")
        return '{"bundle":true}'

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.generate_lockfile",
        publish,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.sign_workspace_snapshot",
        sign,
    )

    with pytest.raises(AttestationError, match=message):
        execute_lock(make_args(_DEFAULTS, sign=True))

    if previous_lock is None:
        assert not lockfile.exists()
    else:
        assert lockfile.read_text(encoding="utf-8") == previous_lock
    assert sidecar.read_text(encoding="utf-8") == expected_sidecar


@pytest.mark.parametrize(
    "previous_lock",
    ["previous lock\n", None],
    ids=["existing-lock", "missing-lock"],
)
@pytest.mark.parametrize(
    "previous_sidecar",
    ["previous bundle\n", None],
    ids=["existing-sidecar", "missing-sidecar"],
)
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("input-change", "manifest changed"),
        ("writer-after-publication", "changed before publication"),
    ],
    ids=["input-change", "writer-after-publication"],
)
def test_lock_sign_restores_outputs_after_sidecar_publication_failure(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_lock: str | None,
    previous_sidecar: str | None,
    failure: str,
    message: str,
    fail_attestation_writer_after_publication: Callable[[], None],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    manifest = pixi_workspace / "pixi.toml"
    lockfile = pixi_workspace / "conda.lock"
    sidecar = pixi_workspace / "conda.lock.sigstore.json"
    if previous_lock is not None:
        lockfile.write_text(previous_lock, encoding="utf-8")
    if previous_sidecar is not None:
        sidecar.write_text(previous_sidecar, encoding="utf-8")

    def publish(*args, publish_lockfile=None, **kwargs) -> None:
        assert publish_lockfile is not None
        publish_lockfile(_RENDERED_LOCK)

    original_write = AttestationOutput.write

    def write_then_change_manifest(
        output: AttestationOutput,
        bundle_json: str,
    ) -> Path:
        written = original_write(output, bundle_json)
        manifest.write_text("[workspace]\nname = 'concurrent'\n", encoding="utf-8")
        return written

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.generate_lockfile",
        publish,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.sign_workspace_snapshot",
        lambda snapshot: '{"bundle":true}',
    )
    if failure == "input-change":
        monkeypatch.setattr(AttestationOutput, "write", write_then_change_manifest)
    else:
        fail_attestation_writer_after_publication()

    with pytest.raises(CondaWorkspacesError, match=message):
        execute_lock(make_args(_DEFAULTS, sign=True))

    if previous_lock is None:
        assert not lockfile.exists()
    else:
        assert lockfile.read_text(encoding="utf-8") == previous_lock
    if previous_sidecar is None:
        assert not sidecar.exists()
    else:
        assert sidecar.read_text(encoding="utf-8") == previous_sidecar


@pytest.mark.parametrize(
    ("partial", "dry_run"),
    [
        ({"environment": "default"}, False),
        ({"platform": ["linux-64"]}, False),
        ({"skip_unsolvable": True}, False),
        ({"environment": "default"}, True),
    ],
    ids=["environment", "platform", "skip-unsolvable", "dry-run"],
)
def test_lock_partial_operations_require_output(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
    capture_generate_lockfile: list[dict],
    partial: dict,
    dry_run: bool,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    before = snapshot_tree(pixi_workspace)

    with pytest.raises(CondaValueError, match="--output is required"):
        execute_lock(make_args(_DEFAULTS, dry_run=dry_run, **partial))

    assert capture_generate_lockfile == []
    assert snapshot_tree(pixi_workspace) == before


def test_lock_unknown_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(EnvironmentNotFoundError):
        execute_lock(
            make_args(
                _DEFAULTS,
                environment="nonexistent",
                output=pixi_workspace / "conda.lock.nonexistent",
            )
        )


def test_lock_forwards_platform_flag(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
) -> None:
    """Repeated ``--platform`` values reach ``generate_lockfile`` as a tuple."""
    monkeypatch.chdir(pixi_workspace)

    result = execute_lock(
        make_args(
            _DEFAULTS,
            platform=["linux-64", "osx-arm64"],
            output=pixi_workspace / "conda.lock.selected-platforms",
        )
    )
    assert result == 0
    assert capture_generate_lockfile[0]["platforms"] == ("linux-64", "osx-arm64")


def test_lock_accepts_feature_only_platform(
    broadened_platform_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
) -> None:
    monkeypatch.chdir(broadened_platform_workspace)

    result = execute_lock(
        make_args(
            _DEFAULTS,
            platform=["win-64"],
            output=broadened_platform_workspace / "conda.lock.win-64",
        )
    )

    assert result == 0
    assert set(capture_generate_lockfile[0]["resolved_envs"]) == {"default", "windows"}
    assert capture_generate_lockfile[0]["platforms"] == ("win-64",)


def test_lock_rejects_platform_unsupported_by_selected_environment(
    broadened_platform_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
) -> None:
    monkeypatch.chdir(broadened_platform_workspace)

    with pytest.raises(PlatformError, match="linux-64"):
        execute_lock(
            make_args(
                _DEFAULTS,
                environment="windows",
                platform=["linux-64"],
                output=broadened_platform_workspace / "conda.lock.linux-64",
            )
        )

    assert capture_generate_lockfile == []


def test_lock_rejects_undeclared_platform(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``--platform`` value absent from the manifest raises ``PlatformError``."""
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(PlatformError, match="freebsd-64"):
        execute_lock(
            make_args(
                _DEFAULTS,
                platform=["freebsd-64"],
                output=pixi_workspace / "conda.lock.freebsd-64",
            )
        )


@pytest.mark.parametrize(
    ("flag_value", "expects_on_skip"),
    [
        (False, False),
        (True, True),
    ],
    ids=["default-off-fail-fast", "flag-on-skip-mode"],
)
def test_lock_forwards_skip_unsolvable(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
    flag_value: bool,
    expects_on_skip: bool,
) -> None:
    """``--skip-unsolvable`` wires ``skip_unsolvable`` and an on_skip callback.

    With the flag off (the default), the CLI must leave ``on_skip`` as
    ``None`` so ``generate_lockfile`` falls back to fail-fast.
    """
    monkeypatch.chdir(pixi_workspace)
    output_path = pixi_workspace / "conda.lock.solvable" if flag_value else None

    result = execute_lock(
        make_args(
            _DEFAULTS,
            skip_unsolvable=flag_value,
            output=output_path,
        )
    )
    assert result == 0
    assert capture_generate_lockfile[0]["skip_unsolvable"] is flag_value
    if expects_on_skip:
        assert callable(capture_generate_lockfile[0]["on_skip"])
    else:
        assert capture_generate_lockfile[0]["on_skip"] is None


def test_lock_forwards_output_path(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
) -> None:
    """``--output`` threads through to ``generate_lockfile(output_path=...)``."""
    monkeypatch.chdir(pixi_workspace)
    target = pixi_workspace / "conda.lock.linux-64"

    result = execute_lock(make_args(_DEFAULTS, output=target))
    assert result == 0
    assert capture_generate_lockfile[0]["output_path"] == target


def test_lock_rejects_manifest_changed_during_render(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    manifest = pixi_workspace / "pixi.toml"
    lockfile = pixi_workspace / "conda.lock"

    def render_and_change_manifest(*args: object, **kwargs: object) -> str:
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + "\n# concurrent change\n",
            encoding="utf-8",
        )
        return "version: 1\nenvironments: {}\npackages: []\n"

    monkeypatch.setattr(
        "conda_workspaces.lockfile.render_lockfile",
        render_and_change_manifest,
    )

    with pytest.raises(CondaWorkspacesError, match="manifest changed"):
        execute_lock(make_args(_DEFAULTS))

    assert not lockfile.exists()


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
def test_lock_rejects_manifest_as_output(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
    dry_run: bool,
) -> None:
    monkeypatch.chdir(pixi_workspace)
    manifest = pixi_workspace / "pixi.toml"
    before = snapshot_tree(pixi_workspace)

    with pytest.raises(ValueError, match="cannot overwrite"):
        execute_lock(
            make_args(
                _DEFAULTS,
                output=manifest,
                dry_run=dry_run,
            )
        )

    assert snapshot_tree(pixi_workspace) == before


@pytest.mark.parametrize(
    ("dry_run", "output_fragment"),
    [(False, "Updated"), (True, "Would update")],
    ids=["write", "dry-run"],
)
def test_lock_merge_dispatches_to_merge_lockfiles(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    capture_generate_lockfile: list[dict],
    dry_run: bool,
    output_fragment: str,
) -> None:
    """``--merge`` bypasses the solver and calls ``merge_lockfiles``."""
    monkeypatch.chdir(pixi_workspace)
    frag1 = pixi_workspace / "conda.lock.linux-64"
    frag2 = pixi_workspace / "conda.lock.osx-arm64"
    frag1.write_text("placeholder", encoding="utf-8")
    frag2.write_text("placeholder", encoding="utf-8")

    seen_paths: list[list] = []

    def fake_merge(paths, ctx, *, dry_run=False, publish_lockfile=None):
        seen_paths.append(list(paths))
        assert dry_run is expected_dry_run
        assert callable(publish_lockfile) is not dry_run
        return pixi_workspace / "conda.lock"

    expected_dry_run = dry_run
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.merge_lockfiles", fake_merge
    )

    result = execute_lock(
        make_args(
            _DEFAULTS,
            merge=[str(frag1), str(frag2)],
            dry_run=dry_run,
        ),
    )
    assert result == 0
    assert capture_generate_lockfile == []
    assert len(seen_paths) == 1
    resolved = {p.resolve() for p in seen_paths[0]}
    assert resolved == {frag1.resolve(), frag2.resolve()}
    assert output_fragment in capsys.readouterr().out


def test_lock_merge_glob_expansion(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--merge 'conda.lock.*'`` globs fragments relative to cwd."""
    monkeypatch.chdir(pixi_workspace)
    (pixi_workspace / "conda.lock.linux-64").write_text("x", encoding="utf-8")
    (pixi_workspace / "conda.lock.osx-arm64").write_text("x", encoding="utf-8")

    seen_paths: list[list] = []

    def fake_merge(paths, ctx, *, dry_run=False, publish_lockfile=None):
        seen_paths.append(list(paths))
        assert dry_run is False
        assert callable(publish_lockfile)
        return pixi_workspace / "conda.lock"

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.merge_lockfiles", fake_merge
    )

    assert execute_lock(make_args(_DEFAULTS, merge=["conda.lock.*"])) == 0
    assert len(seen_paths) == 1
    names = sorted(p.name for p in seen_paths[0])
    assert names == ["conda.lock.linux-64", "conda.lock.osx-arm64"]


@pytest.mark.parametrize(
    "incompatible",
    [
        {"environment": "default"},
        {"platform": ["linux-64"]},
        {"skip_unsolvable": True},
        {"output": "conda.lock.linux-64"},
    ],
    ids=["with-environment", "with-platform", "with-skip-unsolvable", "with-output"],
)
def test_lock_merge_rejects_incompatible_flags(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    incompatible: dict,
) -> None:
    """``--merge`` is mutually exclusive with solver-side flags."""
    monkeypatch.chdir(pixi_workspace)
    frag = pixi_workspace / "conda.lock.linux-64"
    frag.write_text("placeholder", encoding="utf-8")
    if "output" in incompatible:
        incompatible = {"output": Path(str(incompatible["output"]))}

    with pytest.raises(CondaValueError, match="--merge"):
        execute_lock(
            make_args(_DEFAULTS, merge=[str(frag)], **incompatible),
        )


def test_lock_merge_no_matches_raises(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty glob match is a user error, not a silent no-op."""
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(CondaValueError, match="matched no files"):
        execute_lock(make_args(_DEFAULTS, merge=["conda.lock.missing.*"]))


def test_lock_rejects_hardlink_output_alias(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
) -> None:
    monkeypatch.chdir(pixi_workspace)
    canonical = pixi_workspace / "conda.lock"
    canonical.write_text("original", encoding="utf-8")
    alias = pixi_workspace / "lock-alias"
    alias.hardlink_to(canonical)

    with pytest.raises(CondaValueError, match="hardlink alias"):
        execute_lock(make_args(_DEFAULTS, output=alias))

    assert canonical.read_text(encoding="utf-8") == "original"
    assert alias.read_text(encoding="utf-8") == "original"
    assert capture_generate_lockfile == []


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
@pytest.mark.parametrize("boundary", ["leaf", "parent"], ids=["leaf", "parent"])
def test_lock_rejects_symlinked_manifest(
    pixi_workspace: Path,
    capture_generate_lockfile: list[dict],
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
        execute_lock(
            make_args(
                _DEFAULTS,
                manifest_file=linked_manifest,
                dry_run=dry_run,
            )
        )

    assert linked_boundary.is_symlink()
    assert capture_generate_lockfile == []


def test_lock_progress_does_not_emit_terminal_controls(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
) -> None:
    payload = "spoof\x1b[2J\x1b]8;;https://example.invalid\x1b\\"
    output_path = pixi_workspace / payload
    console = Console(file=StringIO(), force_terminal=True, color_system=None)
    monkeypatch.chdir(pixi_workspace)

    result = execute_lock(
        make_args(
            _DEFAULTS,
            output=output_path,
            skip_unsolvable=True,
        ),
        console=console,
    )
    capture_generate_lockfile[0]["progress"](payload, payload)
    capture_generate_lockfile[0]["on_skip"](
        payload,
        payload,
        SimpleNamespace(reason=payload),
    )

    assert result == 0
    output = console.file.getvalue()
    assert "\x1b[2J" not in output
    assert "\x1b]8;;https://example.invalid" not in output
    assert r"\x1b[2J" in output
