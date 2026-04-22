"""Tests for conda_workspaces.resolver."""

from __future__ import annotations

import pytest

from conda_workspaces.exceptions import EnvironmentNotFoundError, PlatformError
from conda_workspaces.models import (
    Channel,
    Environment,
    Feature,
    MatchSpec,
    WorkspaceConfig,
)
from conda_workspaces.resolver import (
    known_platforms,
    resolve_all_environments,
    resolve_environment,
)


@pytest.mark.parametrize(
    "env_name, expected_deps",
    [
        ("default", ["python", "numpy"]),
        ("test", ["python", "numpy", "pytest"]),
        ("docs", ["python", "numpy", "sphinx"]),
    ],
    ids=["default", "test-inherits-default", "docs-inherits-default"],
)
def test_resolve_dependencies(sample_config, env_name, expected_deps):
    resolved = resolve_environment(sample_config, env_name)
    assert resolved.name == env_name
    for dep in expected_deps:
        assert dep in resolved.conda_dependencies


def test_resolve_not_found(sample_config):
    with pytest.raises(EnvironmentNotFoundError):
        resolve_environment(sample_config, "nonexistent")


@pytest.mark.parametrize(
    "platform, should_raise",
    [
        ("linux-64", False),
        ("osx-arm64", False),
        ("win-64", False),
        ("linux-aarch64", True),
    ],
    ids=["linux-valid", "osx-valid", "win-valid", "aarch64-invalid"],
)
def test_resolve_platform_validation(sample_config, platform, should_raise):
    if should_raise:
        with pytest.raises(PlatformError):
            resolve_environment(sample_config, "default", platform=platform)
    else:
        resolved = resolve_environment(sample_config, "default", platform=platform)
        assert resolved.name == "default"


def test_resolve_channels(sample_config):
    resolved = resolve_environment(sample_config, "default")
    assert len(resolved.channels) >= 1
    assert resolved.channels[0].canonical_name == "conda-forge"


def test_resolve_all(sample_config):
    all_resolved = resolve_all_environments(sample_config)
    assert set(all_resolved) == {"default", "test", "docs"}


@pytest.mark.parametrize(
    "feature_platforms, expected_platforms",
    [
        (
            {"default": ["linux-64", "osx-arm64"], "narrow": ["linux-64"]},
            ["linux-64"],
        ),
        (
            {"default": None},
            ["linux-64", "win-64"],
        ),
    ],
    ids=["intersection-narrows", "no-feature-platforms-uses-workspace"],
)
def test_resolve_platforms(feature_platforms, expected_platforms):
    """Feature platforms are intersected; fallback to workspace platforms."""
    features = {}
    for name, plats in feature_platforms.items():
        kwargs = {"name": name}
        if name == "default":
            kwargs["conda_dependencies"] = {"python": MatchSpec("python")}
        if plats is not None:
            kwargs["platforms"] = plats
        features[name] = Feature(**kwargs)

    env_name = list(feature_platforms.keys())[-1]
    extra_features = [k for k in feature_platforms if k != "default"]
    environments = {
        "default": Environment(name="default"),
    }
    if env_name != "default":
        environments[env_name] = Environment(name=env_name, features=extra_features)

    config = WorkspaceConfig(
        channels=[Channel("conda-forge")],
        platforms=["linux-64", "osx-arm64", "win-64"]
        if "narrow" in feature_platforms
        else ["linux-64", "win-64"],
        features=features,
        environments=environments,
    )
    resolved = resolve_environment(config, env_name)
    assert resolved.platforms == expected_platforms


