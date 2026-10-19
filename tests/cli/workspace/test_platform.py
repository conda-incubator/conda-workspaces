"""Tests for editing existing feature platform declarations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import tomlkit
from conda.base.context import context, reset_context

from conda_workspaces.cli.main import execute_workspace, generate_workspace_parser
from conda_workspaces.cli.workspace.platform import execute_platform
from conda_workspaces.exceptions import CondaWorkspacesError, PlatformError, SolveError
from conda_workspaces.lockfile import (
    CondaLockLoader,
    load_lockfile_data,
)
from conda_workspaces.manifests import find_parser
from conda_workspaces.resolver import ResolvedEnvironment, resolve_environment

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

    from conda.models.records import PackageRecord

    from conda_workspaces.models import LockfileStatus, WorkspaceConfig

_DEFAULTS = {
    "manifest_file": None,
    "platform_action": "add",
    "feature": "tools",
    "platforms": ["win-64"],
    "no_lockfile_update": True,
    "dry_run": False,
}


@pytest.fixture
def platform_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "conda.toml"
    path.write_text(
        "# Project comment\n"
        '[workspace]\nname = "platforms"\nchannels = []\n'
        'platforms = ["linux-64", "win-64"]\n\n'
        "[feature.tools]\n"
        'platforms = ["linux-64", "osx-arm64"] # Platform comment\n'
        '[feature.other]\nplatforms = ["win-64"]\n\n'
        "[environments]\n"
        'tools = { features = ["tools"], no-default-feature = true }\n'
        'shared = { features = ["tools"], no-default-feature = true }\n'
        'other = { features = ["other"], no-default-feature = true }\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def platform_solves(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def solve(
        self: ResolvedEnvironment, platform: str, *, prefix: Path
    ) -> list[PackageRecord]:
        calls.append((self.name, platform))
        return []

    monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", solve)
    return calls


@pytest.mark.parametrize("feature", [None, "tools"], ids=["workspace", "feature"])
@pytest.mark.parametrize("action", ["add", "remove"])
@pytest.mark.parametrize(
    ("filename", "namespace", "workspace", "split_namespace"),
    [
        ("conda.toml", "", "workspace", False),
        ("pixi.toml", "", "workspace", False),
        ("pixi.toml", "", "project", False),
        ("pyproject.toml", "tool.conda.", "workspace", False),
        ("pyproject.toml", "tool.pixi.", "workspace", False),
        ("pyproject.toml", "tool.conda.", "workspace", True),
        ("pyproject.toml", "tool.pixi.", "workspace", True),
    ],
    ids=[
        "conda",
        "pixi",
        "pixi-legacy",
        "pyproject-conda",
        "pyproject-pixi",
        "pyproject-conda-split",
        "pyproject-pixi-split",
    ],
)
def test_platform_edit_preserves_selected_manifest_content(
    tmp_path: Path,
    platform_manifest: Path,
    action: str,
    feature: str | None,
    filename: str,
    namespace: str,
    workspace: str,
    split_namespace: bool,
    platform_solves: list[tuple[str, str]],
) -> None:
    original = platform_manifest.read_text(encoding="utf-8")
    content = original.replace("[workspace]", f"[{namespace}{workspace}]")
    for table in ("feature.tools", "feature.other", "environments"):
        content = content.replace(f"[{table}]", f"[{namespace}{table}]")
    if split_namespace:
        content = content.replace(
            f"[{namespace}feature.tools]",
            "[tool.ruff]\nline-length = 88 # Ruff comment\n\n"
            f"[{namespace}feature.tools]",
        )
    if namespace:
        content += '\n[project]\nname = "packaging" # Packaging comment\n'
    selected = tmp_path / "selected" / filename
    selected.parent.mkdir()
    selected.write_text(content, encoding="utf-8")
    args = make_args(
        _DEFAULTS,
        manifest_file=selected,
        platform_action=action,
        feature=feature,
        platforms=(["win-64"] if feature else ["osx-arm64"])
        if action == "add"
        else ["linux-64"],
    )

    assert execute_platform(args) == 0

    updated = selected.read_text(encoding="utf-8")
    config = find_parser(selected).parse(selected)
    if feature:
        assert config.features["tools"].platforms == (
            ["linux-64", "osx-arm64", "win-64"] if action == "add" else ["osx-arm64"]
        )
        assert config.platforms == ["linux-64", "win-64"]
    else:
        assert config.platforms == (
            ["linux-64", "win-64", "osx-arm64"] if action == "add" else ["win-64"]
        )
        assert config.features["tools"].platforms == ["linux-64", "osx-arm64"]
    assert config.features["other"].platforms == ["win-64"]
    assert "# Project comment" in updated
    assert "# Platform comment" in updated
    if namespace:
        assert 'name = "packaging" # Packaging comment' in updated
    if split_namespace:
        assert "line-length = 88 # Ruff comment" in updated
    assert platform_manifest.read_text(encoding="utf-8") == original
    assert platform_solves == []


@pytest.mark.parametrize(
    ("options", "message", "suggestion"),
    [
        ({"feature": "missing"}, "not defined", None),
        ({"feature": "default"}, "Omit --feature", None),
        ({"platforms": ["linxu-64"]}, "Unknown platform", "linux-64"),
        ({"platforms": ["unrelated"]}, "Unknown platform", None),
        ({"platforms": ["noarch"]}, "noarch", None),
        ({"feature": None, "platforms": ["noarch"]}, "noarch", None),
        ({"feature": None, "platforms": ["linxu-64"]}, "Unknown platform", "linux-64"),
        (
            {"platform_action": "remove", "platforms": ["win-64"]},
            "not explicitly declared",
            None,
        ),
        (
            {"platforms": ["win-64", "linxu-64"]},
            "Unknown platform",
            "linux-64",
        ),
    ],
    ids=[
        "missing-feature",
        "default",
        "typo",
        "unknown",
        "feature-noarch",
        "workspace-noarch",
        "workspace-typo",
        "absent",
        "mixed-validity",
    ],
)
def test_platform_invalid_selection_writes_nothing(
    platform_manifest: Path,
    options: dict[str, object],
    message: str,
    suggestion: str | None,
) -> None:
    original = platform_manifest.read_bytes()
    with pytest.raises(CondaWorkspacesError, match=message) as exc:
        execute_platform(
            make_args(_DEFAULTS, manifest_file=platform_manifest, **options)
        )
    if suggestion:
        assert isinstance(exc.value, PlatformError)
        assert f"Did you mean '{suggestion}'?" in exc.value.hints
    else:
        assert not any("Did you mean" in hint for hint in exc.value.hints)
    assert platform_manifest.read_bytes() == original
    assert not (platform_manifest.parent / ".conda").exists()


@pytest.mark.parametrize(
    "declaration",
    ["linux-64", 1, ["linux-64", 1]],
    ids=["string", "integer", "mixed-list"],
)
def test_platform_rejects_malformed_explicit_list(
    platform_manifest: Path, declaration: str | int | list[str | int]
) -> None:
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    document["feature"]["tools"]["platforms"] = declaration
    platform_manifest.write_text(tomlkit.dumps(document), encoding="utf-8")
    original = platform_manifest.read_bytes()
    with pytest.raises(CondaWorkspacesError, match="must be a list of platform names"):
        execute_platform(make_args(_DEFAULTS, manifest_file=platform_manifest))
    assert platform_manifest.read_bytes() == original
    assert not (platform_manifest.parent / ".conda").exists()


@pytest.mark.parametrize("feature", [None, "tools"], ids=["workspace", "feature"])
@pytest.mark.parametrize("declaration", [None, []], ids=["missing", "empty"])
def test_platform_add_starts_an_explicit_list(
    platform_manifest: Path, declaration: list[str] | None, feature: str | None
) -> None:
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    selected = document["feature"][feature] if feature else document["workspace"]
    if declaration is None:
        del selected["platforms"]
    else:
        selected["platforms"] = declaration
    platform_manifest.write_text(tomlkit.dumps(document), encoding="utf-8")
    assert (
        execute_platform(
            make_args(_DEFAULTS, manifest_file=platform_manifest, feature=feature)
        )
        == 0
    )
    config = find_parser(platform_manifest).parse(platform_manifest)
    assert (config.features[feature].platforms if feature else config.platforms) == [
        "win-64"
    ]


@pytest.mark.parametrize("duplicate", [False, True], ids=["unique", "duplicate"])
def test_platform_remove_last_restores_shared_environment_inheritance(
    platform_manifest: Path, capsys: pytest.CaptureFixture[str], duplicate: bool
) -> None:
    if duplicate:
        content = platform_manifest.read_text(encoding="utf-8")
        platform_manifest.write_text(
            content.replace(
                '["linux-64", "osx-arm64"]',
                '["linux-64", "osx-arm64", "linux-64"]',
            ),
            encoding="utf-8",
        )
    args = make_args(
        _DEFAULTS,
        manifest_file=platform_manifest,
        platform_action="remove",
        platforms=["linux-64", "osx-arm64"],
    )
    assert execute_platform(args) == 0
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    assert "platforms" not in document["feature"]["tools"]
    config = find_parser(platform_manifest).parse(platform_manifest)
    for name in ("tools", "shared"):
        assert resolve_environment(config, name).platforms == ["linux-64", "win-64"]
    output = capsys.readouterr().out
    assert "inherit" in output
    assert "shared" in output and "tools" in output


def test_platform_duplicate_add_does_not_publish_or_solve(
    platform_manifest: Path, platform_solves: list[tuple[str, str]]
) -> None:
    original = platform_manifest.read_bytes()
    assert (
        execute_platform(
            make_args(
                _DEFAULTS,
                manifest_file=platform_manifest,
                platforms=["linux-64", "linux-64"],
                no_lockfile_update=False,
            )
        )
        == 0
    )
    assert platform_manifest.read_bytes() == original
    assert platform_solves == []
    assert not (platform_manifest.parent / ".conda").exists()


@pytest.mark.parametrize(
    ("platform", "accepted"),
    [("linux-cuda", True), ("linxu-cuda", False)],
    ids=["declared-custom", "custom-typo"],
)
def test_platform_declared_custom_names_and_suggestions(
    platform_manifest: Path, platform: str, accepted: bool
) -> None:
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    document["workspace"]["platforms"] = [
        "linux-64",
        {"name": "linux-cuda", "platform": "linux-64"},
    ]
    platform_manifest.write_text(tomlkit.dumps(document), encoding="utf-8")
    args = make_args(_DEFAULTS, manifest_file=platform_manifest, platforms=[platform])
    original = platform_manifest.read_bytes()
    if accepted:
        assert execute_platform(args) == 0
        config = find_parser(platform_manifest).parse(platform_manifest)
        assert "linux-cuda" in config.features["tools"].platforms
        assert config.platform_subdir("linux-cuda") == "linux-64"
    else:
        with pytest.raises(PlatformError) as exc:
            execute_platform(args)
        assert "Did you mean 'linux-cuda'?" in exc.value.hints
        assert platform_manifest.read_bytes() == original


def test_platform_console_escapes_feature_names(
    platform_manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "[red]tools\x1b[2J"
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    document["feature"][name] = {"platforms": ["linux-64"]}
    platform_manifest.write_text(tomlkit.dumps(document), encoding="utf-8")
    assert (
        execute_platform(
            make_args(_DEFAULTS, manifest_file=platform_manifest, feature=name)
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "[red]tools" in output
    assert "\x1b" not in output


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "preview"])
def test_platform_refresh_preserves_installed_prefixes(
    platform_manifest: Path,
    platform_solves: list[tuple[str, str]],
    dry_run: bool,
) -> None:
    prefix = platform_manifest.parent / ".conda" / "envs" / "tools"
    metadata = prefix / "conda-meta"
    metadata.mkdir(parents=True)
    state = metadata / "state"
    state.write_text('{"subdir": "osx-arm64"}', encoding="utf-8")
    original = platform_manifest.read_bytes()
    args = make_args(
        _DEFAULTS,
        manifest_file=platform_manifest,
        no_lockfile_update=False,
        dry_run=dry_run,
    )
    assert execute_platform(args) == 0
    assert state.read_text(encoding="utf-8") == '{"subdir": "osx-arm64"}'
    assert sorted(item.name for item in metadata.iterdir()) == ["state"]
    assert (platform_manifest.read_bytes() == original) is dry_run
    assert (platform_manifest.parent / "conda.lock").exists() is not dry_run
    assert ("tools", "win-64") in platform_solves


@pytest.mark.parametrize("no_lockfile_update", [False, True], ids=["solve", "manifest"])
def test_platform_dry_run_creates_no_workspace_state(
    platform_manifest: Path,
    platform_solves: list[tuple[str, str]],
    no_lockfile_update: bool,
) -> None:
    original = platform_manifest.read_bytes()
    assert (
        execute_platform(
            make_args(
                _DEFAULTS,
                manifest_file=platform_manifest,
                no_lockfile_update=no_lockfile_update,
                dry_run=True,
            )
        )
        == 0
    )
    assert platform_manifest.read_bytes() == original
    assert not (platform_manifest.parent / ".conda").exists()
    assert not (platform_manifest.parent / "conda.lock").exists()
    assert bool(platform_solves) is not no_lockfile_update


def test_platform_solve_failure_preserves_manifest(
    platform_manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(self: ResolvedEnvironment, platform: str, *, prefix: Path) -> None:
        raise SolveError(self.name, "Unavailable packages", platform=platform)

    monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", fail)
    original = platform_manifest.read_bytes()
    with pytest.raises(SolveError):
        execute_platform(
            make_args(
                _DEFAULTS, manifest_file=platform_manifest, no_lockfile_update=False
            )
        )
    assert platform_manifest.read_bytes() == original
    assert not (platform_manifest.parent / "conda.lock").exists()


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "preview"])
def test_platform_rejects_broken_lockfile_symlink(
    platform_manifest: Path, platform_solves: list[tuple[str, str]], dry_run: bool
) -> None:
    lock_path = platform_manifest.parent / "conda.lock"
    lock_path.symlink_to("missing-lock")
    original = platform_manifest.read_bytes()
    with pytest.raises((CondaWorkspacesError, ValueError)):
        execute_platform(
            make_args(
                _DEFAULTS,
                manifest_file=platform_manifest,
                no_lockfile_update=False,
                dry_run=dry_run,
            )
        )
    assert platform_manifest.read_bytes() == original
    assert lock_path.is_symlink()
    assert platform_solves == []


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "preview"])
@pytest.mark.parametrize(
    "content",
    [
        "version: 1\nenvironments: []\n",
        "version: 1\nenvironments:\n  tools: []\n",
        "version: 1\nenvironments:\n  tools:\n    packages: []\n",
        "version: 1\nenvironments:\n  tools:\n    packages: {}\n    channels: 1\n",
    ],
    ids=["environments", "environment", "packages", "channels"],
)
def test_platform_malformed_lockfile_writes_nothing(
    platform_manifest: Path,
    platform_solves: list[tuple[str, str]],
    dry_run: bool,
    content: str,
) -> None:
    lock_path = platform_manifest.parent / "conda.lock"
    lock_path.write_text(content, encoding="utf-8")
    original = platform_manifest.read_bytes()
    with pytest.raises(CondaWorkspacesError, match="lockfile is not current") as exc:
        execute_platform(
            make_args(
                _DEFAULTS,
                manifest_file=platform_manifest,
                no_lockfile_update=False,
                dry_run=dry_run,
            )
        )
    assert "lock' before editing" in exc.value.hints[-1]
    assert platform_manifest.read_bytes() == original
    assert lock_path.read_text(encoding="utf-8") == content
    assert platform_solves == []


@pytest.mark.parametrize(
    "arguments",
    [["add"], ["remove", "--feature", "tools"]],
    ids=["workspace-missing-platform", "feature-missing-platform"],
)
def test_platform_parser_requires_platform(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        generate_workspace_parser().parse_args(["platform", *arguments])


@pytest.mark.usefixtures("reset_conda_context")
@pytest.mark.parametrize("action", ["add", "remove"])
@pytest.mark.parametrize("feature", [None, "tools"], ids=["workspace", "feature"])
def test_platform_parser_dispatch_emits_one_json_result(
    platform_manifest: Path,
    action: str,
    capsys: pytest.CaptureFixture[str],
    feature: str | None,
) -> None:
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            str(platform_manifest),
            "platform",
            action,
            *(["--feature", feature] if feature else []),
            "--no-lockfile-update",
            "--json",
            "win-64" if action == "add" else "linux-64",
        ]
    )
    reset_context(argparse_args=args)
    assert execute_workspace(args) == 0
    assert json.loads(capsys.readouterr().out) == {"success": True}


def test_platform_existing_lock_is_preserved_and_stale_lock_rejected(
    platform_manifest: Path, platform_solves: list[tuple[str, str]]
) -> None:
    args = make_args(
        _DEFAULTS, manifest_file=platform_manifest, no_lockfile_update=False
    )
    assert execute_platform(args) == 0
    lock_path = platform_manifest.parent / "conda.lock"
    baseline = load_lockfile_data(lock_path.read_bytes())
    platform_solves.clear()
    args.platform_action = "remove"
    args.platforms = ["osx-arm64"]
    assert execute_platform(args) == 0
    updated = load_lockfile_data(lock_path.read_bytes())
    assert platform_solves == []
    assert updated["environments"]["other"] == baseline["environments"]["other"]
    assert "osx-arm64" not in updated["environments"]["tools"]["packages"]

    original = platform_manifest.read_text(encoding="utf-8")
    platform_manifest.write_text(
        original.replace(
            "[feature.tools]",
            '[feature.tools.dependencies]\npython = "*"\n\n[feature.tools]',
        ),
        encoding="utf-8",
    )
    before = platform_manifest.read_bytes()
    lock_before = lock_path.read_bytes()
    args.platform_action = "add"
    args.platforms = ["osx-arm64"]
    with pytest.raises(
        CondaWorkspacesError, match="lockfile.*current|current.*lockfile"
    ):
        execute_platform(args)
    assert platform_manifest.read_bytes() == before
    assert lock_path.read_bytes() == lock_before
    assert platform_solves == []


@pytest.mark.parametrize("feature", [None, "tools"], ids=["workspace", "feature"])
def test_platform_remove_allows_existing_noarch(
    platform_manifest: Path, feature: str | None
) -> None:
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    selected = document["feature"][feature] if feature else document["workspace"]
    selected["platforms"].append("noarch")
    platform_manifest.write_text(tomlkit.dumps(document), encoding="utf-8")
    assert (
        execute_platform(
            make_args(
                _DEFAULTS,
                manifest_file=platform_manifest,
                feature=feature,
                platform_action="remove",
                platforms=["noarch"],
            )
        )
        == 0
    )
    config = find_parser(platform_manifest).parse(platform_manifest)
    assert "noarch" not in (
        config.features[feature].platforms if feature else config.platforms
    )


@pytest.mark.parametrize("action", ["add", "remove"])
def test_workspace_platform_edits_preserve_rich_entries(
    platform_manifest: Path, action: str
) -> None:
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    document["workspace"]["platforms"] = [
        "win-64",
        {"name": "linux-cuda", "platform": "linux-64", "cuda": "12"},
    ]
    document["workspace"]["platforms"].multiline(True)
    original = tomlkit.dumps(document).replace(
        'cuda = "12"},', 'cuda = "12"}, # GPU target'
    )
    platform_manifest.write_text(original, encoding="utf-8")
    assert (
        execute_platform(
            make_args(
                _DEFAULTS,
                manifest_file=platform_manifest,
                feature=None,
                platform_action=action,
                platforms=["osx-arm64"] if action == "add" else ["win-64"],
            )
        )
        == 0
    )
    updated = platform_manifest.read_text(encoding="utf-8")
    config = find_parser(platform_manifest).parse(platform_manifest)
    assert config.platforms == (
        ["win-64", "linux-cuda", "osx-arm64"] if action == "add" else ["linux-cuda"]
    )
    assert config.platform_subdir("linux-cuda") == "linux-64"
    assert config.platform_system_requirements["linux-cuda"]["cuda"] == "12"
    assert 'name = "linux-cuda", platform = "linux-64", cuda = "12"' in updated
    assert "# GPU target" in updated


@pytest.mark.parametrize(
    ("entry", "platform", "rejected"),
    [
        (
            {"name": "linux-cuda", "platform": "linux-64", "cuda": "12"},
            "linux-cuda",
            True,
        ),
        ({"name": "linux-custom", "platform": "linux-64"}, "linux-custom", True),
        ({"name": "linux-64", "platform": "linux-64", "cuda": "12"}, "linux-64", True),
        ("linux-64", "linux-64", False),
    ],
    ids=["custom-requirements", "custom-name", "native-requirements", "plain"],
)
def test_workspace_platform_removal_preserves_feature_target_metadata(
    platform_manifest: Path, entry: str | dict[str, str], platform: str, rejected: bool
) -> None:
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    document["workspace"]["platforms"] = [entry, "win-64"]
    document["feature"]["tools"]["platforms"] = [platform]
    platform_manifest.write_text(tomlkit.dumps(document), encoding="utf-8")
    original = platform_manifest.read_bytes()
    args = make_args(
        _DEFAULTS,
        manifest_file=platform_manifest,
        feature=None,
        platform_action="remove",
        platforms=[platform],
    )
    if rejected:
        with pytest.raises(CondaWorkspacesError, match="tools"):
            execute_platform(args)
        assert platform_manifest.read_bytes() == original
    else:
        assert execute_platform(args) == 0
        config = find_parser(platform_manifest).parse(platform_manifest)
        assert config.platforms == ["win-64"]
        assert resolve_environment(config, "tools").platforms == ["linux-64"]


@pytest.mark.parametrize("feature", [None, "tools"], ids=["workspace", "feature"])
def test_platform_add_rejects_alias_for_noarch(
    platform_manifest: Path, feature: str | None
) -> None:
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    document["workspace"]["platforms"] = [{"name": "portable", "platform": "noarch"}]
    platform_manifest.write_text(tomlkit.dumps(document), encoding="utf-8")
    original = platform_manifest.read_bytes()
    with pytest.raises(CondaWorkspacesError, match="noarch"):
        execute_platform(
            make_args(
                _DEFAULTS,
                manifest_file=platform_manifest,
                feature=feature,
                platforms=["portable"],
            )
        )
    assert platform_manifest.read_bytes() == original


@pytest.mark.parametrize("action", ["add", "remove"])
def test_workspace_platform_lock_preserves_feature_solutions(
    platform_manifest: Path,
    platform_solves: list[tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    document["environments"]["inherited"] = {"features": [], "no-default-feature": True}
    platform_manifest.write_text(tomlkit.dumps(document), encoding="utf-8")
    args = make_args(
        _DEFAULTS,
        manifest_file=platform_manifest,
        feature=None,
        platforms=["osx-64"],
        no_lockfile_update=False,
    )
    assert execute_platform(args) == 0
    lock_path = platform_manifest.parent / "conda.lock"
    baseline = load_lockfile_data(lock_path.read_bytes())
    checked: list[tuple[int, tuple[tuple[str, str], ...], str]] = []
    original_check_targets = CondaLockLoader.check_targets

    def record_targets(
        loader: CondaLockLoader,
        config: WorkspaceConfig,
        targets: tuple[tuple[str, str], ...],
        *,
        match_budget_scope: str,
    ) -> LockfileStatus:
        pairs = tuple(targets)
        checked.append((id(loader), pairs, match_budget_scope))
        return original_check_targets(
            loader, config, pairs, match_budget_scope=match_budget_scope
        )

    monkeypatch.setattr(
        CondaLockLoader,
        "check_targets",
        record_targets,
    )
    platform_solves.clear()
    capsys.readouterr()
    args.platform_action = action
    args.platforms = ["osx-arm64"] if action == "add" else ["win-64"]
    assert execute_platform(args) == 0
    expected_checks = {
        (name, platform)
        for name, env_data in baseline["environments"].items()
        for platform in env_data["packages"]
    }
    assert len(checked) == 2
    assert set(checked[0][1]) == expected_checks
    assert len(checked[0][1]) == len(expected_checks)
    assert checked[0][2] == "target"
    assert checked[1][2] == "platform"
    updated = load_lockfile_data(lock_path.read_bytes())
    expected = (
        {"linux-64", "win-64", "osx-64", "osx-arm64"}
        if action == "add"
        else {"linux-64", "osx-64"}
    )
    for name in ("default", "inherited"):
        assert set(updated["environments"][name]["packages"]) == expected
    for name in ("tools", "shared", "other"):
        assert updated["environments"][name] == baseline["environments"][name]
    assert sorted(platform_solves) == (
        [("default", "osx-arm64"), ("inherited", "osx-arm64")]
        if action == "add"
        else []
    )
    output = capsys.readouterr().out
    assert "default" in output and "inherited" in output
    assert "tools" not in output and "shared" not in output and "other" not in output


def test_workspace_platform_remove_last_uses_native_target(
    platform_manifest: Path,
    platform_solves: list[tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        execute_platform(
            make_args(
                _DEFAULTS,
                manifest_file=platform_manifest,
                feature=None,
                platform_action="remove",
                platforms=["linux-64", "win-64"],
                no_lockfile_update=False,
            )
        )
        == 0
    )
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    assert document["workspace"]["platforms"] == []
    lock = load_lockfile_data((platform_manifest.parent / "conda.lock").read_bytes())
    assert list(lock["environments"]["default"]["packages"]) == [context.subdir]
    assert list(lock["environments"]["other"]["packages"]) == ["win-64"]
    assert "native" in capsys.readouterr().out


@pytest.mark.parametrize("action", ["add", "remove"])
def test_workspace_platform_array_of_tables(
    platform_manifest: Path,
    action: str,
) -> None:
    document = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    document["workspace"]["platforms"] = [{"platform": "linux-64", "cuda": "12"}]
    platform_manifest.write_text(tomlkit.dumps(document), encoding="utf-8")
    config = find_parser(platform_manifest).parse(platform_manifest)
    target = config.platforms[0]
    assert (
        execute_platform(
            make_args(
                _DEFAULTS,
                manifest_file=platform_manifest,
                feature=None,
                platform_action=action,
                platforms=["osx-arm64"] if action == "add" else [target],
            )
        )
        == 0
    )
    updated = tomlkit.parse(platform_manifest.read_text(encoding="utf-8"))
    config = find_parser(platform_manifest).parse(platform_manifest)
    if action == "add":
        assert config.platforms == [target, "osx-arm64"]
        assert config.platform_system_requirements[target]["cuda"] == "12"
    else:
        assert updated["workspace"]["platforms"] == []
        assert config.platforms == []
