"""Tests for conda_workspaces.cli.workspace.lock."""

from __future__ import annotations

from pathlib import Path

import pytest
from conda.exceptions import CondaValueError

from conda_workspaces.cli.workspace.lock import execute_lock
from conda_workspaces.exceptions import EnvironmentNotFoundError, PlatformError

from ..conftest import make_args

_DEFAULTS = {
    "file": None,
    "environment": None,
    "platform": None,
    "skip_unsolvable": False,
    "merge": None,
    "output": None,
}


@pytest.fixture
def capture_generate_lockfile(monkeypatch: pytest.MonkeyPatch, pixi_workspace: Path):
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
        platforms=None,
        progress=None,
        skip_unsolvable=False,
        on_skip=None,
        output_path=None,
    ):
        calls.append(
            {
                "resolved_envs": resolved_envs,
                "platforms": platforms,
                "progress": progress,
                "skip_unsolvable": skip_unsolvable,
                "on_skip": on_skip,
                "output_path": output_path,
            }
        )
        return output_path or (pixi_workspace / "conda.lock")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.generate_lockfile", fake_generate
    )
    return calls


@pytest.mark.parametrize(
    "env_arg, expected_keys, output_fragment",
    [
        ("default", {"default"}, "Updated"),
        (None, {"default", "test"}, "Updated"),
    ],
    ids=["single-env", "all-envs"],
)
def test_lock_envs(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    capture_generate_lockfile: list[dict],
    env_arg: str | None,
    expected_keys: set[str],
    output_fragment: str,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    result = execute_lock(make_args(_DEFAULTS, environment=env_arg))
    assert result == 0
    assert len(capture_generate_lockfile) == 1
    assert set(capture_generate_lockfile[0]["resolved_envs"].keys()) == expected_keys
    assert capture_generate_lockfile[0]["platforms"] is None
    assert output_fragment in capsys.readouterr().out


def test_lock_unknown_env(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(EnvironmentNotFoundError):
        execute_lock(make_args(_DEFAULTS, environment="nonexistent"))


def test_lock_forwards_platform_flag(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
) -> None:
    """Repeated ``--platform`` values reach ``generate_lockfile`` as a tuple."""
    monkeypatch.chdir(pixi_workspace)

    result = execute_lock(make_args(_DEFAULTS, platform=["linux-64", "osx-arm64"]))
    assert result == 0
    assert capture_generate_lockfile[0]["platforms"] == ("linux-64", "osx-arm64")


def test_lock_rejects_undeclared_platform(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``--platform`` value absent from the manifest raises ``PlatformError``."""
    monkeypatch.chdir(pixi_workspace)

    with pytest.raises(PlatformError, match="freebsd-64"):
        execute_lock(make_args(_DEFAULTS, platform=["freebsd-64"]))


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

    result = execute_lock(make_args(_DEFAULTS, skip_unsolvable=flag_value))
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


def test_lock_merge_dispatches_to_merge_lockfiles(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_generate_lockfile: list[dict],
) -> None:
    """``--merge`` bypasses the solver and calls ``merge_lockfiles``."""
    monkeypatch.chdir(pixi_workspace)
    frag1 = pixi_workspace / "conda.lock.linux-64"
    frag2 = pixi_workspace / "conda.lock.osx-arm64"
    frag1.write_text("placeholder", encoding="utf-8")
    frag2.write_text("placeholder", encoding="utf-8")

    seen_paths: list[list] = []

    def fake_merge(paths, ctx):
        seen_paths.append(list(paths))
        return pixi_workspace / "conda.lock"

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.lock.merge_lockfiles", fake_merge
    )

    result = execute_lock(
        make_args(_DEFAULTS, merge=[str(frag1), str(frag2)]),
    )
    assert result == 0
    assert capture_generate_lockfile == []
    assert len(seen_paths) == 1
    resolved = {p.resolve() for p in seen_paths[0]}
    assert resolved == {frag1.resolve(), frag2.resolve()}


def test_lock_merge_glob_expansion(
    pixi_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--merge 'conda.lock.*'`` globs fragments relative to cwd."""
    monkeypatch.chdir(pixi_workspace)
    (pixi_workspace / "conda.lock.linux-64").write_text("x", encoding="utf-8")
    (pixi_workspace / "conda.lock.osx-arm64").write_text("x", encoding="utf-8")

    seen_paths: list[list] = []

    def fake_merge(paths, ctx):
        seen_paths.append(list(paths))
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
