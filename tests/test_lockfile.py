"""Tests for conda_workspaces.lockfile."""

from __future__ import annotations

import io
import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from conda.base.context import context as conda_context
from conda.common.serialize.yaml import dump as yaml_dump
from conda.models.match_spec import MatchSpec
from conda_lockfiles.load_yaml import load_yaml

from conda_workspaces.context import WorkspaceContext
from conda_workspaces.exceptions import (
    LockfileIntegrityError,
    LockfileMergeError,
    LockfileNotFoundError,
    SolveError,
)
from conda_workspaces.lockfile import (
    ALIASES,
    DEFAULT_FILENAMES,
    FORMAT,
    LOCKFILE_NAME,
    LOCKFILE_VERSION,
    CondaLockLoader,
    check_lockfile_satisfiability,
    generate_lockfile,
    install_from_lockfile,
    lockfile_path,
    merge_lockfiles,
)
from conda_workspaces.models import (
    Channel,
    Environment,
    Feature,
    LockfileStatus,
    WorkspaceConfig,
)
from conda_workspaces.resolver import ResolvedEnvironment, resolve_environment


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
        (None, {"platform": "win-64"}, "does not include packages for"),
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
        if key == "sha256":
            return "a" * 64
        return default


@pytest.fixture
def fake_solver_factory(monkeypatch: pytest.MonkeyPatch):
    """Replace ``ResolvedEnvironment.solve_for_platform`` with a deterministic stub.

    The stub returns one package per ``(env, platform)`` pair whose URL
    encodes both, so tests can assert platform-specific records landed
    in the right slots of the lockfile.  Each call is recorded so tests
    can assert the order and platform targets passed through.
    """
    calls: list[tuple[str, str]] = []

    def _factory(failures: set[tuple[str, str]] | None = None) -> list:
        failures = failures or set()

        def fake_solve(self, platform, *, prefix):
            calls.append((self.name, platform))
            if (self.name, platform) in failures:
                raise SolveError(self.name, "unsatisfiable", platform=platform)
            return [
                _FakePkg(
                    "python",
                    "https://conda.anaconda.org/conda-forge/"
                    f"{platform}/python-{self.name}-{platform}.conda",
                ),
            ]

        monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", fake_solve)
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


@pytest.mark.parametrize(
    ("platform", "expected_extra", "forbidden"),
    [
        pytest.param(
            "linux-64",
            frozenset(),
            frozenset(
                {
                    "python.app",
                    "anaconda_prompt",
                    "anaconda_powershell_prompt",
                }
            ),
            id="linux-64",
        ),
        pytest.param(
            "linux-aarch64",
            frozenset(),
            frozenset(
                {
                    "python.app",
                    "anaconda_prompt",
                    "anaconda_powershell_prompt",
                }
            ),
            id="linux-aarch64",
        ),
        pytest.param(
            "osx-arm64",
            frozenset({"python.app"}),
            frozenset({"anaconda_prompt", "anaconda_powershell_prompt"}),
            id="osx-arm64",
        ),
        pytest.param(
            "win-64",
            frozenset({"anaconda_prompt", "anaconda_powershell_prompt"}),
            frozenset({"python.app"}),
            id="win-64",
        ),
    ],
)
def test_generate_lockfile_resolves_target_dependencies_per_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    expected_extra: frozenset[str],
    forbidden: frozenset[str],
) -> None:
    """Target dependency tables are scoped to their matching platform."""
    config = WorkspaceConfig(
        name="target-dep-repro",
        channels=[
            Channel("https://repo.anaconda.com/pkgs/main"),
            Channel("https://repo.anaconda.com/pkgs/msys2"),
        ],
        platforms=["linux-64", "linux-aarch64", "osx-arm64", "win-64"],
        features={
            "default": Feature(
                name="default",
                conda_dependencies={
                    "python": MatchSpec("python=3.13"),
                    "conda": MatchSpec("conda==26.5.2"),
                    "menuinst": MatchSpec("menuinst"),
                },
                target_conda_dependencies={
                    "osx-arm64": {"python.app": MatchSpec("python.app")},
                    "win-64": {
                        "anaconda_prompt": MatchSpec("anaconda_prompt"),
                        "anaconda_powershell_prompt": MatchSpec(
                            "anaconda_powershell_prompt"
                        ),
                    },
                },
            )
        },
        environments={"default": Environment(name="default")},
        root=str(tmp_path),
        manifest_path=str(tmp_path / "conda.toml"),
    )
    ctx = WorkspaceContext(config)
    ctx._cache["platform"] = "osx-arm64"
    resolved_envs = {
        "default": resolve_environment(config, "default", platform=ctx.platform)
    }
    observed_deps: dict[str, set[str]] = {}

    def fake_solve(self, platform, *, prefix):
        observed_deps[platform] = set(self.conda_dependencies)
        return [
            _FakePkg(name, f"https://example.com/{name}-{platform}.conda")
            for name in sorted(self.conda_dependencies)
        ]

    monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", fake_solve)

    result = generate_lockfile(ctx, resolved_envs, config=config)

    common = {"conda", "menuinst", "python"}
    assert observed_deps[platform] == common | expected_extra

    data = load_yaml(result)
    package_refs = data["environments"]["default"]["packages"][platform]
    locked_names = {
        ref["conda"].rpartition("/")[2].removesuffix(f"-{platform}.conda")
        for ref in package_refs
    }
    assert expected_extra <= locked_names
    assert not forbidden & locked_names


