"""Tests for conda_workspaces.lockfile."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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


def test_generate_lockfile(
    tmp_path: Path,
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_lockfile solves and writes conda.lock."""
    ctx = workspace_ctx_factory(env_names=["default", "test"])

    class FakePkg:
        def __init__(self, name: str, url: str):
            self.name = name
            self.url = url

        def get(self, key, default=None):
            return default

    def fake_solve(ctx_arg, resolved):
        pkgs = [FakePkg("python", "https://example.com/python.conda")]
        if resolved.name == "test":
            pkgs.append(FakePkg("pytest", "https://example.com/pytest.conda"))
        return pkgs

    monkeypatch.setattr("conda_workspaces.lockfile._solve_for_records", fake_solve)

    resolved_envs = {
        "default": ResolvedEnvironment(
            name="default",
            channels=[Channel("conda-forge")],
            platforms=["linux-64"],
        ),
        "test": ResolvedEnvironment(
            name="test",
            channels=[Channel("conda-forge")],
            platforms=["linux-64"],
        ),
    }

    result = generate_lockfile(ctx, resolved_envs)
    assert result == tmp_path / LOCKFILE_NAME
    assert result.is_file()

    content = result.read_text(encoding="utf-8")
    assert f"version: {LOCKFILE_VERSION}" in content
    assert "default" in content
    assert "test" in content
    assert "https://example.com/python.conda" in content
    assert "https://example.com/pytest.conda" in content


def test_generate_lockfile_specific_envs(
    workspace_ctx_factory: Callable[..., WorkspaceContext],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_lockfile with only one env generates only for that env."""
    ctx = workspace_ctx_factory(env_names=["default", "test"])

    monkeypatch.setattr(
        "conda_workspaces.lockfile._solve_for_records",
        lambda ctx_arg, resolved: [],
    )

    resolved_envs = {
        "default": ResolvedEnvironment(
            name="default",
            channels=[Channel("conda-forge")],
            platforms=["linux-64"],
        ),
    }

    result = generate_lockfile(ctx, resolved_envs)
    content = result.read_text(encoding="utf-8")
    assert "default" in content


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
