"""Tests for conda_workspaces.models."""

from __future__ import annotations

from dataclasses import asdict, fields

import pytest

from conda_workspaces.exceptions import (
    EnvironmentNameInvalidError,
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
    has_url_credentials,
    redact_channel_name,
    redact_channel_url,
    redact_url,
    redact_url_text,
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
    ("field", "value"),
    [
        ("path", "https://user:LEAKME@packages.example.test/private.whl"),
        ("url", "https://user%3ALEAKME%40packages.example.test/private.whl"),
        (
            "git",
            "https%3A%2F%2Fuser%3ALEAKME%40packages.example.test%2Frepository.git",
        ),
        ("path", "https://packages.example.test/path%3Ftoken=LEAKME"),
        ("spec", "@ https://user%3ALEAKME%40packages.example.test/private.whl"),
        ("path", "t/INFO-LEAK/private"),
    ],
    ids=[
        "basic-auth-path",
        "encoded-authority-url",
        "fully-encoded-git",
        "encoded-query-path",
        "encoded-authority-spec",
        "relative-token-path",
    ],
)
def test_pypi_dependency_rejects_credentials_without_echoing(
    field: str,
    value: str,
) -> None:
    dependency = PyPIDependency(name="private", **{field: value})

    with pytest.raises(ValueError) as caught:
        dependency.to_manifest_toml()

    assert "LEAKME" not in str(caught.value)
    assert "LEAKME" not in str(dependency.redacted())


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "HTTPS://user:password@packages.example.test/team/channel",
            "HTTPS://packages.example.test/team/channel",
        ),
        (
            "//user:password@packages.example.test/team/channel?token=secret",
            "//packages.example.test/team/channel",
        ),
        (
            "https://packages.example.test/%74/secret/team/channel",
            "https://packages.example.test/team/channel",
        ),
        (
            "https://packages.example.test/t%2Fsecret/team/channel",
            "https://packages.example.test/team/channel",
        ),
        (
            "https://packages.example.test/t%252Fsecret/team/channel",
            "https://packages.example.test/team/channel",
        ),
        (
            "https://packages.example.test/t/secret%2FLEAK/team/channel",
            "https://packages.example.test/team/channel",
        ),
        (
            "https://packages.example.test/%252525252574/secret/team/channel",
            "<redacted-url>",
        ),
        (
            "https://conda.anaconda.org/HTTPS://user:password@packages.example.test/t/secret/private",
            "<redacted-url>",
        ),
        ("https:///missing-host?token=secret", "<redacted-url>"),
        ("HTTPS://", "<redacted-url>"),
        ("//", "<redacted-url>"),
        ("//?token=secret", "<redacted-url>"),
        ("https://packages.example.test:bad/channel", "<redacted-url>"),
        ("//packages.example.test:bad/channel", "<redacted-url>"),
        (r"https://packages.example.test\evil/channel", "<redacted-url>"),
        (
            "git+HTTPS://user:password@packages.example.test/team/repo.git?key=value#ref",
            "git+HTTPS://packages.example.test/team/repo.git",
        ),
        (
            "https://user%3ALEAKME%40packages.example.test/team/channel",
            "<redacted-url>",
        ),
        (
            "//user%253ALEAKME%2540packages.example.test/team/channel",
            "<redacted-url>",
        ),
        (
            "https%3A%2F%2Fuser%3ALEAKME%40packages.example.test%2Fteam%2Fchannel",
            "<redacted-url>",
        ),
        (
            "https%253A%252F%252Fuser%253ALEAKME%2540packages.example.test%252Fchannel",
            "<redacted-url>",
        ),
        (
            "https://packages.example.test/path%3Ftoken=LEAKME",
            "<redacted-url>",
        ),
        (
            "https://packages.example.test/path%2523LEAKME",
            "<redacted-url>",
        ),
        (
            "https://packages.example.test/safe%20path",
            "https://packages.example.test/safe%20path",
        ),
        (
            "https%3A%2F%2Fpackages.example.test%2Fsafe%2520path",
            "https%3A%2F%2Fpackages.example.test%2Fsafe%2520path",
        ),
        (
            "https://user:password@packages.example.test/safe%20path",
            "https://packages.example.test/safe%20path",
        ),
        ("conda-forge", "conda-forge"),
    ],
    ids=[
        "uppercase-basic-auth",
        "scheme-relative-basic-auth",
        "encoded-token-segment",
        "encoded-token-separator",
        "double-encoded-token-separator",
        "encoded-token-value-separator",
        "excessive-encoding",
        "nested-authenticated-url",
        "missing-host",
        "empty-absolute-url",
        "empty-scheme-relative-url",
        "hostless-scheme-relative-url",
        "invalid-port",
        "scheme-relative-invalid-port",
        "backslash-host",
        "git-url",
        "encoded-authority",
        "double-encoded-scheme-relative-authority",
        "fully-encoded-authenticated-url",
        "double-encoded-authenticated-url",
        "encoded-query-delimiter",
        "double-encoded-fragment-delimiter",
        "safe-encoded-path",
        "safe-fully-encoded-url",
        "basic-auth-safe-encoded-path",
        "channel-name",
    ],
)
def test_redact_url(url: str, expected: str) -> None:
    redacted = redact_url(url)

    assert redacted == expected
    assert has_url_credentials(url) is (redacted != url)
    assert not has_url_credentials(redacted)