@pytest.mark.parametrize(
    ("platform", "expected_requirements"),
    [
        pytest.param("linux-64", {"glibc": "2.28"}, id="linux-64"),
        pytest.param("linux-aarch64", {"glibc": "2.28"}, id="linux-aarch64"),
        pytest.param("osx-arm64", {}, id="osx-arm64"),
        pytest.param("win-64", {}, id="win-64"),
    ],
)
def test_generate_lockfile_resolves_rich_platform_system_requirements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    expected_requirements: dict[str, str],
) -> None:
    """Rich-platform requirements are scoped to their matching platform."""
    config = WorkspaceConfig(
        name="rich-platform-sysreq-repro",
        channels=[Channel("conda-forge")],
        platforms=["linux-64", "linux-aarch64", "osx-arm64", "win-64"],
        platform_system_requirements={
            "linux-64": {"glibc": "2.28"},
            "linux-aarch64": {"glibc": "2.28"},
        },
        features={"default": Feature(name="default")},
        environments={"default": Environment(name="default")},
        root=str(tmp_path),
        manifest_path=str(tmp_path / "conda.toml"),
    )
    ctx = WorkspaceContext(config)
    ctx._cache["platform"] = "osx-arm64"
    resolved_envs = {
        "default": resolve_environment(config, "default", platform=ctx.platform)
    }
    observed_requirements: dict[str, dict[str, str]] = {}
    observed_overrides: dict[str, dict[str, str]] = {}

    def fake_solve(self, platform, *, prefix):
        observed_requirements[platform] = dict(self.system_requirements)
        observed_overrides[platform] = self.virtual_package_overrides(platform)
        return [_FakePkg("python", f"https://example.com/python-{platform}.conda")]

    monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", fake_solve)

    with conda_context._override("_subdir", "osx-arm64"):
        generate_lockfile(ctx, resolved_envs, config=config)

    assert observed_requirements[platform] == expected_requirements
    if platform.startswith("linux-"):
        assert observed_overrides[platform]["CONDA_OVERRIDE_GLIBC"] == "2.28"
    else:
        assert "CONDA_OVERRIDE_GLIBC" not in observed_overrides[platform]


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
    python_url = "https://example.com/channel/linux-64/python.conda"
    numpy_url = "https://example.com/channel/noarch/numpy.conda"
    python_sha256 = "a" * 64
    numpy_md5 = "b" * 32

    lockfile = tmp_path / LOCKFILE_NAME
    lockfile.write_text(
        "version: 1\n"
        "environments:\n"
        "  default:\n"
        "    channels:\n"
        "    - url: https://example.com/channel\n"
        "    packages:\n"
        "      linux-64:\n"
        f"      - conda: {python_url}\n"
        f"      - conda: {numpy_url}\n"
        "packages:\n"
        f"- conda: {python_url}\n"
        f"  sha256: {python_sha256}\n"
        f"- conda: {numpy_url}\n"
        f"  md5: {numpy_md5}\n",
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
        f"{python_url}#sha256:{python_sha256}",
        f"{numpy_url}#{numpy_md5}",
    ]
    assert len(install_calls) == 1
    assert install_calls[0]["records"] == records_sentinel
    assert install_calls[0]["prefix"] == str(ctx.env_prefix("default"))


