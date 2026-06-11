"""Tests for conda_workspaces.models."""

from __future__ import annotations

import pytest

from conda_workspaces.exceptions import (
    EnvironmentNotFoundError,
    FeatureNotFoundError,
    PlatformError,
    TaskNotFoundError,
)
from conda_workspaces.models import (
    ArchiveConfig,
    Channel,
    Environment,
    Feature,
    MatchSpec,
    PyPIDependency,
    Task,
    TaskArg,
    TaskDependency,
    TaskOverride,
    WorkspaceConfig,
)


@pytest.mark.parametrize(
    "spec_str, expected_name, expected_version",
    [
        ("numpy >=1.24", "numpy", ">=1.24"),
        ("numpy", "numpy", None),
        ("python >=3.10,<4", "python", ">=3.10,<4"),
        ("scipy *", "scipy", "*"),
    ],
    ids=["with-version", "no-version", "compound-version", "wildcard"],
)
def test_matchspec(spec_str, expected_name, expected_version):
    ms = MatchSpec(spec_str)
    assert ms.name == expected_name
    if expected_version is None:
        assert ms.version is None
    else:
        assert str(ms.version) == expected_version


@pytest.mark.parametrize(
    "name, spec, expected",
    [
        ("requests", ">=2.28", "requests>=2.28"),
        ("requests", "", "requests"),
        ("flask", ">=2.0,<3", "flask>=2.0,<3"),
    ],
    ids=["with-spec", "no-spec", "compound-spec"],
)
def test_pypi_dep_str(name, spec, expected):
    dep = PyPIDependency(name=name, spec=spec) if spec else PyPIDependency(name=name)
    assert str(dep) == expected


@pytest.mark.parametrize(
    "value, expected_canonical",
    [
        ("conda-forge", "conda-forge"),
        ("bioconda", "bioconda"),
        ("https://my.server/channel", "https://my.server/channel"),
    ],
    ids=["short-name", "other-name", "full-url"],
)
def test_channel(value, expected_canonical):
    ch = Channel(value)
    assert ch.canonical_name == expected_canonical


@pytest.mark.parametrize(
    "name, expected_default",
    [
        ("default", True),
        ("test", False),
        ("docs", False),
    ],
    ids=["default", "test", "docs"],
)
def test_feature_is_default(name, expected_default):
    f = Feature(name=name)
    assert f.is_default is expected_default


@pytest.mark.parametrize(
    "name, features, expected_default",
    [
        ("default", [], True),
        ("test", ["test"], False),
        ("docs", ["docs"], False),
    ],
    ids=["default", "test", "docs"],
)
def test_environment(name, features, expected_default):
    env = Environment(name=name, features=features)
    assert env.is_default is expected_default


def test_config_post_init_creates_defaults():
    config = WorkspaceConfig()
    assert "default" in config.features
    assert "default" in config.environments


def test_config_get_environment(sample_config):
    env = sample_config.get_environment("test")
    assert env.name == "test"
    assert "test" in env.features


def test_config_get_environment_not_found(sample_config):
    with pytest.raises(EnvironmentNotFoundError):
        sample_config.get_environment("nonexistent")


@pytest.mark.parametrize(
    "env_name, expected_names",
    [
        ("default", ["default"]),
        ("test", ["default", "test"]),
    ],
    ids=["default-only", "test-inherits-default"],
)
def test_config_resolve_features(sample_config, env_name, expected_names):
    env = sample_config.environments[env_name]
    features = sample_config.resolve_features(env)
    names = [f.name for f in features]
    for name in expected_names:
        assert name in names
    assert len(features) == len(expected_names)


def test_config_resolve_features_no_default():
    config = WorkspaceConfig(
        features={
            "default": Feature(name="default"),
            "standalone": Feature(name="standalone"),
        },
        environments={
            "default": Environment(name="default"),
            "isolated": Environment(
                name="isolated",
                features=["standalone"],
                no_default_feature=True,
            ),
        },
    )
    env = config.environments["isolated"]
    features = config.resolve_features(env)
    names = [f.name for f in features]
    assert "default" not in names
    assert "standalone" in names


