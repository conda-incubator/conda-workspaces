"""Tests for conda_workspaces.cli.workspace.lock."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conda.exceptions import CondaValueError

from conda_workspaces.cli.workspace.lock import execute_lock
from conda_workspaces.exceptions import EnvironmentNotFoundError, PlatformError

from ..conftest import make_args

if TYPE_CHECKING:
    from tests.conftest import SnapshotTree

_DEFAULTS = {
    "manifest_file": None,
    "environment": None,
    "platform": None,
    "skip_unsolvable": False,
    "merge": None,
    "output": None,
    "dry_run": False,
}


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

    def fake_merge(paths, ctx, *, dry_run=False):
        seen_paths.append(list(paths))
        assert dry_run is expected_dry_run
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

    def fake_merge(paths, ctx, *, dry_run=False):
        seen_paths.append(list(paths))
        assert dry_run is False
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