@pytest.mark.parametrize(
    ("package_url", "package_record", "match"),
    [
        pytest.param(
            "https://attacker.example/pkgs/linux-64/python-3.11.9-hbad_0.tar.bz2",
            {
                "conda": (
                    "https://attacker.example/pkgs/linux-64/"
                    "python-3.11.9-hbad_0.tar.bz2"
                ),
                "sha256": "0" * 64,
            },
            "not under any channel",
            id="off-channel",
        ),
        pytest.param(
            "https://example.com/channel/linux-64/python-3.11.9-hgood_0.tar.bz2",
            None,
            "no top-level package record",
            id="missing-top-level-record",
        ),
        pytest.param(
            "https://example.com/channel/linux-64/python-3.11.9-hgood_0.tar.bz2",
            {
                "conda": (
                    "https://example.com/channel/linux-64/python-3.11.9-hgood_0.tar.bz2"
                )
            },
            "missing a sha256 or md5 digest",
            id="missing-digest",
        ),
        pytest.param(
            "https://example.com/channel/linux-64/python-3.11.9-hgood_0.tar.bz2",
            {
                "conda": (
                    "https://example.com/channel/linux-64/python-3.11.9-hgood_0.tar.bz2"
                ),
                "sha256": "not-a-valid-sha256",
            },
            "invalid sha256 digest",
            id="invalid-sha256",
        ),
    ],
)
def test_install_from_lockfile_rejects_unbound_package_refs(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    monkeypatch: pytest.MonkeyPatch,
    package_url: str,
    package_record: dict[str, str] | None,
    match: str,
) -> None:
    """Locked installs reject refs that are not bound to channel + hash metadata."""
    ctx = workspace_ctx_factory()
    packages = [] if package_record is None else [package_record]
    lockfile = {
        "version": LOCKFILE_VERSION,
        "environments": {
            "default": {
                "channels": [{"url": "https://example.com/channel"}],
                "packages": {"linux-64": [{"conda": package_url}]},
            }
        },
        "packages": packages,
    }
    buf = io.StringIO()
    yaml_dump(lockfile, buf)
    (tmp_path / LOCKFILE_NAME).write_text(buf.getvalue(), encoding="utf-8")

    get_records_calls: list[list] = []

    def fake_get_records(lines):
        get_records_calls.append(lines)
        return []

    monkeypatch.setattr(
        "conda.misc.get_package_records_from_explicit",
        fake_get_records,
    )

    with pytest.raises(LockfileIntegrityError, match=match):
        install_from_lockfile(ctx, "default")

    assert get_records_calls == []


def test_lockfile_status_rejects_off_channel_package_refs() -> None:
    """Freshness checks reject the off-channel URL used by the scan PoC."""
    config = WorkspaceConfig(
        name="lock-test",
        channels=[Channel("conda-forge")],
        platforms=["linux-64"],
        features={
            "default": Feature(
                name="default",
                conda_dependencies={"python": MatchSpec("python >=3.11")},
            )
        },
        environments={"default": Environment(name="default")},
    )
    package_url = "https://attacker.example/pkgs/linux-64/python-3.11.9-hbad_0.tar.bz2"
    lockfile_data = {
        "version": LOCKFILE_VERSION,
        "environments": {
            "default": {
                "channels": [{"url": str(Channel("conda-forge"))}],
                "packages": {"linux-64": [{"conda": package_url}]},
            }
        },
        "packages": [{"conda": package_url, "sha256": "0" * 64}],
    }

    status = check_lockfile_satisfiability(config, lockfile_data, "linux-64")

    assert status.status == LockfileStatus.OUT_OF_DATE
    assert "not under a declared channel" in status.reason


