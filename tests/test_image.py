"""Behavioral tests for workspace image preparation and Buildx execution."""

from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.exceptions import (
    ArchiveError,
    CondaWorkspacesError,
    EnvironmentNotFoundError,
    LockfileNotFoundError,
    LockfileStaleError,
    PlatformError,
)
from conda_workspaces.image import WorkspaceImage

if TYPE_CHECKING:
    from collections.abc import Callable

    from conda_workspaces.context import WorkspaceContext
    from conda_workspaces.models import WorkspaceConfig

    from .conftest import SnapshotTree


@pytest.mark.parametrize(
    "name",
    ["image-test", "image test"],
    ids=["plain", "spaces"],
)
def test_image_preview_preserves_workspace_and_command_argv(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    monkeypatch: pytest.MonkeyPatch,
    snapshot_tree: SnapshotTree,
    tmp_path: Path,
    name: str,
) -> None:
    config, ctx = image_workspace()
    config.name = name
    output = tmp_path / "result.oci.tar"
    command = ("python", "-c", 'print("hello")\nRUN touch unwanted', "--json")
    before = snapshot_tree(tmp_path)

    def no_docker(command: str) -> None:
        raise AssertionError(f"Preview must not look up {command}")

    monkeypatch.setattr("conda_workspaces.image.shutil.which", no_docker)
    image = WorkspaceImage.prepare(
        config,
        ctx,
        environment="default",
        platform="linux-64",
        command=command,
        output=output,
    )
    preview = image.preview()

    recipe = str(preview["recipe"])
    cmd_line = next(line for line in recipe.splitlines() if line.startswith("CMD "))
    assert json.loads(cmd_line[4:]) == list(command)
    assert "\nRUN touch unwanted" not in recipe
    assert preview["oci_platform"] == "linux/amd64"
    assert preview["workspace"] == f"/workspaces/{name}"
    assert preview["prefix"] == f"/workspaces/{name}/.conda/envs/default"
    assert preview["files"] == ["app.py", "conda.lock", "conda.toml"]
    assert snapshot_tree(tmp_path) == before
    assert not output.exists()


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "../escape",
        "project\nRUN true",
        "image${PATH}",
        "image's workspace",
    ],
    ids=[
        "missing",
        "empty",
        "traversal",
        "newline",
        "dollar",
        "apostrophe",
    ],
)
def test_image_rejects_missing_or_unsafe_workspace_name(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
    name: str | None,
) -> None:
    config, ctx = image_workspace()
    config.name = name

    with pytest.raises(CondaWorkspacesError, match="workspace name"):
        WorkspaceImage.prepare(
            config,
            ctx,
            environment="default",
            platform="linux-64",
            command=("python", "app.py"),
            output=tmp_path / "result.oci.tar",
        )


