"""Tests for conda_workspaces.lockfile."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from conda.base.context import context as conda_context
from conda.models.match_spec import MatchSpec
from conda_lockfiles.rattler_lock.v6 import _record_to_dict

from conda_workspaces.context import WorkspaceContext
from conda_workspaces.exceptions import LockfileNotFoundError
from conda_workspaces.lockfile import (
    ALIASES,
    DEFAULT_FILENAMES,
    FORMAT,
    LOCKFILE_NAME,
    LOCKFILE_VERSION,
    CondaLockLoader,
    _solve_for_records,
    generate_lockfile,
    install_from_lockfile,
    lockfile_path,
)
from conda_workspaces.models import Channel, Environment, Feature, WorkspaceConfig
from conda_workspaces.resolver import ResolvedEnvironment


@pytest.fixture
def lockfile_content() -> str:
    """A ``conda.lock`` body with one env, two platforms, a pypi entry."""
    return (
        "version: 1\n"
        "environments:\n"
        "  default:\n"
        "    channels:\n"
        "    - url: https://conda.anaconda.org/conda-forge\n"
        "    packages:\n"
        "      linux-64:\n"
        "      - conda: https://example.com/python-linux-64.conda\n"
        "      - pypi: https://pypi.org/simple/requests/\n"
        "      osx-arm64:\n"
        "      - conda: https://example.com/python-osx-arm64.conda\n"
        "packages:\n"
        "- conda: https://example.com/python-linux-64.conda\n"
        "  sha256: abc123\n"
        "- conda: https://example.com/python-osx-arm64.conda\n"
        "  sha256: def456\n"
        "- pypi: https://pypi.org/simple/requests/\n"
    )


@pytest.fixture
def lockfile_with_platforms(tmp_path: Path, lockfile_content: str) -> Path:
    """Write ``lockfile_content`` to tmp_path/conda.lock and return the path."""
    path = tmp_path / LOCKFILE_NAME
    path.write_text(lockfile_content, encoding="utf-8")
    return path


@pytest.fixture
def workspace_ctx_factory(tmp_path: Path) -> Callable[..., WorkspaceContext]:
    """Factory fixture that builds a ``WorkspaceContext`` rooted at tmp_path.

    Accepts ``platform`` (default ``"linux-64"``) and ``env_names``
    (default ``["default"]``) so each test can tweak just the bits it
    cares about.
    """

    def _factory(
        platform: str = "linux-64",
        env_names: list[str] | None = None,
    ) -> WorkspaceContext:
        if env_names is None:
            env_names = ["default"]
        config = WorkspaceConfig(
            name="lock-test",
            channels=[Channel("conda-forge")],
            platforms=[platform],
            features={"default": Feature(name="default")},
            environments={n: Environment(name=n) for n in env_names},
            root=str(tmp_path),
            manifest_path=str(tmp_path / "pixi.toml"),
        )
        ctx = WorkspaceContext(config)
        ctx._cache["platform"] = platform
        return ctx

    return _factory


def test_lockfile_path_returns_conda_lock(
    tmp_path: Path, workspace_ctx_factory: Callable[..., WorkspaceContext]
) -> None:
    ctx = workspace_ctx_factory()
    assert lockfile_path(ctx) == tmp_path / LOCKFILE_NAME


def test_plugin_metadata() -> None:
    """Plugin metadata is exposed as module-level ``Final`` constants."""
    assert FORMAT == "conda-workspaces-lock-v1"
    assert "conda-workspaces-lock" in ALIASES
    assert "workspace-lock" in ALIASES
    assert DEFAULT_FILENAMES == (LOCKFILE_NAME,)
    assert LOCKFILE_VERSION == 1


def test_record_to_dict() -> None:
    """``conda_lockfiles.rattler_lock.v6._record_to_dict`` works as expected."""

    class FakeRecord:
        url = "https://example.com/numpy-1.24.conda"
        _data = {"sha256": "abc123", "md5": "def456", "size": 1024}

        def get(self, key, default=None):
            return self._data.get(key, default)

    result = _record_to_dict(FakeRecord())  # type: ignore[arg-type]
    assert result["conda"] == "https://example.com/numpy-1.24.conda"
    assert result["sha256"] == "abc123"
    assert result["md5"] == "def456"
    assert result["size"] == 1024
    assert "depends" not in result


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        (
            "conda.lock",
            "version: 1\nenvironments: {}\npackages: []\n",
            True,
        ),
        ("pixi.lock", "version: 1\n", False),
        ("pixi.lock", "version: 6\n", False),
        ("conda.lock", "version: 6\n", False),
        ("conda.lock", "{{not yaml::", False),
        ("conda.lock", None, False),
    ],
    ids=[
        "conda-lock-v1",
        "pixi-lock-wrong-name",
        "pixi-lock-v6",
        "conda-lock-v6",
        "invalid-yaml",
        "missing",
    ],
)
def test_conda_lock_loader_can_handle(
    tmp_path: Path, filename: str, content: str | None, expected: bool
) -> None:
    path = tmp_path / filename
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert CondaLockLoader(path).can_handle() is expected


def test_conda_lock_loader_caches_data(lockfile_with_platforms: Path) -> None:
    """_data is read once and cached across calls."""
    loader = CondaLockLoader(lockfile_with_platforms)
    assert loader.can_handle() is True
    assert loader._data_cache is not None

    lockfile_with_platforms.write_text("version: 99\n", encoding="utf-8")
    assert loader.can_handle() is True


def test_conda_lock_loader_available_platforms(
    lockfile_with_platforms: Path,
) -> None:
    loader = CondaLockLoader(lockfile_with_platforms)
    assert loader.available_platforms == ("linux-64", "osx-arm64")


@pytest.fixture
def fake_records_factory(monkeypatch: pytest.MonkeyPatch):
    """Stub conda-lockfiles' URL -> PackageRecord conversion.

    Returns a closure that records invocations so tests can assert on
    which URLs were passed through.  Patches the name as imported into
    ``conda_lockfiles.rattler_lock.v6`` (that is where the helper we
    reuse looks it up).
    """
    calls: list[dict] = []

    class FakeRecord:
        def __init__(self, url: str):
            self.url = url
            self.name = "python"

    def fake_records_from_conda_urls(metadata_by_url, **kwargs):
        calls.append(dict(metadata_by_url))
        return tuple(FakeRecord(url) for url in metadata_by_url)

    monkeypatch.setattr(
        "conda_lockfiles.rattler_lock.v6.records_from_conda_urls",
        fake_records_from_conda_urls,
    )
    return calls


@pytest.mark.parametrize(
    ("platform", "expected_url"),
    [
        ("linux-64", "https://example.com/python-linux-64.conda"),
        ("osx-arm64", "https://example.com/python-osx-arm64.conda"),
    ],
    ids=["linux-64", "osx-arm64"],
)
def test_conda_lock_loader_env_for_platform(
    lockfile_with_platforms: Path,
    fake_records_factory: list,
    platform: str,
    expected_url: str,
) -> None:
    loader = CondaLockLoader(lockfile_with_platforms)
    env = loader.env_for(platform)

    assert env.platform == platform
    assert len(env.explicit_packages) == 1
    assert env.explicit_packages[0].url == expected_url


def test_conda_lock_loader_env_pypi_as_external(
    lockfile_with_platforms: Path,
    fake_records_factory: list,
) -> None:
    loader = CondaLockLoader(lockfile_with_platforms)
    env = loader.env_for("linux-64")

    assert "pypi" in env.external_packages
    assert "https://pypi.org/simple/requests/" in env.external_packages["pypi"]


def test_conda_lock_loader_env_uses_context_subdir(
    lockfile_with_platforms: Path,
    fake_records_factory: list,
) -> None:
    """``env`` property delegates to ``env_for(context.subdir)``."""
    from conda.base.context import context

    if context.subdir not in ("linux-64", "osx-arm64"):
        pytest.skip(f"test fixture does not cover {context.subdir}")

    loader = CondaLockLoader(lockfile_with_platforms)
    env = loader.env

    assert env.platform == context.subdir


@pytest.mark.parametrize(
    ("content", "env_for_kwargs", "match"),
    [
        (None, {"platform": "win-64"}, "does not list packages for platform"),
        (
            "version: 1\nenvironments:\n  test: {}\npackages: []\n",
            {"platform": "linux-64", "name": "default"},
            "not found in lockfile",
        ),
        (
            "version: 99\nenvironments: {}\npackages: []\n",
            {"platform": "linux-64"},
            f"Unsupported {LOCKFILE_NAME} version",
        ),
    ],
    ids=["missing-platform", "missing-environment", "wrong-version"],
)
def test_conda_lock_loader_env_for_errors(
    tmp_path: Path,
    lockfile_with_platforms: Path,
    content: str | None,
    env_for_kwargs: dict,
    match: str,
) -> None:
    """``env_for`` raises ``ValueError`` for missing / malformed inputs.

    ``content=None`` reuses ``lockfile_with_platforms`` (a realistic
    multi-platform lockfile) so the missing-platform message can name
    real alternatives.
    """
    if content is None:
        path = lockfile_with_platforms
    else:
        path = tmp_path / LOCKFILE_NAME
        path.write_text(content, encoding="utf-8")

    loader = CondaLockLoader(path)
    with pytest.raises(ValueError, match=match):
        loader.env_for(**env_for_kwargs)


class _FakePkg:
    """Minimal stand-in for ``PackageRecord`` used by lockfile tests."""

    def __init__(self, name: str, url: str) -> None:
        self.name = name
        self.url = url

    def get(self, key: str, default: object = None) -> object:
        return default


@pytest.fixture
def fake_solver_factory(monkeypatch: pytest.MonkeyPatch):
    """Replace ``_solve_for_records`` with a deterministic stub.

    The stub returns one package per ``(env, platform)`` pair whose URL
    encodes both, so tests can assert platform-specific records landed
    in the right slots of the lockfile.  Each call is recorded so tests
    can assert the order and platform targets passed through.
    """
    calls: list[tuple[str, str]] = []

    def _factory(failures: set[tuple[str, str]] | None = None) -> list:
        failures = failures or set()

        def fake_solve(ctx_arg, resolved, platform):
            calls.append((resolved.name, platform))
            if (resolved.name, platform) in failures:
                from conda_workspaces.exceptions import SolveError

                raise SolveError(resolved.name, "unsatisfiable", platform=platform)
            return [
                _FakePkg(
                    "python",
                    f"https://example.com/python-{resolved.name}-{platform}.conda",
                ),
            ]

        monkeypatch.setattr("conda_workspaces.lockfile._solve_for_records", fake_solve)
        return calls

    return _factory


@pytest.fixture
def resolved_envs_factory():
    """Build a ``{name: ResolvedEnvironment}`` dict from a minimal spec.

    Each keyword argument is an environment name mapped to the list of
    declared platforms (or ``None`` for "no declared platforms, fall
    back to the host at lock time"):

        resolved_envs_factory(default=["linux-64", "osx-arm64"], test=["linux-64"])
    """

    def _factory(**envs: list[str] | None) -> dict[str, ResolvedEnvironment]:
        return {
            name: ResolvedEnvironment(
                name=name,
                channels=[Channel("conda-forge")],
                platforms=platforms,
            )
            for name, platforms in envs.items()
        }

    return _factory


@pytest.mark.parametrize(
    ("envs", "host", "requested_platforms", "expected_pairs"),
    [
        pytest.param(
            {"default": ["linux-64", "osx-arm64"], "test": ["linux-64"]},
            "linux-64",
            None,
            {("default", "linux-64"), ("default", "osx-arm64"), ("test", "linux-64")},
            id="all-declared-platforms",
        ),
        pytest.param(
            {"default": None},
            "linux-64",
            None,
            {("default", "linux-64")},
            id="host-fallback-when-undeclared",
        ),
        pytest.param(
            {"default": ["linux-64", "osx-arm64"], "test": ["linux-64"]},
            "linux-64",
            ("osx-arm64",),
            {("default", "osx-arm64")},
            id="requested-platforms-intersect-declared",
        ),
    ],
)
def test_generate_lockfile_solves_expected_pairs(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    fake_solver_factory,
    resolved_envs_factory,
    envs: dict[str, list[str] | None],
    host: str,
    requested_platforms: tuple[str, ...] | None,
    expected_pairs: set[tuple[str, str]],
) -> None:
    """Intersection of declared platforms with the requested subset."""
    ctx = workspace_ctx_factory(platform=host, env_names=list(envs))
    calls = fake_solver_factory()
    resolved_envs = resolved_envs_factory(**envs)

    result = generate_lockfile(ctx, resolved_envs, platforms=requested_platforms)
    assert result == tmp_path / LOCKFILE_NAME
    assert set(calls) == expected_pairs

    content = result.read_text(encoding="utf-8")
    assert f"version: {LOCKFILE_VERSION}" in content
    for env_name, platform in expected_pairs:
        assert f"python-{env_name}-{platform}.conda" in content


def test_generate_lockfile_progress_callback(
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    fake_solver_factory,
    resolved_envs_factory,
) -> None:
    """``progress`` is invoked once per ``(env, platform)`` pair, in order."""
    ctx = workspace_ctx_factory(env_names=["default"])
    fake_solver_factory()
    resolved_envs = resolved_envs_factory(default=["linux-64", "osx-arm64"])

    events: list[tuple[str, str]] = []
    generate_lockfile(
        ctx,
        resolved_envs,
        progress=lambda env, platform: events.append((env, platform)),
    )
    assert events == [("default", "linux-64"), ("default", "osx-arm64")]


@pytest.mark.parametrize(
    "failing_pair",
    [
        ("default", "linux-64"),
        ("default", "osx-arm64"),
    ],
    ids=["fails-on-first-platform", "fails-on-second-platform"],
)
def test_generate_lockfile_fails_fast_on_solve_error(
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    fake_solver_factory,
    resolved_envs_factory,
    failing_pair: tuple[str, str],
) -> None:
    """Default behaviour: first unsolvable pair raises and writes no lockfile."""
    from conda_workspaces.exceptions import SolveError

    env_name, failing_platform = failing_pair
    ctx = workspace_ctx_factory(env_names=[env_name])
    fake_solver_factory(failures={failing_pair})
    resolved_envs = resolved_envs_factory(**{env_name: ["linux-64", "osx-arm64"]})

    with pytest.raises(SolveError, match=failing_platform):
        generate_lockfile(ctx, resolved_envs)
    assert not lockfile_path(ctx).exists()


def test_generate_lockfile_skip_unsolvable_partial(
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    fake_solver_factory,
    resolved_envs_factory,
) -> None:
    """``skip_unsolvable=True`` writes the solvable pairs and invokes on_skip."""
    ctx = workspace_ctx_factory(env_names=["default", "test"])
    fake_solver_factory(failures={("default", "osx-arm64")})
    resolved_envs = resolved_envs_factory(
        default=["linux-64", "osx-arm64"],
        test=["linux-64"],
    )

    skipped: list[tuple[str, str, str]] = []

    def _on_skip(env: str, platform: str, exc) -> None:
        skipped.append((env, platform, exc.reason))

    result = generate_lockfile(
        ctx,
        resolved_envs,
        skip_unsolvable=True,
        on_skip=_on_skip,
    )

    assert skipped == [("default", "osx-arm64", "unsatisfiable")]
    content = result.read_text(encoding="utf-8")
    assert "python-default-linux-64.conda" in content
    assert "python-test-linux-64.conda" in content
    assert "python-default-osx-arm64.conda" not in content


def test_generate_lockfile_skip_unsolvable_all_fail(
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    fake_solver_factory,
    resolved_envs_factory,
) -> None:
    """When every pair fails, ``skip_unsolvable`` raises AllTargetsUnsolvableError."""
    from conda_workspaces.exceptions import AllTargetsUnsolvableError

    ctx = workspace_ctx_factory(env_names=["default", "test"])
    all_pairs = {
        ("default", "linux-64"),
        ("default", "osx-arm64"),
        ("test", "linux-64"),
    }
    fake_solver_factory(failures=all_pairs)
    resolved_envs = resolved_envs_factory(
        default=["linux-64", "osx-arm64"],
        test=["linux-64"],
    )

    with pytest.raises(AllTargetsUnsolvableError) as excinfo:
        generate_lockfile(ctx, resolved_envs, skip_unsolvable=True)
    assert len(excinfo.value.failures) == len(all_pairs)
    assert not lockfile_path(ctx).exists()


@pytest.mark.parametrize(
    ("platform", "write_lockfile", "env_name", "match"),
    [
        ("linux-64", False, "default", None),
        ("linux-64", True, "no-such-env", "no-such-env"),
        ("win-64", True, "default", "default"),
    ],
    ids=["missing-file", "missing-env", "missing-platform"],
)
def test_install_from_lockfile_errors(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    lockfile_content: str,
    platform: str,
    write_lockfile: bool,
    env_name: str,
    match: str | None,
) -> None:
    """``install_from_lockfile`` raises ``LockfileNotFoundError`` for the
    three failure modes: no file at all, wrong env name, wrong platform.
    """
    ctx = workspace_ctx_factory(platform=platform)
    if write_lockfile:
        (tmp_path / LOCKFILE_NAME).write_text(lockfile_content, encoding="utf-8")

    with pytest.raises(LockfileNotFoundError, match=match):
        install_from_lockfile(ctx, env_name)


def test_install_from_lockfile(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install_from_lockfile reads conda.lock, extracts URLs, and installs."""
    ctx = workspace_ctx_factory()

    lockfile = tmp_path / LOCKFILE_NAME
    lockfile.write_text(
        "version: 1\n"
        "environments:\n"
        "  default:\n"
        "    channels:\n"
        "    - url: conda-forge\n"
        "    packages:\n"
        "      linux-64:\n"
        "      - conda: https://example.com/python.conda\n"
        "      - conda: https://example.com/numpy.conda\n"
        "packages:\n"
        "- conda: https://example.com/python.conda\n"
        "  sha256: aaa\n"
        "- conda: https://example.com/numpy.conda\n"
        "  sha256: bbb\n",
        encoding="utf-8",
    )

    records_sentinel = [object(), object()]
    get_records_calls: list[list] = []

    def fake_get_records(lines):
        get_records_calls.append(lines)
        return records_sentinel

    monkeypatch.setattr(
        "conda.misc.get_package_records_from_explicit",
        fake_get_records,
    )

    install_calls: list[dict] = []

    def fake_install(*, package_cache_records, prefix):
        install_calls.append({"records": package_cache_records, "prefix": prefix})

    monkeypatch.setattr(
        "conda.misc.install_explicit_packages",
        fake_install,
    )

    install_from_lockfile(ctx, "default")

    assert len(get_records_calls) == 1
    assert get_records_calls[0] == [
        "https://example.com/python.conda",
        "https://example.com/numpy.conda",
    ]
    assert len(install_calls) == 1
    assert install_calls[0]["records"] == records_sentinel
    assert install_calls[0]["prefix"] == str(ctx.env_prefix("default"))


@pytest.mark.parametrize(
    ("host", "target", "expected"),
    [
        ("linux-64", "linux-64", {}),
        ("linux-64", "linux-aarch64", {}),
        ("osx-arm64", "osx-64", {}),
        ("linux-64", "osx-arm64", {"CONDA_OVERRIDE_OSX": "11.0"}),
        ("linux-64", "osx-64", {"CONDA_OVERRIDE_OSX": "10.15"}),
        ("osx-arm64", "linux-64", {"CONDA_OVERRIDE_GLIBC": "2.17"}),
        ("osx-arm64", "linux-aarch64", {"CONDA_OVERRIDE_GLIBC": "2.17"}),
        ("linux-64", "win-64", {"CONDA_OVERRIDE_WIN": "0"}),
        ("win-64", "linux-64", {"CONDA_OVERRIDE_GLIBC": "2.17"}),
        ("linux-64", "noarch", {}),
    ],
    ids=[
        "native-linux",
        "linux-to-linux-cross-arch",
        "osx-to-osx-cross-arch",
        "linux-to-osx-arm64",
        "linux-to-osx-64",
        "osx-to-linux-64",
        "osx-to-linux-aarch64",
        "linux-to-win",
        "win-to-linux",
        "noarch-target",
    ],
)
def test_virtual_package_overrides_by_target(
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    target: str,
    expected: dict[str, str],
) -> None:
    """Overrides trigger only when host family differs from the target family."""
    monkeypatch.setattr(conda_context, "_subdir", host)
    monkeypatch.delenv("CONDA_OVERRIDE_GLIBC", raising=False)
    monkeypatch.delenv("CONDA_OVERRIDE_OSX", raising=False)
    monkeypatch.delenv("CONDA_OVERRIDE_WIN", raising=False)

    env = ResolvedEnvironment(name="test")
    assert env.virtual_package_overrides(target) == expected


def test_virtual_package_overrides_respect_existing_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit ``CONDA_OVERRIDE_*`` values win over the baseline."""
    monkeypatch.setattr(conda_context, "_subdir", "osx-arm64")
    monkeypatch.setenv("CONDA_OVERRIDE_GLIBC", "2.28")

    env = ResolvedEnvironment(name="test")
    assert env.virtual_package_overrides("linux-64") == {}


@pytest.mark.parametrize(
    ("system_requirements", "expected"),
    [
        ({}, {"CONDA_OVERRIDE_GLIBC": "2.17"}),
        ({"glibc": "2.28"}, {"CONDA_OVERRIDE_GLIBC": "2.28"}),
        ({"__glibc": "2.34"}, {"CONDA_OVERRIDE_GLIBC": "2.34"}),
        ({"osx": "12.0"}, {"CONDA_OVERRIDE_GLIBC": "2.17"}),
    ],
    ids=[
        "default-baseline",
        "bare-name-wins",
        "dunder-name-wins",
        "unrelated-requirement-ignored",
    ],
)
def test_virtual_package_overrides_lift_system_requirements(
    monkeypatch: pytest.MonkeyPatch,
    system_requirements: dict[str, str],
    expected: dict[str, str],
) -> None:
    """``[system-requirements]`` versions are lifted into the overrides."""
    monkeypatch.setattr(conda_context, "_subdir", "osx-arm64")
    monkeypatch.delenv("CONDA_OVERRIDE_GLIBC", raising=False)

    env = ResolvedEnvironment(name="test", system_requirements=system_requirements)
    assert env.virtual_package_overrides("linux-64") == expected


def test_solve_for_records_applies_virtual_package_overrides(
    monkeypatch: pytest.MonkeyPatch,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    resolved_envs_factory,
) -> None:
    """Cross-compiled solves see the baseline ``CONDA_OVERRIDE_*`` in os.environ."""
    monkeypatch.setattr(conda_context, "_subdir", "osx-arm64")
    monkeypatch.delenv("CONDA_OVERRIDE_GLIBC", raising=False)

    ctx = workspace_ctx_factory()
    resolved = resolved_envs_factory(default=["linux-64"])["default"]
    resolved.conda_dependencies = {"python": MatchSpec("python=3.12")}

    observed: dict[str, str | None] = {}

    class FakeSolver:
        def __init__(self, *args, **kwargs) -> None:
            observed["CONDA_OVERRIDE_GLIBC"] = os.environ.get("CONDA_OVERRIDE_GLIBC")
            observed["_subdir"] = conda_context.subdir

        def solve_final_state(self) -> list:
            return []

    monkeypatch.setattr(
        conda_context.plugin_manager,
        "get_cached_solver_backend",
        lambda: FakeSolver,
    )

    _solve_for_records(ctx, resolved, "linux-64")

    assert observed["CONDA_OVERRIDE_GLIBC"] == "2.17"
    assert observed["_subdir"] == "linux-64"
    # After the solve, the baseline must have been restored.
    assert os.environ.get("CONDA_OVERRIDE_GLIBC") is None


def test_solve_for_records_native_solve_leaves_env_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    resolved_envs_factory,
) -> None:
    """Native ``(host, target)`` solves do not mutate any ``CONDA_OVERRIDE_*``."""
    monkeypatch.setattr(conda_context, "_subdir", "linux-64")
    monkeypatch.delenv("CONDA_OVERRIDE_GLIBC", raising=False)

    ctx = workspace_ctx_factory()
    resolved = resolved_envs_factory(default=["linux-64"])["default"]
    resolved.conda_dependencies = {"python": MatchSpec("python=3.12")}

    seen_env: dict[str, str | None] = {}

    class FakeSolver:
        def __init__(self, *args, **kwargs) -> None:
            seen_env["CONDA_OVERRIDE_GLIBC"] = os.environ.get("CONDA_OVERRIDE_GLIBC")

        def solve_final_state(self) -> list:
            return []

    monkeypatch.setattr(
        conda_context.plugin_manager,
        "get_cached_solver_backend",
        lambda: FakeSolver,
    )

    _solve_for_records(ctx, resolved, "linux-64")

    assert seen_env["CONDA_OVERRIDE_GLIBC"] is None
