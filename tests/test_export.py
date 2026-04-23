"""Tests for conda_workspaces.export."""

from __future__ import annotations

from conda_workspaces.export import multiplatform_export
from conda_workspaces.lockfile import LOCKFILE_VERSION, CondaLockLoader


class FakeRecord:
    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self._data: dict = {}

    def get(self, key, default=None):
        return self._data.get(key, default)


class FakeConfig:
    def __init__(self, channels: tuple[str, ...]):
        self.channels = channels


class FakeEnvironment:
    def __init__(
        self,
        name: str,
        platform: str,
        channels: tuple[str, ...],
        packages: list[FakeRecord] | None = None,
        external: dict[str, list[str]] | None = None,
    ):
        self.name = name
        self.platform = platform
        self.config = FakeConfig(channels)
        self.explicit_packages = packages or []
        self.external_packages = external or {}


def test_compose_single_env() -> None:
    pkg = FakeRecord("python", "https://example.com/python-3.10.conda")
    env = FakeEnvironment(
        name="default",
        platform="linux-64",
        channels=("conda-forge",),
        packages=[pkg],
    )

    result = CondaLockLoader.compose([env])  # type: ignore[arg-type]

    assert result["version"] == LOCKFILE_VERSION
    assert "default" in result["environments"]
    assert len(result["packages"]) == 1
    assert result["packages"][0]["conda"] == "https://example.com/python-3.10.conda"

    refs = result["environments"]["default"]["packages"]["linux-64"]
    assert refs == [{"conda": "https://example.com/python-3.10.conda"}]


def test_compose_deduplicates_packages() -> None:
    """Same URL across environments appears only once in packages list."""
    pkg = FakeRecord("python", "https://example.com/python-3.10.conda")
    env1 = FakeEnvironment("default", "linux-64", ("conda-forge",), [pkg])
    env2 = FakeEnvironment("test", "linux-64", ("conda-forge",), [pkg])

    result = CondaLockLoader.compose([env1, env2])  # type: ignore[arg-type]

    assert len(result["packages"]) == 1
    assert "default" in result["environments"]
    assert "test" in result["environments"]


def test_compose_multiple_platforms() -> None:
    pkg_lin = FakeRecord("python", "https://example.com/python-linux.conda")
    pkg_mac = FakeRecord("python", "https://example.com/python-osx.conda")
    env_lin = FakeEnvironment("default", "linux-64", ("conda-forge",), [pkg_lin])
    env_mac = FakeEnvironment("default", "osx-arm64", ("conda-forge",), [pkg_mac])

    result = CondaLockLoader.compose([env_lin, env_mac])  # type: ignore[arg-type]

    pkgs_by_plat = result["environments"]["default"]["packages"]
    assert "linux-64" in pkgs_by_plat
    assert "osx-arm64" in pkgs_by_plat
    assert len(result["packages"]) == 2


def test_compose_includes_external_packages() -> None:
    env = FakeEnvironment(
        name="default",
        platform="linux-64",
        channels=("conda-forge",),
        external={"pip": ["https://pypi.org/simple/requests/"]},
    )

    result = CondaLockLoader.compose([env])  # type: ignore[arg-type]

    refs = result["environments"]["default"]["packages"]["linux-64"]
    assert {"pip": "https://pypi.org/simple/requests/"} in refs


def test_compose_no_name_defaults() -> None:
    """Environment with name=None gets filed under 'default'."""
    env = FakeEnvironment(None, "linux-64", ("conda-forge",))  # type: ignore[arg-type]

    result = CondaLockLoader.compose([env])  # type: ignore[arg-type]

    assert "default" in result["environments"]


def test_compose_channels() -> None:
    env = FakeEnvironment("default", "linux-64", ("conda-forge", "bioconda"))

    result = CondaLockLoader.compose([env])  # type: ignore[arg-type]

    channels = result["environments"]["default"]["channels"]
    assert channels == [{"url": "conda-forge"}, {"url": "bioconda"}]


def test_multiplatform_export_returns_yaml() -> None:
    pkg = FakeRecord("numpy", "https://example.com/numpy-1.24.conda")
    env = FakeEnvironment("default", "linux-64", ("conda-forge",), [pkg])

    yaml_str = multiplatform_export([env])  # type: ignore[arg-type]

    assert isinstance(yaml_str, str)
    assert "version: 1" in yaml_str
    assert "https://example.com/numpy-1.24.conda" in yaml_str
    assert "default" in yaml_str
    assert "linux-64" in yaml_str


def test_multiplatform_export_empty() -> None:
    yaml_str = multiplatform_export([])  # type: ignore[arg-type]

    assert "version: 1" in yaml_str
    assert "environments: {}" in yaml_str
    assert "packages: []" in yaml_str


def test_multiplatform_export_handles_str_subclass_keys() -> None:
    """``str`` subclass env/platform keys are coerced before YAML dump.

    ``ruamel.yaml`` dispatches representers by exact type, so a
    ``str`` subclass (e.g. ``tomlkit.items.String``) reaching the
    YAML writer as a dict key raises ``TypeError: Object of type ...
    is not YAML serializable``.  The parsers normalise at the
    boundary and the exporter is defensive; this test pins both
    behaviours so a regression in either layer fails loudly rather
    than at a user's first ``conda workspace lock`` invocation.
    """

    class FakeStr(str):
        pass

    pkg = FakeRecord("python", "https://example.com/python-3.10.conda")
    env = FakeEnvironment(
        name=FakeStr("default"),
        platform=FakeStr("linux-64"),
        channels=("conda-forge",),
        packages=[pkg],
    )

    yaml_str = multiplatform_export([env])  # type: ignore[arg-type]

    assert "default:" in yaml_str
    assert "linux-64:" in yaml_str
