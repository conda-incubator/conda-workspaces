"""Tests for ``conda workspace update``."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import tomlkit

from conda_workspaces.cli.workspace.update import execute_update
from conda_workspaces.exceptions import (
    CondaWorkspacesError,
    EnvironmentNotInstalledError,
)
from conda_workspaces.models import LockfileStatus

from ..conftest import make_args

_DEFAULTS = {
    "manifest_file": None,
    "specs": [],
    "feature": None,
    "environment": None,
    "platform": None,
    "no_install": True,
    "dry_run": False,
}


@pytest.fixture
def update_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    """Replace prefix, solve, and lock boundaries with recording fakes."""
    runtime = SimpleNamespace(
        installed=set(),
        env_exists_calls=[],
        render_calls=[],
        sync_calls=[],
    )

    class FakeWorkspaceContext:
        platform = "linux-64"

        def __init__(self, config) -> None:
            self.config = config
            self.root = Path(config.root)

        def env_exists(self, name: str) -> bool:
            runtime.env_exists_calls.append(name)
            return name in runtime.installed

        def env_prefix(self, name: str) -> Path:
            return self.root / ".conda" / "envs" / name

    def fake_render(ctx, resolved, **kwargs) -> str:
        runtime.render_calls.append((ctx, resolved, kwargs))
        return "rendered-lock"

    def fake_load(content: bytes) -> dict[str, str]:
        source = "existing" if content == b"existing-lock" else "rendered"
        return {"source": source}

    def fake_sync(config, ctx, env_names, **kwargs) -> None:
        runtime.sync_calls.append(
            {
                "config": config,
                "ctx": ctx,
                "env_names": list(env_names),
                **kwargs,
            }
        )
        publisher = kwargs.get("publish_lockfile")
        if publisher is not None and not kwargs.get("dry_run", False):
            publisher("rendered-lock")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.update.WorkspaceContext",
        FakeWorkspaceContext,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.update.render_lockfile",
        fake_render,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.update.load_lockfile_data",
        fake_load,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.update.sync_environments",
        fake_sync,
    )
    return runtime


@pytest.fixture
def layered_manifest(tmp_path: Path) -> Path:
    """Create declarations at every supported ownership layer."""
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "layered-update"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[workspace.dependencies]
shared = ">=1,<2"

[dependencies]
defaultpkg = ">=1,<2"
shared = { workspace = true }

[target.linux-64.dependencies]
targetpkg = ">=1,<2"

[feature.dev.dependencies]
featurepkg = ">=1,<2"

[environments.default]
features = []

[environments.default.dependencies]
defaultlocal = ">=1,<2"

[environments.dev]
features = ["dev"]

[environments.private]
features = ["dev"]

[environments.private.dependencies]
envpkg = ">=1,<2"

[environments.isolated]
features = []
no-default-feature = true

[environments.isolated.dependencies]
isolatedpkg = ">=1,<2"
""",
        encoding="utf-8",
    )
    return path


def test_update_bare_name_preserves_manifest_bytes(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
) -> None:
    before = layered_manifest.read_bytes()

    assert (
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=["defaultpkg"],
            )
        )
        == 0
    )

    assert layered_manifest.read_bytes() == before
    assert update_runtime.sync_calls[0]["update_targets"] == {
        ("default", "linux-64"): {"defaultpkg"},
        ("default", "osx-arm64"): {"defaultpkg"},
        ("dev", "linux-64"): {"defaultpkg"},
        ("dev", "osx-arm64"): {"defaultpkg"},
        ("private", "linux-64"): {"defaultpkg"},
        ("private", "osx-arm64"): {"defaultpkg"},
    }


def test_update_explicit_spec_replaces_selected_constraint(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
) -> None:
    assert (
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=["defaultpkg>=2,<3"],
            )
        )
        == 0
    )

    document = tomlkit.loads(layered_manifest.read_text(encoding="utf-8"))
    assert document["dependencies"]["defaultpkg"] == ">=2,<3"
    assert (
        update_runtime.sync_calls[0]["config"]
        .features["default"]
        .conda_dependencies["defaultpkg"]
        .version
        == ">=2,<3"
    )