def test_config_merged_conda_dependencies(sample_config):
    env = sample_config.environments["test"]
    merged = sample_config.merged_conda_dependencies(env)
    assert "python" in merged  # from default
    assert "numpy" in merged  # from default
    assert "pytest" in merged  # from test feature


def test_config_merged_channels(sample_config):
    env = sample_config.environments["test"]
    channels = sample_config.merged_channels(env)
    assert len(channels) >= 1
    assert channels[0].canonical_name == "conda-forge"


def test_config_merged_channels_deduplication():
    feat_a = Feature(name="a", channels=[Channel("conda-forge")])
    config = WorkspaceConfig(
        channels=[Channel("conda-forge")],
        features={"default": Feature(name="default"), "a": feat_a},
        environments={
            "default": Environment(name="default"),
            "env": Environment(name="env", features=["a"]),
        },
    )
    env = config.environments["env"]
    channels = config.merged_channels(env)
    canonical = [ch.canonical_name for ch in channels]
    assert canonical.count("conda-forge") == 1


def test_config_post_init_invalid_platform():
    with pytest.raises(PlatformError):
        WorkspaceConfig(platforms=["not-a-real-platform"])


def test_config_resolve_features_unknown_feature():
    config = WorkspaceConfig(
        features={"default": Feature(name="default")},
        environments={
            "default": Environment(name="default"),
            "broken": Environment(name="broken", features=["nonexistent"]),
        },
    )
    with pytest.raises(FeatureNotFoundError):
        config.resolve_features(config.environments["broken"])


def test_config_merged_conda_deps_with_target():
    default_feat = Feature(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.10")},
        target_conda_dependencies={
            "linux-64": {"gcc": MatchSpec("gcc >=12")},
        },
    )
    config = WorkspaceConfig(
        features={"default": default_feat},
        environments={"default": Environment(name="default")},
    )
    env = config.environments["default"]
    merged = config.merged_conda_dependencies(env, platform="linux-64")
    assert "python" in merged
    assert "gcc" in merged


def test_config_merged_pypi_deps_with_target():
    default_feat = Feature(
        name="default",
        pypi_dependencies={"requests": PyPIDependency(name="requests", spec=">=2.28")},
        target_pypi_dependencies={
            "linux-64": {"uvloop": PyPIDependency(name="uvloop", spec=">=0.17")},
        },
    )
    config = WorkspaceConfig(
        features={"default": default_feat},
        environments={"default": Environment(name="default")},
    )
    env = config.environments["default"]
    merged = config.merged_pypi_dependencies(env, platform="linux-64")
    assert "requests" in merged
    assert "uvloop" in merged


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        pytest.param("linux-64", {"cuda": "12.0", "glibc": "2.28"}, id="override"),
        pytest.param("osx-arm64", {"cuda": "12.0", "glibc": "2.17"}, id="fallback"),
    ],
)
def test_config_merged_system_requirements_with_platform(
    platform: str,
    expected: dict[str, str],
) -> None:
    default_feat = Feature(
        name="default",
        system_requirements={"cuda": "12.0", "glibc": "2.17"},
    )
    config = WorkspaceConfig(
        platform_system_requirements={"linux-64": {"glibc": "2.28"}},
        features={"default": default_feat},
        environments={"default": Environment(name="default")},
    )
    env = config.environments["default"]

    assert config.merged_system_requirements(env, platform=platform) == expected


def test_config_merged_channels_feature_adds_new():
    default_feat = Feature(name="default")
    extra_feat = Feature(name="bio", channels=[Channel("bioconda")])
    config = WorkspaceConfig(
        channels=[Channel("conda-forge")],
        features={"default": default_feat, "bio": extra_feat},
        environments={
            "default": Environment(name="default"),
            "bio": Environment(name="bio", features=["bio"]),
        },
    )
    env = config.environments["bio"]
    channels = config.merged_channels(env)
    canonical = [ch.canonical_name for ch in channels]
    assert "conda-forge" in canonical
    assert "bioconda" in canonical
    assert len(canonical) == 2