@pytest.mark.skipif(not Path("/bin/bash").is_file(), reason="requires /bin/bash")
@pytest.mark.parametrize(
    "existing", ["absent", "directory", "file", "symlink"], ids=str
)
def test_image_rejects_occupied_workspace_in_base(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
    existing: str,
) -> None:
    config, ctx = image_workspace()
    image = WorkspaceImage.prepare(
        config,
        ctx,
        environment="default",
        platform="linux-64",
        command=("python", "app.py"),
        output=tmp_path / "result.oci.tar",
    )
    sources = image.recipe().split(" AS sources\n", 1)[1]
    command = json.loads(
        next(
            line for line in sources.splitlines() if line.startswith("RUN ")
        ).removeprefix("RUN ")
    )
    assert command[-1] == "/workspaces/image-test"
    root = tmp_path / "workspaces" / "image-test"
    sibling = root.parent / "other-project"
    sibling.mkdir(parents=True)
    (sibling / "application").write_text("preserve other workspace")
    command[-1] = str(root)
    if existing == "directory":
        root.mkdir()
        (root / "old-package").write_text("preserve")
    elif existing == "file":
        root.write_text("preserve")
    elif existing == "symlink":
        root.symlink_to(tmp_path / "missing-workspace", target_is_directory=True)

    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == (0 if existing == "absent" else 1)
    assert (sibling / "application").read_text() == "preserve other workspace"
    if existing != "absent":
        assert "Base image already contains" in result.stderr
    if existing == "directory":
        assert (root / "old-package").read_text() == "preserve"
    elif existing == "file":
        assert root.read_text() == "preserve"
    elif existing == "symlink":
        assert root.is_symlink()


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"command": ()}, "Provide an application command"),
        ({"command": ("python", "\0")}, "Provide an application command"),
        ({"output": None}, "Choose exactly one"),
        ({"load": True}, "Choose exactly one"),
        ({"output": None, "load": True}, "require at least one"),
        ({"output": None, "push": True}, "require at least one"),
        ({"base_image": "debian\nRUN touch unwanted"}, "Invalid container image"),
        ({"tags": ("--output=type=local,dest=unwanted",)}, "Invalid container image"),
        ({"builder": "--help"}, "Invalid Buildx builder"),
    ],
    ids=[
        "missing-command",
        "nul-command",
        "missing-destination",
        "multiple-destinations",
        "load-without-tag",
        "push-without-tag",
        "dockerfile-injection",
        "tag-option-injection",
        "builder-option-injection",
    ],
)
def test_image_rejects_invalid_build_arguments(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    config, ctx = image_workspace()
    options = {
        "environment": "default",
        "platform": "linux-64",
        "command": ("python", "app.py"),
        "output": tmp_path / "result.oci.tar",
        **overrides,
    }
    with pytest.raises(CondaWorkspacesError, match=message):
        WorkspaceImage.prepare(config, ctx, **options)


@pytest.mark.parametrize(
    "source",
    ["manifest", "lock-comment"],
    ids=str,
)
def test_image_rejects_credentials_in_raw_workspace_inputs(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
    source: str,
) -> None:
    manifest_extra = (
        '[tasks]\nleak = "curl https://user:secret@repo.example/private"\n'
        if source == "manifest"
        else ""
    )
    config, ctx = image_workspace(
        manifest_extra=manifest_extra,
    )
    if source == "lock-comment":
        lock = ctx.root / "conda.lock"
        lock.write_text(
            lock.read_text(encoding="utf-8")
            + "\n# https://user:secret@repo.example/private\n",
            encoding="utf-8",
        )

    with pytest.raises(ArchiveError, match="credentials embedded"):
        WorkspaceImage.prepare(
            config,
            ctx,
            environment="default",
            platform="linux-64",
            command=("python", "app.py"),
            output=tmp_path / "result.oci.tar",
        )


def test_image_build_stages_use_root_from_neutral_directory(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
) -> None:
    config, ctx = image_workspace(
        files={
            "conda.py": "raise AssertionError('workspace conda imported')\n",
            "conda_workspaces/__init__.py": (
                "raise AssertionError('workspace conda_workspaces imported')\n"
            ),
        }
    )
    base_image = "example.invalid/nonroot:latest"
    image = WorkspaceImage.prepare(
        config,
        ctx,
        environment="default",
        platform="linux-64",
        command=("python", "app.py"),
        output=tmp_path / "result.oci.tar",
        base_image=base_image,
    )
    recipe = image.recipe()
    assert ctx.root / "conda.py" in image.files
    assert ctx.root / "conda_workspaces" / "__init__.py" in image.files
    sources, remainder = recipe.split(f"FROM {base_image} AS sources\n", 1)[1].split(
        f"FROM {base_image} AS build\n", 1
    )
    build, final = remainder.split(f"FROM {base_image}\n", 1)

    assert sources.startswith("USER 0\n")
    assert build.startswith("USER 0\n")
    assert 'WORKDIR "/build"\nRUN ' in build
    assert f'WORKDIR "{image.workspace}"' not in build
    assert not final.startswith("USER 0\n")


@pytest.mark.parametrize(
    "case, error",
    [
        ("missing", LockfileNotFoundError),
        ("malformed", CondaWorkspacesError),
        ("wrong-version", LockfileStaleError),
        ("missing-environment", LockfileStaleError),
        ("missing-platform", LockfileStaleError),
        ("missing-dependency", LockfileStaleError),
        ("external-package", LockfileStaleError),
    ],
    ids=[
        "missing",
        "malformed",
        "wrong-version",
        "missing-environment",
        "missing-platform",
        "missing-dependency",
        "external-package",
    ],
)
def test_image_requires_a_complete_current_lock(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
    case: str,
    error: type[Exception],
) -> None:
    extra = '[dependencies]\npython = "*"\n' if case == "missing-dependency" else ""
    config, ctx = image_workspace(manifest_extra=extra)
    lock = ctx.root / "conda.lock"
    data = json.loads(lock.read_text(encoding="utf-8"))
    if case == "missing":
        lock.unlink()
    elif case == "malformed":
        lock.write_text("[", encoding="utf-8")
    else:
        if case == "wrong-version":
            data["version"] = 2
        elif case == "missing-environment":
            data["environments"] = {}
        elif case == "missing-platform":
            data["environments"]["default"]["packages"] = {}
        elif case == "external-package":
            data["environments"]["default"]["packages"]["linux-64"] = [
                {"pypi": "https://example.invalid/app.whl"}
            ]
        lock.write_text(json.dumps(data), encoding="utf-8")
    before = lock.read_bytes() if lock.exists() else None

    with pytest.raises(error):
        WorkspaceImage.prepare(
            config,
            ctx,
            environment="default",
            platform="linux-64",
            command=("python", "app.py"),
            output=tmp_path / "result.oci.tar",
        )

    assert (lock.read_bytes() if lock.exists() else None) == before


@pytest.mark.parametrize(
    "environment, platform, error",
    [
        ("missing", "linux-64", EnvironmentNotFoundError),
        ("default", "linux-aarch64", PlatformError),
        ("default", "win-64", PlatformError),
    ],
    ids=["undefined-environment", "undeclared-linux-platform", "undeclared-windows"],
)
def test_image_rejects_undefined_environment_or_platform(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
    environment: str,
    platform: str,
    error: type[Exception],
) -> None:
    config, ctx = image_workspace()
    with pytest.raises(error):
        WorkspaceImage.prepare(
            config,
            ctx,
            environment=environment,
            platform=platform,
            command=("python", "app.py"),
            output=tmp_path / "result.oci.tar",
        )


@pytest.mark.parametrize(
    "platform, oci_platform",
    [
        ("linux-64", "linux/amd64"),
        ("linux-aarch64", "linux/arm64"),
        ("osx-arm64", None),
        ("win-64", None),
    ],
    ids=["amd64", "arm64", "macos", "windows"],
)
def test_image_only_builds_supported_linux_targets(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
    platform: str,
    oci_platform: str | None,
) -> None:
    config, ctx = image_workspace(platforms=(platform,))
    options = {
        "environment": "default",
        "platform": platform,
        "command": ("python", "app.py"),
        "output": tmp_path / "result.oci.tar",
    }
    if oci_platform is None:
        with pytest.raises(CondaWorkspacesError, match="currently support linux"):
            WorkspaceImage.prepare(config, ctx, **options)
    else:
        assert (
            WorkspaceImage.prepare(config, ctx, **options).oci_platform == oci_platform
        )


def test_image_resolves_activation_and_local_sources_from_manifest_directory(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
) -> None:
    config, ctx = image_workspace(
        manifest_extra=(
            '[activation]\nscripts = ["activate.sh"]\n'
            '[pypi-dependencies]\napp = {path = "pkg", editable = false}\n'
        ),
        files={
            "activate.sh": "export APP_READY=yes\n",
            "pkg/pyproject.toml": '[project]\nname = "app"\nversion = "1.0"\n',
            "pkg/app.py": 'print("app")\n',
        },
    )
    image = WorkspaceImage.prepare(
        config,
        ctx,
        environment="default",
        platform="linux-64",
        command=("python", "app.py"),
        output=tmp_path / "result.oci.tar",
    )
    assert ctx.root / "activate.sh" in image.files
    assert ctx.root / "pkg" / "app.py" in image.files


@pytest.mark.parametrize(
    "local_package", [False, True], ids=["application", "root-package"]
)
@pytest.mark.parametrize(
    "envs_dir",
    ["runtime-envs", "runtime envs"],
    ids=["plain", "spaces"],
)
@pytest.mark.parametrize(
    "name",
    ["image-test", "image test"],
    ids=["plain", "spaces"],
)
def test_image_uses_custom_environment_path_without_archiving_host_environment(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
    local_package: bool,
    envs_dir: str,
    name: str,
) -> None:
    config, ctx = image_workspace(
        manifest_extra='[pypi-dependencies]\napp = {path = "."}\n'
        if local_package
        else "",
        files={
            "pyproject.toml": '[project]\nname = "app"\nversion = "1.0"\n',
            f"{envs_dir}/default/bin/python": "host executable",
        },
    )
    config.envs_dir = envs_dir
    config.name = name

    image = WorkspaceImage.prepare(
        config,
        ctx,
        environment="default",
        platform="linux-64",
        command=("python", "app.py"),
        output=tmp_path / "result.oci.tar",
    )

    assert image.workspace == f"/workspaces/{name}"
    assert image.prefix == f"/workspaces/{name}/{envs_dir}/default"
    assert image.preview()["prefix"] == image.prefix
    assert all(not file.is_relative_to(ctx.envs_dir) for file in image.files)
    copy = next(
        line
        for line in image.recipe().splitlines()
        if line.startswith("COPY --from=build [")
    )
    assert json.loads(copy.removeprefix("COPY --from=build ")) == [
        image.prefix,
        image.prefix,
    ]
    install_commands = [
        json.loads(line[line.index("[") :])
        for line in image.recipe().splitlines()
        if line.startswith("RUN ") and '"install"' in line and '"workspace"' in line
    ]
    assert len(install_commands) == 2
    for command in install_commands:
        assert command[command.index("--prefix") + 1] == image.prefix
        assert f"{image.workspace}/conda.toml" in command
        assert command[:5] == [
            "/opt/conda/bin/python",
            "-m",
            "conda",
            "workspace",
            "--file",
        ]
        assert "--locked" in command
    assert install_commands[0][-1] == "--download-only"
    assert "--download-only" not in install_commands[1]
    assert "RUN --network=none " + json.dumps(install_commands[1]) in image.recipe()

    entrypoint = next(
        line.removeprefix("ENTRYPOINT ")
        for line in image.recipe().splitlines()
        if line.startswith("ENTRYPOINT ")
    )
    assert json.loads(entrypoint)[-1] == (
        f"{image.workspace}/.conda/bin/workspace-entrypoint"
    )

    if Path("/bin/bash").is_file():
        path_assignment = next(
            line.removeprefix("ENV ")
            for line in image.recipe().splitlines()
            if line.startswith("ENV PATH=")
        )
        result = subprocess.run(
            ["/bin/bash", "-ec", path_assignment + '\nprintf "%s" "$PATH"'],
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout == (
            f"{image.prefix}/bin:{image.workspace}/.conda/bin:/usr/bin:/bin"
        )


@pytest.mark.parametrize(
    "component", ["envs-dir", "environment-name"], ids=["envs-dir", "environment"]
)
@pytest.mark.parametrize(
    "name", ["runtime${PATH}", "runtime's envs"], ids=["dollar", "apostrophe"]
)
def test_image_rejects_unsupported_characters_in_environment_prefix(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
    component: str,
    name: str,
) -> None:
    environment = name if component == "environment-name" else "default"
    config, ctx = image_workspace(
        manifest_extra=f'[environments]\n"{environment}" = []\n'
    )
    if component == "envs-dir":
        config.envs_dir = name
    else:
        lock = ctx.root / "conda.lock"
        data = json.loads(lock.read_text(encoding="utf-8"))
        data["environments"][environment] = data["environments"]["default"]
        lock.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CondaWorkspacesError, match="dollar signs or apostrophes"):
        WorkspaceImage.prepare(
            config,
            ctx,
            environment=environment,
            platform="linux-64",
            command=("python", "app.py"),
            output=tmp_path / "result.oci.tar",
        )


@pytest.mark.parametrize(
    "extra, files, message",
    [
        (
            (
                '[activation]\nscripts = ["activate.sh"]\n'
                '[workspace.archive]\nexclude = ["activate.sh"]\n'
            ),
            {"activate.sh": "export APP_READY=yes\n"},
            "Archive filters exclude",
        ),
        (
            '[activation]\nscripts = ["missing.sh"]\n',
            {},
            "activation scripts must be regular files",
        ),
        (
            '[activation]\nscripts = ["/outside/activate.sh"]\n',
            {},
            "activation scripts must use workspace-relative paths",
        ),
        (
            '[activation]\nscripts = ["first/activate.sh", "second/activate.sh"]\n',
            {"first/activate.sh": "", "second/activate.sh": ""},
            "activation scripts must have distinct filenames",
        ),
        (
            '[pypi-dependencies]\napp = {path = "pkg", editable = true}\n',
            {"pkg/pyproject.toml": ""},
            "unsupported editable install",
        ),
        (
            '[pypi-dependencies]\napp = {path = "pkg"}\n',
            {"pkg/app.py": ""},
            "no included Python build metadata",
        ),
        (
            (
                '[pypi-dependencies]\napp = {path = "pkg"}\n'
                '[workspace.archive]\nexclude = ["pkg/app.py"]\n'
            ),
            {"pkg/pyproject.toml": "", "pkg/app.py": ""},
            "Archive filters exclude",
        ),
        (
            '[pypi-dependencies]\napp = {path = ".."}\n',
            {},
            "directory inside the workspace",
        ),
        (
            '[pypi-dependencies]\napp = {path = "/outside/pkg"}\n',
            {},
            "must use a workspace-relative path",
        ),
        (
            "[pypi-dependencies]\napp = {path = 'C:\\project\\pkg'}\n",
            {},
            "must use a workspace-relative path",
        ),
        (
            '[pypi-dependencies]\napp = {git = "https://example.invalid/app.git"}\n',
            {},
            "unsupported Git or URL source",
        ),
        (
            '[pypi-dependencies]\napp = {url = "https://example.invalid/app.whl"}\n',
            {},
            "unsupported Git or URL source",
        ),
        (
            '[workspace.archive]\nexclude = ["conda.lock"]\n',
            {},
            "Archive filters exclude",
        ),
    ],
    ids=[
        "excluded-activation",
        "missing-activation",
        "absolute-activation",
        "duplicate-activation-filenames",
        "editable-package",
        "missing-package-metadata",
        "excluded-package-source",
        "outside-package",
        "absolute-posix-package",
        "absolute-windows-package",
        "git-source",
        "url-source",
        "excluded-lock",
    ],
)
def test_image_rejects_missing_or_unsupported_sources(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    tmp_path: Path,
    extra: str,
    files: dict[str, str],
    message: str,
) -> None:
    config, ctx = image_workspace(manifest_extra=extra, files=files)
    with pytest.raises(CondaWorkspacesError, match=message):
        WorkspaceImage.prepare(
            config,
            ctx,
            environment="default",
            platform="linux-64",
            command=("python", "app.py"),
            output=tmp_path / "result.oci.tar",
        )


@pytest.mark.parametrize(
    "member", ["conda.toml", "conda.lock"], ids=["manifest", "lock"]
)
def test_image_rejects_changed_inputs_before_docker(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_image_builder: Callable[..., list[list[str]]],
    tmp_path: Path,
    member: str,
) -> None:
    config, ctx = image_workspace()
    output = tmp_path / "result.oci.tar"
    image = WorkspaceImage.prepare(
        config,
        ctx,
        environment="default",
        platform="linux-64",
        command=("python", "app.py"),
        output=output,
    )
    path = ctx.root / member
    path.write_bytes(path.read_bytes() + b"\n")
    calls = record_image_builder()

    with pytest.raises(ArchiveError):
        image.build()

    assert calls == []
    assert not output.exists()


@pytest.mark.parametrize("builder", [None, "existing"], ids=["temporary", "existing"])
@pytest.mark.parametrize(
    "destination", ["output", "load", "push"], ids=["oci", "load", "push"]
)
def test_image_build_publishes_after_success_and_only_cleans_its_own_builder(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_image_builder: Callable[..., list[list[str]]],
    tmp_path: Path,
    builder: str | None,
    destination: str,
) -> None:
    config, ctx = image_workspace(
        manifest_extra=(
            '[workspace.archive]\ncompression = "zst"\ncompression-level = 15\n'
        )
    )
    output = tmp_path / "result.oci.tar"
    image = WorkspaceImage.prepare(
        config,
        ctx,
        environment="default",
        platform="linux-64",
        command=("python", "app.py"),
        tags=("example:v1", "example:latest"),
        output=output if destination == "output" else None,
        load=destination == "load",
        push=destination == "push",
        builder=builder,
    )

    def inspect_build(args: list[str]) -> None:
        assert not output.exists()
        context = Path(args[-1])
        with tarfile.open(context / "workspace.tar.gz") as archive:
            assert set(archive.getnames()) == {"app.py", "conda.lock", "conda.toml"}
        assert (context / "Dockerfile").read_text(encoding="utf-8") == image.recipe()
        assert (context / "tools/conda_workspaces/cli/workspace/install.py").is_file()
        assert not list((context / "tools").rglob("*.pyc"))

    calls = record_image_builder(on_build=inspect_build)
    result = image.build()

    assert result["digest"] == "sha256:" + "a" * 64
    assert [call[2] for call in calls] == (
        ["build"] if builder else ["create", "build", "rm"]
    )
    build = next(call for call in calls if call[2] == "build")
    assert build[build.index("--platform") + 1] == "linux/amd64"
    assert [
        build[index + 1] for index, value in enumerate(build) if value == "--tag"
    ] == ["example:v1", "example:latest"]
    if builder is not None:
        assert build[build.index("--builder") + 1] == builder
    if destination == "output":
        assert output.read_bytes() == b"OCI image archive"
    else:
        assert f"--{destination}" in build
        assert not output.exists()
    assert not Path(build[-1]).exists()


@pytest.mark.parametrize(
    "operation, expected_calls",
    [
        ("create", ["create"]),
        ("build", ["create", "build", "rm"]),
    ],
    ids=["create-failed", "build-failed"],
)
def test_image_build_failure_does_not_publish_partial_oci_archive(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_image_builder: Callable[..., list[list[str]]],
    tmp_path: Path,
    operation: str,
    expected_calls: list[str],
) -> None:
    config, ctx = image_workspace()
    output = tmp_path / "result.oci.tar"
    image = WorkspaceImage.prepare(
        config,
        ctx,
        environment="default",
        platform="linux-64",
        command=("python", "app.py"),
        output=output,
    )
    calls = record_image_builder(fail_operation=operation)

    with pytest.raises(CondaWorkspacesError, match="Docker Buildx failed"):
        image.build()

    assert [call[2] for call in calls] == expected_calls
    assert not output.exists()


def test_image_cleanup_failure_keeps_successful_artifact(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_image_builder: Callable[..., list[list[str]]],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config, ctx = image_workspace()
    output = tmp_path / "result.oci.tar"
    image = WorkspaceImage.prepare(
        config,
        ctx,
        environment="default",
        platform="linux-64",
        command=("python", "app.py"),
        output=output,
    )
    record_image_builder(fail_operation="rm")

    assert image.build()["success"] is True
    assert output.read_bytes() == b"OCI image archive"
    captured = capsys.readouterr()
    assert "Could not remove temporary Buildx builder" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize(
    "created_during_build", [False, True], ids=["existing", "concurrent"]
)
def test_image_never_overwrites_an_existing_output(
    image_workspace: Callable[..., tuple[WorkspaceConfig, WorkspaceContext]],
    record_image_builder: Callable[..., list[list[str]]],
    tmp_path: Path,
    created_during_build: bool,
) -> None:
    config, ctx = image_workspace()
    output = tmp_path / "result.oci.tar"
    if not created_during_build:
        output.write_bytes(b"existing image")

    def create_output(args: list[str]) -> None:
        output.write_bytes(b"existing image")

    calls = record_image_builder(on_build=create_output)
    with pytest.raises(
        CondaWorkspacesError,
        match="already exists|changed before writing",
    ):
        image = WorkspaceImage.prepare(
            config,
            ctx,
            environment="default",
            platform="linux-64",
            command=("python", "app.py"),
            output=output,
        )
        image.build()

    assert output.read_bytes() == b"existing image"
    assert [call[2] for call in calls] == (
        ["create", "build", "rm"] if created_during_build else []
    )
