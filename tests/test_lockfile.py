"""Tests for conda_workspaces.lockfile."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from conda_workspaces.context import WorkspaceContext
from conda_workspaces.exceptions import LockfileNotFoundError
from conda_workspaces.lockfile import (
    LOCKFILE_NAME,
    LOCKFILE_VERSION,
    _build_lockfile_dict,
    _extract_env_packages,
    _record_to_dict,
    install_from_lockfile,
    lockfile_exists,
    lockfile_path,
)
from conda_workspaces.models import Channel, Environment, Feature, WorkspaceConfig


def _make_ctx(
    tmp_path: Path,
    platform: str = "linux-64",
    env_names: list[str] | None = None,
) -> WorkspaceContext:
    """Build a workspace context rooted at *tmp_path*."""
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


def test_lockfile_path_returns_conda_lock(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    assert lockfile_path(ctx) == tmp_path / LOCKFILE_NAME


def test_lockfile_exists_false(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    assert lockfile_exists(ctx) is False


def test_lockfile_exists_true(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    (tmp_path / LOCKFILE_NAME).write_text("version: 1\n", encoding="utf-8")
    assert lockfile_exists(ctx) is True


def test_record_to_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """_record_to_dict extracts url plus non-empty metadata fields."""

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


def test_build_lockfile_dict() -> None:
    """_build_lockfile_dict produces correct lockfile structure."""

    class FakePkg:
        def __init__(self, name: str, url: str):
            self.name = name
            self.url = url
            self._data: dict = {}

        def get(self, key, default=None):
            return self._data.get(key, default)

    pkg_a = FakePkg("numpy", "https://example.com/numpy-1.24.conda")
    pkg_b = FakePkg("python", "https://example.com/python-3.10.conda")
    pkg_c = FakePkg("pytest", "https://example.com/pytest-8.0.conda")

    environments = {
        ("default", "linux-64"): [pkg_a, pkg_b],
        ("test", "linux-64"): [pkg_a, pkg_b, pkg_c],
    }
    channels_by_env = {
        "default": ["conda-forge"],
        "test": ["conda-forge"],
    }

    result = _build_lockfile_dict(environments, channels_by_env)  # type: ignore[arg-type]

    assert result["version"] == LOCKFILE_VERSION
    assert "default" in result["environments"]
    assert "test" in result["environments"]

    assert len(result["environments"]["default"]["packages"]["linux-64"]) == 2
    assert len(result["environments"]["test"]["packages"]["linux-64"]) == 3

    assert len(result["packages"]) == 3

    assert result["environments"]["default"]["channels"] == [{"url": "conda-forge"}]


def test_extract_env_packages() -> None:
    data = {
        "version": 1,
        "environments": {
            "default": {
                "channels": [{"url": "conda-forge"}],
                "packages": {
                    "linux-64": [
                        {"conda": "https://example.com/python.conda"},
                        {"conda": "https://example.com/numpy.conda"},
                    ],
                },
            },
        },
        "packages": [
            {"conda": "https://example.com/python.conda", "sha256": "aaa"},
            {"conda": "https://example.com/numpy.conda", "sha256": "bbb"},
        ],
    }

    pairs = _extract_env_packages(data, "default", "linux-64")
    assert len(pairs) == 2
    expected_0 = (
        "https://example.com/python.conda",
        {"conda": "https://example.com/python.conda", "sha256": "aaa"},
    )
    expected_1 = (
        "https://example.com/numpy.conda",
        {"conda": "https://example.com/numpy.conda", "sha256": "bbb"},
    )
    assert pairs[0] == expected_0
    assert pairs[1] == expected_1


def test_extract_env_packages_missing_env() -> None:
    data = {"version": 1, "environments": {}, "packages": []}
    with pytest.raises(LockfileNotFoundError, match="no-such-env"):
        _extract_env_packages(data, "no-such-env", "linux-64")


def test_extract_env_packages_missing_platform() -> None:
    data = {
        "version": 1,
        "environments": {
            "default": {
                "channels": [],
                "packages": {"linux-64": []},
            },
        },
        "packages": [],
    }
    with pytest.raises(LockfileNotFoundError):
        _extract_env_packages(data, "default", "osx-arm64")


def test_generate_lockfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_lockfile solves and writes conda.lock."""
    from conda_workspaces.lockfile import generate_lockfile
    from conda_workspaces.resolver import ResolvedEnvironment

    ctx = _make_ctx(tmp_path, env_names=["default", "test"])

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
    assert "version: 1" in content
    assert "default" in content
    assert "test" in content
    assert "https://example.com/python.conda" in content
    assert "https://example.com/pytest.conda" in content


def test_generate_lockfile_specific_envs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_lockfile with only one env generates only for that env."""
    from conda_workspaces.lockfile import generate_lockfile
    from conda_workspaces.resolver import ResolvedEnvironment

    ctx = _make_ctx(tmp_path, env_names=["default", "test"])

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


def test_install_from_lockfile_missing(tmp_path: Path) -> None:
    """install_from_lockfile raises LockfileNotFoundError when no conda.lock."""
    ctx = _make_ctx(tmp_path)
    with pytest.raises(LockfileNotFoundError):
        install_from_lockfile(ctx, "default")


def test_install_from_lockfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install_from_lockfile reads conda.lock, extracts URLs, and installs."""
    ctx = _make_ctx(tmp_path)

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