@pytest.mark.parametrize(
    ("default", "expected"),
    [
        (None, None),
        ("tests/", "tests/"),
    ],
    ids=["required", "optional"],
)
def test_task_arg(default, expected):
    arg = TaskArg(name="path", default=default) if default else TaskArg(name="path")
    assert arg.name == "path"
    assert arg.default == expected


def test_task_dependency_simple():
    dep = TaskDependency(task="build")
    assert dep.task == "build"
    assert dep.args == []
    assert dep.environment is None


def test_task_dependency_with_args_and_env():
    dep = TaskDependency(task="test", args=["src/"], environment="py311")
    assert dep.args == ["src/"]
    assert dep.environment == "py311"


@pytest.mark.parametrize(
    ("name", "expected_hidden"),
    [
        ("build", False),
        ("_internal", True),
        ("__double", True),
        ("visible", False),
    ],
)
def test_task_is_hidden(name, expected_hidden):
    task = Task(name=name, cmd="echo x")
    assert task.is_hidden is expected_hidden


def test_task_simple_command():
    task = Task(name="build", cmd="make")
    assert task.cmd == "make"
    assert not task.is_alias


def test_task_alias(alias_task):
    assert alias_task.is_alias
    assert alias_task.cmd is None


def test_task_list_command():
    task = Task(name="build", cmd=["python", "-m", "build"])
    assert task.cmd == ["python", "-m", "build"]


def test_task_env_vars():
    task = Task(name="test", cmd="pytest", env={"PYTHONPATH": "src"})
    assert task.env == {"PYTHONPATH": "src"}


@pytest.mark.parametrize(
    ("kwargs", "attr", "expected"),
    [
        ({"cmd": "nmake"}, "cmd", "nmake"),
        ({"env": {"CC": "gcc"}}, "env", {"CC": "gcc"}),
        ({"cwd": "/tmp"}, "cwd", "/tmp"),
        ({"clean_env": True}, "clean_env", True),
    ],
)
def test_task_override(kwargs, attr, expected):
    ov = TaskOverride(**kwargs)
    assert getattr(ov, attr) == expected


@pytest.mark.parametrize(
    "platform",
    ["linux-64", "linux-aarch64"],
    ids=["no-platforms", "no-match"],
)
def test_resolve_returns_self_when_no_override(platform, simple_task):
    resolved = simple_task.resolve_for_platform(platform)
    assert resolved is simple_task


@pytest.mark.parametrize(
    ("platform", "expected_cmd", "expected_env"),
    [
        ("win-64", "rd /s /q build", {}),
        ("osx-arm64", "rm -rf build/", {"MACOSX_DEPLOYMENT_TARGET": "11.0"}),
    ],
)
def test_resolve_platform_override(
    task_with_overrides, platform, expected_cmd, expected_env
):
    resolved = task_with_overrides.resolve_for_platform(platform)
    assert resolved is not task_with_overrides
    assert resolved.name == "clean"
    assert resolved.cmd == expected_cmd
    assert resolved.env == expected_env


@pytest.mark.parametrize(
    ("available", "expected_in", "expected_not_in"),
    [
        (["a", "b", "c"], "a, b, c", None),
        (None, None, "Available"),
    ],
    ids=["with-available", "no-available"],
)
def test_task_not_found_error(available, expected_in, expected_not_in):
    if available:
        err = TaskNotFoundError("missing", available)
    else:
        err = TaskNotFoundError("missing")
    assert "missing" in str(err)
    if expected_in:
        assert expected_in in str(err)
    if expected_not_in:
        assert expected_not_in not in str(err)


def test_archive_config_defaults():
    cfg = ArchiveConfig()
    assert cfg.include == ()
    assert cfg.exclude == ()
    assert cfg.compression == "zst"
    assert cfg.compression_level is None


def test_archive_config_custom():
    cfg = ArchiveConfig(
        include=("src/**",),
        exclude=("data/**", "*.bin"),
        compression="gz",
        compression_level=6,
    )
    assert cfg.include == ("src/**",)
    assert cfg.exclude == ("data/**", "*.bin")
    assert cfg.compression == "gz"
    assert cfg.compression_level == 6