@pytest.mark.parametrize(
    ("filename", "namespace"),
    [
        ("conda.toml", ()),
        ("pixi.toml", ()),
        ("pyproject.toml", ("tool", "conda")),
        ("pyproject.toml", ("tool", "pixi")),
    ],
    ids=["conda-toml", "pixi-toml", "pyproject-conda", "pyproject-pixi"],
)
def test_update_explicit_spec_across_manifest_formats(
    tmp_path: Path,
    update_runtime: SimpleNamespace,
    filename: str,
    namespace: tuple[str, ...],
) -> None:
    path = tmp_path / filename
    prefix = ".".join(namespace)
    prefix = f"{prefix}." if prefix else ""
    path.write_text(
        f"""\
[{prefix}workspace]
name = "format-update"
channels = ["conda-forge"]
platforms = ["linux-64"]

[{prefix}dependencies]
boltons = ">=25,<27"
""",
        encoding="utf-8",
    )

    assert (
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=path,
                specs=["boltons>=26,<27"],
            )
        )
        == 0
    )

    document = tomlkit.loads(path.read_text(encoding="utf-8"))
    table = document
    for key in namespace:
        table = table[key]
    assert table["dependencies"]["boltons"] == ">=26,<27"
    assert update_runtime.sync_calls[0]["update_targets"] == {
        ("default", "linux-64"): {"boltons"}
    }


@pytest.mark.parametrize(
    ("spec", "selectors", "table_path", "expected_targets"),
    [
        (
            "defaultpkg>=2",
            {},
            ("dependencies",),
            {
                ("default", "linux-64"),
                ("default", "osx-arm64"),
                ("dev", "linux-64"),
                ("dev", "osx-arm64"),
                ("private", "linux-64"),
                ("private", "osx-arm64"),
            },
        ),
        (
            "featurepkg>=2",
            {"feature": "dev"},
            ("feature", "dev", "dependencies"),
            {
                ("dev", "linux-64"),
                ("dev", "osx-arm64"),
                ("private", "linux-64"),
                ("private", "osx-arm64"),
            },
        ),
        (
            "envpkg>=2",
            {"environment": "private"},
            ("environments", "private", "dependencies"),
            {
                ("private", "linux-64"),
                ("private", "osx-arm64"),
            },
        ),
        (
            "targetpkg>=2",
            {"platform": "linux-64"},
            ("target", "linux-64", "dependencies"),
            {
                ("default", "linux-64"),
                ("dev", "linux-64"),
                ("private", "linux-64"),
            },
        ),
        (
            "shared>=2",
            {},
            ("dependencies",),
            {
                ("default", "linux-64"),
                ("default", "osx-arm64"),
                ("dev", "linux-64"),
                ("dev", "osx-arm64"),
                ("private", "linux-64"),
                ("private", "osx-arm64"),
            },
        ),
    ],
    ids=["default", "feature", "environment", "target", "workspace-member"],
)
def test_update_targets_exact_effective_declaration_owners(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
    spec: str,
    selectors: dict[str, str],
    table_path: tuple[str, ...],
    expected_targets: set[tuple[str, str]],
) -> None:
    assert (
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=[spec],
                **selectors,
            )
        )
        == 0
    )

    name = spec.split(">", 1)[0]
    document = tomlkit.loads(layered_manifest.read_text(encoding="utf-8"))
    table = document
    for key in table_path:
        table = table[key]
    assert table[name] == ">=2"
    assert update_runtime.sync_calls[0]["update_targets"] == {
        target: {name} for target in expected_targets
    }
    if name == "shared":
        assert document["workspace"]["dependencies"]["shared"] == ">=1,<2"


def test_update_wrong_location_is_atomic_and_lists_declarations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "wrong-location"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
numpy = ">=2"

[target.linux-64.dependencies]
numpy = ">=2.1"

[feature.test.dependencies]
numpy = ">=2.2"

[feature.test.pypi-dependencies]
requests = ">=2"

[feature.test.target.linux-64.dependencies]
numpy = ">=2.3"

[environments.qa]
features = ["test"]

[environments.qa.dependencies]
numpy = ">=2.4"

[environments.qa.target.linux-64.dependencies]
numpy = ">=2.5"