@pytest.mark.parametrize(
    ("value", "expected_name", "expected_url"),
    [
        (
            "conda-forge",
            "conda-forge",
            "https://conda.anaconda.org/conda-forge",
        ),
        (
            "t/SENSITIVE-VALUE/private",
            "https://conda.anaconda.org/private",
            "https://conda.anaconda.org/private",
        ),
        (
            "//user:password@packages.example.test/t/SENSITIVE-VALUE/private",
            "https://packages.example.test/private",
            "https://packages.example.test/private",
        ),
        (
            " t/SENSITIVE-VALUE/private",
            "<redacted-url-value>",
            "<redacted-url-value>",
        ),
        (
            '"t/SENSITIVE-VALUE/private"',
            "<redacted-url-value>",
            "<redacted-url-value>",
        ),
    ],
    ids=[
        "named",
        "relative-token",
        "scheme-relative-auth",
        "leading-whitespace-relative-token",
        "quoted-relative-token",
    ],
)
def test_redact_channel_name_and_url(
    value: str,
    expected_name: str,
    expected_url: str,
) -> None:
    channel = Channel(value)

    assert redact_channel_name(value) == expected_name
    assert redact_channel_name(channel) == expected_name
    assert redact_channel_url(channel) == expected_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://packages.example.test/channel", False),
        ("https://user:password@packages.example.test/channel", True),
        ("https://packages.example.test/t/token/channel", True),
        ("https://packages.example.test/channel?token=secret", True),
        ("download https:///missing-host", True),
        ("t/relative-token/private", True),
        ("nested/t/relative-token/private", True),
        ("curl -fsSL t/relative-token/private/file.txt", True),
        (r"t\relative-token\private", True),
        (r"nested\t\relative-token\private", True),
        ("t%2Frelative-token%2Fprivate", True),
        ("t%5Crelative-token%5Cprivate", True),
        ("t%252Frelative-token%252Fprivate", True),
        ("plain manifest value", False),
    ],
    ids=[
        "safe-url",
        "basic-auth",
        "token-path",
        "query",
        "embedded-malformed-url",
        "relative-channel-token",
        "nested-relative-channel-token",
        "relative-token-in-task-command",
        "windows-relative-channel-token",
        "nested-windows-relative-channel-token",
        "encoded-relative-channel-token",
        "encoded-windows-relative-channel-token",
        "double-encoded-relative-channel-token",
        "plain-value",
    ],
)
def test_has_url_credentials(value: str, expected: bool) -> None:
    assert has_url_credentials(value) is expected


def test_redact_url_text_fails_closed_for_later_relative_token() -> None:
    value = (
        "failed https://user:ABSOLUTE-LEAK@packages.example.test/private "
        "then t/RELATIVE-LEAK/private"
    )

    redacted = redact_url_text(value)

    assert redacted == "<redacted-url-value>"
    assert "ABSOLUTE-LEAK" not in redacted
    assert "RELATIVE-LEAK" not in redacted
    assert not has_url_credentials(redacted)


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