@pytest.mark.parametrize(
    ("workspace_platforms", "feature_platforms", "env_features", "expected"),
    [
        pytest.param(
            ["linux-64", "osx-arm64"],
            {"default": None},
            {"default": []},
            {"linux-64", "osx-arm64"},
            id="no-feature-platforms-returns-workspace-set",
        ),
        pytest.param(
            ["linux-64", "osx-arm64"],
            {"default": None, "gpu": ["linux-64"]},
            {"default": [], "gpu": ["gpu"]},
            {"linux-64", "osx-arm64"},
            id="narrowing-feature-does-not-shrink-known-set",
        ),
        pytest.param(
            ["linux-64", "osx-arm64"],
            {"default": None, "windows": ["win-64"]},
            {"default": [], "windows": ["windows"]},
            {"linux-64", "osx-arm64", "win-64"},
            id="broadening-feature-adds-to-known-set",
        ),
        pytest.param(
            [],
            {"default": ["linux-64", "win-64"]},
            {"default": []},
            {"linux-64", "win-64"},
            id="features-supply-platforms-when-workspace-has-none",
        ),
    ],
)
def test_known_platforms(
    workspace_platforms: list[str],
    feature_platforms: dict[str, list[str] | None],
    env_features: dict[str, list[str]],
    expected: set[str],
) -> None:
    """``known_platforms`` unions workspace + reachable feature platforms."""
    features: dict[str, Feature] = {}
    for name, plats in feature_platforms.items():
        kwargs: dict[str, object] = {"name": name}
        if name == "default":
            kwargs["conda_dependencies"] = {"python": MatchSpec("python")}
        if plats is not None:
            kwargs["platforms"] = plats
        features[name] = Feature(**kwargs)

    environments = {
        env_name: Environment(
            name=env_name,
            features=[f for f in feats if f != "default"],
        )
        for env_name, feats in env_features.items()
    }

    config = WorkspaceConfig(
        channels=[Channel("conda-forge")],
        platforms=workspace_platforms,
        features=features,
        environments=environments,
    )
    resolved_envs = resolve_all_environments(config)
    assert known_platforms(config, resolved_envs.values()) == expected


def test_known_platforms_without_resolved_envs() -> None:
    """Degrades to workspace-only when ``resolved_envs`` is empty."""
    config = WorkspaceConfig(
        channels=[Channel("conda-forge")],
        platforms=["linux-64", "osx-arm64"],
        features={"default": Feature(name="default")},
    )
    assert known_platforms(config) == {"linux-64", "osx-arm64"}


def test_resolve_activation_merged():
    """Activation scripts and env vars are merged across features."""
    default_feat = Feature(
        name="default",
        activation_scripts=["base.sh"],
        activation_env={"BASE": "1"},
    )
    dev_feat = Feature(
        name="dev",
        activation_scripts=["dev.sh"],
        activation_env={"DEV": "1"},
    )
    config = WorkspaceConfig(
        channels=[Channel("conda-forge")],
        platforms=["linux-64"],
        features={"default": default_feat, "dev": dev_feat},
        environments={
            "default": Environment(name="default"),
            "dev": Environment(name="dev", features=["dev"]),
        },
    )
    resolved = resolve_environment(config, "dev")
    assert "base.sh" in resolved.activation_scripts
    assert "dev.sh" in resolved.activation_scripts
    assert resolved.activation_env == {"BASE": "1", "DEV": "1"}


def test_resolve_system_requirements_merged():
    """System requirements are merged across features."""
    default_feat = Feature(
        name="default",
        system_requirements={"glibc": "2.17"},
    )
    gpu_feat = Feature(
        name="gpu",
        system_requirements={"cuda": "12.0"},
    )
    config = WorkspaceConfig(
        channels=[Channel("conda-forge")],
        platforms=["linux-64"],
        features={"default": default_feat, "gpu": gpu_feat},
        environments={
            "default": Environment(name="default"),
            "gpu": Environment(name="gpu", features=["gpu"]),
        },
    )
    resolved = resolve_environment(config, "gpu")
    assert resolved.system_requirements == {"glibc": "2.17", "cuda": "12.0"}


@pytest.mark.parametrize(
    "priority, expected",
    [
        ("strict", "strict"),
        (None, None),
    ],
    ids=["explicit-strict", "default-none"],
)
def test_resolve_channel_priority(priority, expected):
    """channel_priority is propagated (or defaults to None)."""
    kwargs = {
        "channels": [Channel("conda-forge")],
        "platforms": ["linux-64"],
        "features": {"default": Feature(name="default")},
        "environments": {"default": Environment(name="default")},
    }
    if priority is not None:
        kwargs["channel_priority"] = priority
    config = WorkspaceConfig(**kwargs)
    resolved = resolve_environment(config, "default")
    assert resolved.channel_priority == expected