def test_install_from_lockfile_explicit_prefix_override(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install_from_lockfile can install elsewhere while embedding a final prefix."""
    ctx = workspace_ctx_factory()
    package_url = "https://example.com/channel/linux-64/python.conda"
    package_sha256 = "a" * 64

    lockfile = tmp_path / LOCKFILE_NAME
    lockfile.write_text(
        "version: 1\n"
        "environments:\n"
        "  default:\n"
        "    channels:\n"
        "    - url: https://example.com/channel\n"
        "    packages:\n"
        "      linux-64:\n"
        f"      - conda: {package_url}\n"
        "packages:\n"
        f"- conda: {package_url}\n"
        f"  sha256: {package_sha256}\n",
        encoding="utf-8",
    )

    records_sentinel = [object()]

    def fake_get_records(lines):
        assert lines == [f"{package_url}#sha256:{package_sha256}"]
        return records_sentinel

    monkeypatch.setattr(
        "conda.misc.get_package_records_from_explicit",
        fake_get_records,
    )

    install_prefix = tmp_path / "rootfs" / "opt" / "runtime"
    final_prefix = tmp_path / "final" / "runtime"
    install_calls: list[dict] = []

    def fake_install(*, package_cache_records, prefix):
        install_calls.append(
            {
                "records": package_cache_records,
                "prefix": prefix,
                "target_prefix_override": conda_context.target_prefix_override,
            }
        )

    monkeypatch.setattr(
        "conda.misc.install_explicit_packages",
        fake_install,
    )

    install_from_lockfile(
        ctx,
        "default",
        prefix=install_prefix,
        target_prefix_override=final_prefix,
    )

    assert len(install_calls) == 1
    assert install_calls[0]["records"] == records_sentinel
    assert install_calls[0]["prefix"] == str(install_prefix)
    assert install_calls[0]["target_prefix_override"] == str(final_prefix)
    assert conda_context.target_prefix_override == ""


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
        ({"libc": "2.28"}, {"CONDA_OVERRIDE_GLIBC": "2.28"}),
        ({"__glibc": "2.34"}, {"CONDA_OVERRIDE_GLIBC": "2.34"}),
        ({"osx": "12.0"}, {"CONDA_OVERRIDE_GLIBC": "2.17"}),
    ],
    ids=[
        "default-baseline",
        "bare-name-wins",
        "pixi-name-wins",
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


@pytest.mark.parametrize(
    ("host", "target", "expected_glibc_during_solve"),
    [
        ("osx-arm64", "linux-64", "2.17"),
        ("linux-64", "linux-64", None),
    ],
    ids=["cross-compile-seeds-baseline", "native-leaves-env-unchanged"],
)
def test_solve_for_platform_virtual_package_env(
    monkeypatch: pytest.MonkeyPatch,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    resolved_envs_factory,
    host: str,
    target: str,
    expected_glibc_during_solve: str | None,
) -> None:
    """``solve_for_platform`` seeds baselines only when host differs from target."""
    monkeypatch.setattr(conda_context, "_subdir", host)
    monkeypatch.delenv("CONDA_OVERRIDE_GLIBC", raising=False)

    ctx = workspace_ctx_factory()
    resolved = resolved_envs_factory(default=[target])["default"]
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

    resolved.solve_for_platform(target, prefix=ctx.env_prefix(resolved.name))

    assert observed["CONDA_OVERRIDE_GLIBC"] == expected_glibc_during_solve
    assert observed["_subdir"] == target
    # After the solve, any baseline the context manager applied must
    # have been restored — nothing leaks into the surrounding process.
    assert os.environ.get("CONDA_OVERRIDE_GLIBC") is None


@pytest.fixture
def write_fragment() -> Callable[[WorkspaceContext, dict, str], Path]:
    """Factory that solves one platform into a ``conda.lock.<platform>`` fragment.

    ``conda_lockfiles.load_yaml`` caches parsed YAML by path; fragment
    files are rewritten between calls in the merge tests, so the
    factory evicts the cache after every write to keep subsequent
    reads fresh.
    """

    def _factory(ctx: WorkspaceContext, resolved_envs, platform: str) -> Path:
        target = ctx.root / f"conda.lock.{platform}"
        generate_lockfile(
            ctx,
            resolved_envs,
            platforms=(platform,),
            output_path=target,
        )
        load_yaml.cache_clear()
        return target

    return _factory


def test_merge_lockfiles_byte_stable_with_single_run(
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    fake_solver_factory,
    resolved_envs_factory,
    write_fragment: Callable[[WorkspaceContext, dict, str], Path],
) -> None:
    """Merging per-platform fragments must match a single-run lockfile byte-for-byte."""
    ctx_single = workspace_ctx_factory(env_names=["default", "test"])
    fake_solver_factory()
    resolved_envs = resolved_envs_factory(
        default=["linux-64", "osx-arm64"],
        test=["linux-64", "osx-arm64"],
    )
    single_path = generate_lockfile(ctx_single, resolved_envs)
    single_content = single_path.read_text(encoding="utf-8")

    ctx_split = workspace_ctx_factory(env_names=["default", "test"])
    fake_solver_factory()
    resolved_envs_split = resolved_envs_factory(
        default=["linux-64", "osx-arm64"],
        test=["linux-64", "osx-arm64"],
    )
    frag_linux = write_fragment(ctx_split, resolved_envs_split, "linux-64")
    frag_osx = write_fragment(ctx_split, resolved_envs_split, "osx-arm64")

    # Delete any lockfile left behind by the fragment solves so the
    # merge writes into a clean slate.
    merged_path = lockfile_path(ctx_split)
    if merged_path.exists():
        merged_path.unlink()

    result = merge_lockfiles([frag_linux, frag_osx], ctx_split)
    assert result == merged_path
    assert merged_path.read_text(encoding="utf-8") == single_content


def test_merge_lockfiles_rejects_empty_input(
    workspace_ctx_factory: Callable[..., WorkspaceContext],
) -> None:
    """An empty fragment list is a user error."""
    ctx = workspace_ctx_factory()
    with pytest.raises(LockfileMergeError, match="no lockfile fragments"):
        merge_lockfiles([], ctx)


def test_merge_lockfiles_missing_file(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
) -> None:
    ctx = workspace_ctx_factory()
    with pytest.raises(LockfileMergeError, match="does not exist"):
        merge_lockfiles([tmp_path / "missing.lock"], ctx)


def test_merge_lockfiles_rejects_wrong_version(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
) -> None:
    ctx = workspace_ctx_factory()
    bad = tmp_path / "conda.lock.bad"
    bad.write_text(
        "version: 99\nenvironments: {}\npackages: []\n",
        encoding="utf-8",
    )
    with pytest.raises(LockfileMergeError, match="version"):
        merge_lockfiles([bad], ctx)


def test_merge_lockfiles_fills_manifest_channels_for_fragment_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merge accepts fragments that omit an unused manifest-declared channel."""
    main = "https://repo.anaconda.com/pkgs/main"
    msys2 = "https://repo.anaconda.com/pkgs/msys2"
    config = WorkspaceConfig(
        name="fragment-channel-subset",
        channels=[Channel(main), Channel(msys2)],
        platforms=["linux-64", "win-64"],
        features={"default": Feature(name="default")},
        environments={"default": Environment(name="default")},
        root=str(tmp_path),
        manifest_path=str(tmp_path / "conda.toml"),
    )
    ctx = WorkspaceContext(config)
    ctx._cache["platform"] = "linux-64"
    resolved_envs = {
        "default": ResolvedEnvironment(
            name="default",
            platforms=["linux-64", "win-64"],
        )
    }

    def fake_solve(
        self: ResolvedEnvironment,
        platform: str,
        *,
        prefix: str | Path,
    ) -> list[_FakePkg]:
        return [_FakePkg("python", f"{main}/{platform}/python-{platform}.conda")]

    monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", fake_solve)

    frag_linux = generate_lockfile(
        ctx,
        resolved_envs,
        config=config,
        platforms=("linux-64",),
        output_path=tmp_path / "conda.lock.linux-64",
    )
    frag_win = generate_lockfile(
        ctx,
        resolved_envs,
        config=config,
        platforms=("win-64",),
        output_path=tmp_path / "conda.lock.win-64",
    )

    win_data = load_yaml(frag_win)
    win_data["environments"]["default"]["channels"] = [{"url": main}]
    buf = io.StringIO()
    yaml_dump(win_data, buf)
    frag_win.write_text(buf.getvalue(), encoding="utf-8")
    load_yaml.cache_clear()

    merged_path = merge_lockfiles([frag_linux, frag_win], ctx)
    merged = load_yaml(merged_path)

    assert merged["environments"]["default"]["channels"] == [
        {"url": main},
        {"url": msys2},
    ]
    assert set(merged["environments"]["default"]["packages"]) == {
        "linux-64",
        "win-64",
    }


def test_merge_lockfiles_preserves_manifest_channels_with_canonical_collision(
    tmp_path: Path,
) -> None:
    """Merge preserves distinct manifest URLs even if conda canonicalizes them alike."""
    main = "https://repo.anaconda.com/pkgs/main"
    msys2 = "https://repo.anaconda.com/pkgs/msys2"

    class CanonicalCollisionChannel:
        canonical_name = "defaults"

        def __init__(self, url: str) -> None:
            self.url = url

        def __str__(self) -> str:
            return self.url

    config = WorkspaceConfig(
        name="fragment-channel-collision",
        channels=[
            CanonicalCollisionChannel(main),  # type: ignore[list-item]
            CanonicalCollisionChannel(msys2),  # type: ignore[list-item]
        ],
        platforms=["linux-64"],
        features={"default": Feature(name="default")},
        environments={"default": Environment(name="default")},
        root=str(tmp_path),
        manifest_path=str(tmp_path / "conda.toml"),
    )
    env = config.environments["default"]
    assert [str(ch) for ch in config.merged_channels(env)] == [main]
    ctx = WorkspaceContext(config)

    fragment = tmp_path / "conda.lock.linux-64"
    fragment.write_text(
        f"""\
version: 1
environments:
  default:
    channels:
    - url: {main}
    packages:
      linux-64:
      - conda: {main}/linux-64/python-linux-64.conda
packages:
- conda: {main}/linux-64/python-linux-64.conda
  sha256: {"a" * 64}
""",
        encoding="utf-8",
    )
    load_yaml.cache_clear()

    merged_path = merge_lockfiles([fragment], ctx)
    merged = load_yaml(merged_path)

    assert merged["environments"]["default"]["channels"] == [
        {"url": main},
        {"url": msys2},
    ]
    result = check_lockfile_satisfiability(config, merged, "linux-64")
    assert result.status == LockfileStatus.UP_TO_DATE


@pytest.mark.parametrize(
    ("fragment_channels", "case"),
    [
        pytest.param(
            [
                "https://repo.anaconda.com/pkgs/main",
                "https://repo.anaconda.com/pkgs/extra",
            ],
            "unknown",
            id="unknown-channel",
        ),
        pytest.param(
            [
                "https://repo.anaconda.com/pkgs/msys2",
                "https://repo.anaconda.com/pkgs/main",
            ],
            "reordered",
            id="reordered-subset",
        ),
    ],
)
def test_merge_lockfiles_rejects_manifest_channel_mismatch(
    tmp_path: Path,
    fragment_channels: list[str],
    case: str,
) -> None:
    """Fragments can omit manifest channels but cannot add or reorder them."""
    main = "https://repo.anaconda.com/pkgs/main"
    msys2 = "https://repo.anaconda.com/pkgs/msys2"
    config = WorkspaceConfig(
        name=f"fragment-channel-{case}",
        channels=[Channel(main), Channel(msys2)],
        platforms=["linux-64"],
        features={"default": Feature(name="default")},
        environments={"default": Environment(name="default")},
        root=str(tmp_path),
        manifest_path=str(tmp_path / "conda.toml"),
    )
    ctx = WorkspaceContext(config)
    channel_lines = "\n".join(f"    - url: {url}" for url in fragment_channels)
    fragment = tmp_path / "conda.lock.linux-64"
    fragment.write_text(
        f"""\
version: 1
environments:
  default:
    channels:
{channel_lines}
    packages:
      linux-64:
      - conda: {main}/linux-64/python-linux-64.conda
packages:
- conda: {main}/linux-64/python-linux-64.conda
""",
        encoding="utf-8",
    )
    load_yaml.cache_clear()

    with pytest.raises(LockfileMergeError, match="channels differ"):
        merge_lockfiles([fragment], ctx)


def test_merge_lockfiles_channel_mismatch_for_manifest_unknown_env(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
) -> None:
    """Manifest-unknown environments keep strict fragment-to-fragment checks."""
    ctx = workspace_ctx_factory(env_names=["default"])
    frag_a = tmp_path / "conda.lock.external-linux-64"
    frag_a.write_text(
        """\
version: 1
environments:
  external:
    channels:
    - url: https://conda.anaconda.org/conda-forge
    packages:
      linux-64:
      - conda: https://conda.anaconda.org/conda-forge/linux-64/python-linux-64.conda
packages:
- conda: https://conda.anaconda.org/conda-forge/linux-64/python-linux-64.conda
  sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""",
        encoding="utf-8",
    )
    frag_b = ctx.root / "conda.lock.osx-arm64"
    frag_b.write_text(
        """\
version: 1
environments:
  external:
    channels:
    - url: https://conda.anaconda.org/different-channel
    packages:
      osx-arm64:
      - conda: https://conda.anaconda.org/different-channel/osx-arm64/python-osx-arm64.conda
packages:
- conda: https://conda.anaconda.org/different-channel/osx-arm64/python-osx-arm64.conda
  sha256: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
""",
        encoding="utf-8",
    )
    load_yaml.cache_clear()

    with pytest.raises(LockfileMergeError, match="channels differ"):
        merge_lockfiles([frag_a, frag_b], ctx)


def test_merge_lockfiles_duplicate_platform(
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    fake_solver_factory,
    resolved_envs_factory,
    write_fragment: Callable[[WorkspaceContext, dict, str], Path],
) -> None:
    ctx = workspace_ctx_factory(env_names=["default"])
    fake_solver_factory()
    resolved_envs = resolved_envs_factory(default=["linux-64"])
    frag_a = write_fragment(ctx, resolved_envs, "linux-64")

    frag_b = ctx.root / "conda.lock.linux-64.dup"
    frag_b.write_text(frag_a.read_text(encoding="utf-8"), encoding="utf-8")
    load_yaml.cache_clear()

    with pytest.raises(LockfileMergeError, match="present in both"):
        merge_lockfiles([frag_a, frag_b], ctx)


@pytest.mark.parametrize(
    ("second_digest", "match"),
    [
        pytest.param("a" * 64, None, id="identical-record"),
        pytest.param("b" * 64, "conflicting package record", id="conflicting-record"),
    ],
)
def test_merge_lockfiles_package_record_consistency(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    second_digest: str,
    match: str | None,
) -> None:
    """Same-URL package records from fragments must agree exactly."""
    ctx = workspace_ctx_factory(env_names=["default"])
    channel = str(Channel("conda-forge"))
    package_url = f"{channel}/noarch/demo-1.0-0.tar.bz2"
    frag_a = tmp_path / "conda.lock.linux-64"
    frag_a.write_text(
        f"""\
version: 1
environments:
  default:
    channels:
    - url: {channel}
    packages:
      linux-64:
      - conda: {package_url}
packages:
- conda: {package_url}
  sha256: {"a" * 64}
  depends:
  - python >=3.11
""",
        encoding="utf-8",
    )
    frag_b = tmp_path / "conda.lock.osx-64"
    frag_b.write_text(
        f"""\
version: 1
environments:
  default:
    channels:
    - url: {channel}
    packages:
      osx-64:
      - conda: {package_url}
packages:
- conda: {package_url}
  sha256: {second_digest}
  depends:
  - python >=3.11
""",
        encoding="utf-8",
    )
    load_yaml.cache_clear()

    if match is not None:
        with pytest.raises(LockfileMergeError, match=match):
            merge_lockfiles([frag_a, frag_b], ctx)
        return

    merged_path = merge_lockfiles([frag_a, frag_b], ctx)
    merged = load_yaml(merged_path)

    assert merged["packages"] == [
        {
            "conda": package_url,
            "sha256": "a" * 64,
            "depends": ["python >=3.11"],
        }
    ]


@pytest.mark.parametrize(
    ("packages_block", "package_url", "match"),
    [
        pytest.param(
            "packages: []\n",
            "https://conda.anaconda.org/conda-forge/linux-64/demo-1.0-0.tar.bz2",
            "no top-level package record",
            id="missing-record",
        ),
        pytest.param(
            "packages:\n"
            "- conda: https://conda.anaconda.org/conda-forge/linux-64/"
            "demo-1.0-0.tar.bz2\n",
            "https://conda.anaconda.org/conda-forge/linux-64/demo-1.0-0.tar.bz2",
            "missing a sha256 or md5 digest",
            id="missing-digest",
        ),
        pytest.param(
            "packages:\n"
            "- conda: https://attacker.example/pkgs/linux-64/demo-1.0-0.tar.bz2\n"
            f"  sha256: {'0' * 64}\n",
            "https://attacker.example/pkgs/linux-64/demo-1.0-0.tar.bz2",
            "not under any declared channel",
            id="off-channel",
        ),
    ],
)
def test_merge_lockfiles_rejects_unbound_package_refs(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    packages_block: str,
    package_url: str,
    match: str,
) -> None:
    """Merged env refs must be bound to channel-valid hashed package records."""
    ctx = workspace_ctx_factory(env_names=["default"])
    channel = str(Channel("conda-forge"))
    fragment = tmp_path / "conda.lock.linux-64"
    fragment.write_text(
        f"""\
version: 1
environments:
  default:
    channels:
    - url: {channel}
    packages:
      linux-64:
      - conda: {package_url}
{packages_block}""",
        encoding="utf-8",
    )
    load_yaml.cache_clear()

    with pytest.raises(LockfileMergeError, match=match):
        merge_lockfiles([fragment], ctx)


@pytest.fixture
def lockfile_data_factory():
    """Factory that builds a lockfile data dict for satisfiability tests."""

    def _factory(
        *,
        version: int | None = 1,
        environments: dict | None = None,
        packages: list | None = None,
    ) -> dict:
        data: dict = {}
        if version is not None:
            data["version"] = version
        if environments is None:
            linux_url = (
                "https://conda.anaconda.org/conda-forge/linux-64/"
                "python-3.12.0-hab00c5b_0.conda"
            )
            osx_url = (
                "https://conda.anaconda.org/conda-forge/osx-arm64/"
                "python-3.12.0-h47c9636_0.conda"
            )
            environments = {
                "default": {
                    "channels": [
                        {"url": "https://conda.anaconda.org/conda-forge/"},
                    ],
                    "packages": {
                        "linux-64": [
                            {
                                "conda": linux_url,
                            },
                        ],
                        "osx-arm64": [
                            {
                                "conda": osx_url,
                            },
                        ],
                    },
                },
            }
            if packages is None:
                packages = [
                    {"conda": linux_url, "sha256": "a" * 64},
                    {"conda": osx_url, "sha256": "b" * 64},
                ]
        data["environments"] = environments
        data["packages"] = packages or []
        return data

    return _factory


@pytest.fixture
def satisfiability_config_factory(tmp_path: Path):
    """Factory that builds a real ``WorkspaceConfig`` for satisfiability tests.

    Uses ``monkeypatch.setattr`` recording closures internally so that
    ``merged_conda_dependencies`` and ``merged_channels`` return the
    requested values without needing a full manifest file.
    """

    def _factory(
        *,
        platforms: list[str] | None = None,
        environments: dict[str, list[str]] | None = None,
        deps: dict[str, MatchSpec] | None = None,
        channels: list[str] | None = None,
    ) -> WorkspaceConfig:
        if platforms is None:
            platforms = ["linux-64", "osx-arm64"]
        if environments is None:
            environments = {"default": []}
        if deps is None:
            deps = {"python": MatchSpec("python>=3.10")}
        if channels is None:
            channels = ["conda-forge"]

        features = {Feature.DEFAULT_NAME: Feature(name=Feature.DEFAULT_NAME)}
        env_objs = {}
        for name, feat_names in environments.items():
            for fn in feat_names:
                if fn not in features:
                    features[fn] = Feature(name=fn)
            env_objs[name] = Environment(name=name, features=feat_names)

        config = WorkspaceConfig(
            name="sat-test",
            channels=[Channel(c) for c in channels],
            platforms=platforms,
            features=features,
            environments=env_objs,
            root=str(tmp_path),
            manifest_path=str(tmp_path / "pixi.toml"),
        )

        def _patched_deps(env, platform=None):
            return deps

        def _patched_channels(env):
            return [Channel(c) for c in channels]

        config.merged_conda_dependencies = _patched_deps  # type: ignore[assignment]
        config.merged_channels = _patched_channels  # type: ignore[assignment]

        return config

    return _factory


@pytest.mark.parametrize(
    ("config_kwargs", "data_kwargs", "expected_ok", "reason_fragment"),
    [
        pytest.param(
            {},
            {},
            True,
            "",
            id="all-specs-matched",
        ),
        pytest.param(
            {"environments": {"default": [], "test": []}},
            {},
            False,
            "test",
            id="missing-environment",
        ),
        pytest.param(
            {"platforms": ["linux-64", "osx-arm64", "win-64"]},
            {},
            False,
            "win-64",
            id="missing-platform",
        ),
        pytest.param(
            {"channels": ["defaults"]},
            {},
            False,
            "channel",
            id="channel-mismatch",
        ),
        pytest.param(
            {
                "deps": {
                    "python": MatchSpec("python>=3.10"),
                    "numpy": MatchSpec("numpy"),
                },
            },
            {},
            False,
            "numpy",
            id="missing-dependency",
        ),
        pytest.param(
            {"deps": {"python": MatchSpec("python>=3.14")}},
            {},
            False,
            "python",
            id="version-mismatch",
        ),
        pytest.param(
            {},
            {"version": None},
            False,
            "version",
            id="lockfile-missing-version",
        ),
    ],
)
def test_satisfiability(
    satisfiability_config_factory,
    lockfile_data_factory,
    config_kwargs: dict,
    data_kwargs: dict,
    expected_ok: bool,
    reason_fragment: str,
) -> None:
    config = satisfiability_config_factory(**config_kwargs)
    data = lockfile_data_factory(**data_kwargs)
    result = check_lockfile_satisfiability(config, data, "linux-64")
    if expected_ok:
        assert result.status == LockfileStatus.UP_TO_DATE
        assert result.reason == ""
    else:
        assert result.status == LockfileStatus.OUT_OF_DATE
        assert reason_fragment in result.reason.lower()


def test_satisfiability_ignores_extra_lockfile_envs(
    satisfiability_config_factory, lockfile_data_factory
) -> None:
    config = satisfiability_config_factory()
    data = lockfile_data_factory()
    data["environments"]["extra"] = {
        "channels": [
            {"url": "https://conda.anaconda.org/conda-forge/"},
        ],
        "packages": {
            "linux-64": [
                {
                    "conda": "https://conda.anaconda.org/conda-forge/linux-64/python-3.12.0-hab00c5b_0.conda"
                },
            ],
        },
    }
    result = check_lockfile_satisfiability(config, data, "linux-64")
    assert result.status == LockfileStatus.UP_TO_DATE
    assert result.reason == ""