def test_config_manifest_generation_is_not_a_public_dataclass_field() -> None:
    config = WorkspaceConfig()
    config._manifest_text = "https://user:password@example.test/private"

    assert "_manifest_text" not in {item.name for item in fields(config)}
    assert "_accepted_manifest_text" not in repr(asdict(config))
    assert "password" not in repr(asdict(config))


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "bad?name",
        "bad\x7fname",
        "bad\x85name",
        "a" * 256,
    ],
    ids=[
        "parent",
        "windows-reserved-character",
        "del-control",
        "c1-control",
        "component-too-long",
    ],
)
def test_config_rejects_invalid_environment_names(name: str) -> None:
    with pytest.raises(EnvironmentNameInvalidError, match="not valid"):
        WorkspaceConfig(environments={name: Environment(name=name)})


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Dev", "dev"),
        (
            "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
            "caf\N{LATIN SMALL LETTER E}\N{COMBINING ACUTE ACCENT}",
        ),
    ],
    ids=["case", "unicode-normalization"],
)
def test_config_rejects_portable_environment_name_collisions(
    first: str,
    second: str,
) -> None:
    with pytest.raises(EnvironmentNameInvalidError, match="conflicts with environment"):
        WorkspaceConfig(
            environments={
                first: Environment(name=first),
                second: Environment(name=second),
            }
        )


@pytest.mark.parametrize(
    ("subdir", "requirements", "expected"),
    [
        pytest.param(
            "linux-64",
            {"cuda": "12.0", "glibc": "2.28"},
            "linux-64-cuda-12-0",
            id="linux-default-glibc-elided",
        ),
        pytest.param(
            "osx-arm64",
            {"osx": "13.0"},
            "osx-arm64",
            id="osx-default-elided",
        ),
        pytest.param(
            "linux-aarch64",
            {"archspec": "armv8-a", "__custom": "1.2"},
            "linux-aarch64-archspec-armv8-a-custom-1-2",
            id="archspec-and-raw-virtual",
        ),
    ],
)
def test_config_synthesize_platform_name(
    subdir: str,
    requirements: dict[str, str],
    expected: str,
) -> None:
    assert WorkspaceConfig.synthesize_platform_name(subdir, requirements) == expected


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
    env.conda_dependencies["coverage"] = MatchSpec("coverage >=7")
    merged = sample_config.merged_conda_dependencies(env)
    assert "python" in merged  # from default
    assert "numpy" in merged  # from default
    assert "pytest" in merged  # from test feature
    assert "coverage" in merged  # from the environment


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
            "linux-64": {
                "gcc": MatchSpec("gcc >=12"),
                "python": MatchSpec("python >=3.11"),
            },
        },
    )
    environment = Environment(
        name="default",
        conda_dependencies={"python": MatchSpec("python >=3.12")},
        target_conda_dependencies={
            "linux-64": {"python": MatchSpec("python >=3.13")},
            "linux-64-cuda": {"python": MatchSpec("python >=3.14")},
        },
    )
    config = WorkspaceConfig(
        platforms=["linux-64-cuda"],
        platform_subdirs={"linux-64-cuda": "linux-64"},
        features={"default": default_feat},
        environments={"default": environment},
    )
    env = config.environments["default"]
    merged = config.merged_conda_dependencies(env, platform="linux-64-cuda")
    assert str(merged["python"].version) == ">=3.14"
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
        platforms=["linux-64-cuda"],
        platform_subdirs={"linux-64-cuda": "linux-64"},
        features={"default": default_feat},
        environments={
            "default": Environment(
                name="default",
                pypi_dependencies={
                    "httpx": PyPIDependency(name="httpx", spec=">=0.28"),
                    "requests": PyPIDependency(name="requests", spec=">=2.29"),
                },
                target_pypi_dependencies={
                    "linux-64": {
                        "requests": PyPIDependency(
                            name="requests",
                            spec=">=2.30",
                        )
                    },
                    "linux-64-cuda": {
                        "requests": PyPIDependency(
                            name="requests",
                            spec=">=2.31",
                        )
                    },
                },
            )
        },
    )
    env = config.environments["default"]
    merged = config.merged_pypi_dependencies(env, platform="linux-64-cuda")
    assert merged["requests"].spec == ">=2.31"
    assert "uvloop" in merged
    assert "httpx" in merged


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