[environments.other.dependencies]
click = ">=8"
""",
        encoding="utf-8",
    )
    before = path.read_bytes()

    with pytest.raises(CondaWorkspacesError) as exc_info:
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=path,
                specs=["click", "numpy", "requests"],
                environment="other",
            )
        )

    assert path.read_bytes() == before
    hints = exc_info.value.hints
    for command in (
        "update numpy",
        "update --platform linux-64 numpy",
        "update --feature test numpy",
        "update --feature test --platform linux-64 numpy",
        "update --environment qa numpy",
        "update --environment qa --platform linux-64 numpy",
    ):
        assert any(command in hint for hint in hints)
    assert any("PyPI dependency" in hint for hint in hints)
    assert all(f"--file {path}" in hint for hint in hints if hint.startswith("Run '"))


def test_update_rejects_declaration_shadowed_on_every_target(tmp_path: Path) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        """\
[workspace]
name = "shadowed"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
numpy = ">=1"

[target.linux-64.dependencies]
numpy = ">=2"

[target.osx-arm64.dependencies]
numpy = ">=2"
""",
        encoding="utf-8",
    )
    before = path.read_bytes()

    with pytest.raises(CondaWorkspacesError, match="overridden in every") as exc_info:
        execute_update(make_args(_DEFAULTS, manifest_file=path, specs=["numpy"]))

    assert path.read_bytes() == before
    assert "--feature" in exc_info.value.hints[0]
    assert "--environment" in exc_info.value.hints[0]
    assert "--platform" in exc_info.value.hints[0]


def test_update_requires_affected_installed_prefix_before_manifest_write(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
) -> None:
    before = layered_manifest.read_bytes()

    with pytest.raises(EnvironmentNotInstalledError):
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=["defaultlocal>=2"],
                environment="default",
                no_install=False,
            )
        )

    assert layered_manifest.read_bytes() == before
    assert update_runtime.sync_calls == []


def test_update_keeps_manifest_when_sync_fails_before_publication(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = layered_manifest.read_bytes()

    def fail_sync(*args, **kwargs):
        assert layered_manifest.read_bytes() == before
        raise RuntimeError("lock solve failed")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.update.sync_environments",
        fail_sync,
    )

    with pytest.raises(RuntimeError, match="lock solve failed"):
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=["featurepkg>=2"],
                feature="dev",
            )
        )

    assert layered_manifest.read_bytes() == before


def test_update_publishes_recoverable_state_before_prefix_failure(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update_runtime.installed.update({"dev", "private"})

    def fail_after_publish(config, ctx, env_names, **kwargs):
        kwargs["publish_lockfile"]("rendered-lock")
        raise RuntimeError("transaction failed")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.update.sync_environments",
        fail_after_publish,
    )

    with pytest.raises(
        CondaWorkspacesError,
        match="after publishing its desired state",
    ) as exc_info:
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=["featurepkg>=2"],
                feature="dev",
                no_install=False,
            )
        )

    document = tomlkit.loads(layered_manifest.read_text(encoding="utf-8"))
    assert document["feature"]["dev"]["dependencies"]["featurepkg"] == ">=2"
    assert layered_manifest.with_name("conda.lock").read_text(encoding="utf-8") == (
        "rendered-lock"
    )
    assert {hint.split("'")[1] for hint in exc_info.value.hints} == {
        f"conda workspace --file {layered_manifest} install -e dev",
        f"conda workspace --file {layered_manifest} install -e private",
    }


def test_update_no_install_skips_prefixes_and_refreshes_complete_lock(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
) -> None:
    assert (
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=["featurepkg"],
                feature="dev",
                no_install=True,
            )
        )
        == 0
    )

    assert update_runtime.env_exists_calls == []
    call = update_runtime.sync_calls[0]
    assert call["env_names"] == []
    assert call["no_install"] is True
    assert set(call["config"].environments) == {
        "default",
        "dev",
        "private",
        "isolated",
    }
    assert call["update_targets"] == {
        ("dev", "linux-64"): {"featurepkg"},
        ("dev", "osx-arm64"): {"featurepkg"},
        ("private", "linux-64"): {"featurepkg"},
        ("private", "osx-arm64"): {"featurepkg"},
    }


def test_update_no_install_reports_lock_recovery_after_publication(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.update.write_lockfile",
        fail_write,
    )

    with pytest.raises(
        CondaWorkspacesError,
        match="after publishing its desired state",
    ) as exc_info:
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=["featurepkg>=2"],
                feature="dev",
            )
        )

    document = tomlkit.loads(layered_manifest.read_text(encoding="utf-8"))
    assert document["feature"]["dev"]["dependencies"]["featurepkg"] == ">=2"
    assert exc_info.value.hints == [
        f"Run 'conda workspace --file {layered_manifest} lock' to refresh"
        " conda.lock from the published manifest."
    ]


def test_update_dry_run_uses_replacement_without_writing_manifest(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
) -> None:
    before = layered_manifest.read_bytes()

    assert (
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=["envpkg>=2"],
                environment="private",
                dry_run=True,
            )
        )
        == 0
    )

    assert layered_manifest.read_bytes() == before
    call = update_runtime.sync_calls[0]
    assert call["dry_run"] is True
    assert (
        str(call["config"].environments["private"].conda_dependencies["envpkg"].version)
        == ">=2"
    )
    assert update_runtime.render_calls[0][2]["dry_run"] is True


@pytest.mark.parametrize(
    ("mode", "expected_source", "render_count"),
    [
        ("complete", "existing", 0),
        ("stale", "rendered", 1),
        ("missing-root", "rendered", 1),
        ("missing-slice", "rendered", 1),
    ],
    ids=["complete", "stale", "missing-root", "missing-environment-slice"],
)
def test_update_selects_only_a_complete_current_lock_as_baseline(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_source: str,
    render_count: int,
) -> None:
    layered_manifest.with_name("conda.lock").write_bytes(b"existing-lock")
    checked_platforms: list[str] = []

    def fake_status(config, data, platform: str) -> LockfileStatus:
        checked_platforms.append(platform)
        status = (
            LockfileStatus.OUT_OF_DATE if mode == "stale" else LockfileStatus.UP_TO_DATE
        )
        return LockfileStatus(status=status)

    def fake_records(data, name: str, platform: str):
        if mode == "missing-slice" and name == "private" and platform == "osx-arm64":
            raise ValueError("slice missing")
        if mode == "missing-root" and name == "dev" and platform == "linux-64":
            return []
        return [SimpleNamespace(name="featurepkg")]

    existing = {
        "source": "existing",
        "environments": {
            name: {"packages": {"linux-64": [], "osx-arm64": []}}
            for name in ("default", "dev", "private", "isolated")
        },
    }

    def fake_load(content: bytes):
        if content == b"existing-lock":
            return existing
        return {"source": "rendered"}

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.update.check_lockfile_satisfiability",
        fake_status,
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.update.CondaLockLoader.package_records_for_env_data",
        staticmethod(fake_records),
    )
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.update.load_lockfile_data",
        fake_load,
    )

    assert (
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=["featurepkg"],
                feature="dev",
            )
        )
        == 0
    )

    assert set(checked_platforms) == {"linux-64", "osx-arm64"}
    assert len(update_runtime.render_calls) == render_count
    baseline = update_runtime.sync_calls[0]["baseline_lockfile"]
    assert baseline["source"] == expected_source


def test_update_passes_installed_environments_and_targets_to_sync(
    layered_manifest: Path,
    update_runtime: SimpleNamespace,
) -> None:
    update_runtime.installed.update({"dev", "private"})

    assert (
        execute_update(
            make_args(
                _DEFAULTS,
                manifest_file=layered_manifest,
                specs=["featurepkg"],
                feature="dev",
                no_install=False,
            )
        )
        == 0
    )

    call = update_runtime.sync_calls[0]
    assert call["env_names"] == ["dev", "private"]
    assert call["no_install"] is False
    assert call["update_targets"] == {
        ("dev", "linux-64"): {"featurepkg"},
        ("dev", "osx-arm64"): {"featurepkg"},
        ("private", "linux-64"): {"featurepkg"},
        ("private", "osx-arm64"): {"featurepkg"},
    }
