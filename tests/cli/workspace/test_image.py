"""Tests for ``conda workspace image`` parsing, dispatch, and output."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from conda_workspaces.cli.main import execute_workspace, generate_workspace_parser
from conda_workspaces.cli.workspace import image as image_module
from conda_workspaces.cli.workspace.image import execute_image
from conda_workspaces.exceptions import CondaWorkspacesError

if TYPE_CHECKING:
    from conda_workspaces.context import WorkspaceContext
    from conda_workspaces.models import WorkspaceConfig


@pytest.fixture
def recorded_image(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    list[tuple[WorkspaceConfig, WorkspaceContext, dict[str, object]]],
    list[str],
]:
    """Record preparation and distinguish preview from the Docker build."""
    prepared: list[tuple[WorkspaceConfig, WorkspaceContext, dict[str, object]]] = []
    operations: list[str] = []

    class RecordingImage:
        @classmethod
        def prepare(
            cls,
            config: WorkspaceConfig,
            ctx: WorkspaceContext,
            **kwargs: object,
        ) -> RecordingImage:
            prepared.append((config, ctx, kwargs))
            return cls()

        def preview(self) -> dict[str, object]:
            operations.append("preview")
            return {
                "success": True,
                "environment": "test",
                "recipe": 'FROM debian:bookworm-slim\nCMD ["python", "-m", "myapp"]\n',
                "files": ["pixi.toml", "src/[app].py"],
                "outputs": {"mode": "preview"},
            }

        def build(self) -> dict[str, object]:
            operations.append("build")
            print("BuildKit progress", file=sys.stderr)
            return {
                "success": True,
                "environment": "test",
                "outputs": {"digest": "sha256:" + "a" * 64},
            }

    monkeypatch.setattr(image_module, "WorkspaceImage", RecordingImage)
    return prepared, operations


def test_image_parser_default_base_image() -> None:
    args = generate_workspace_parser().parse_args(
        ["image", "-e", "runtime", "--platform", "linux-64", "--load"]
    )
    assert args.base_image == "debian:bookworm-slim"


@pytest.mark.parametrize(
    "options",
    [
        ["--platform", "linux-64", "--load"],
        ["-e", "runtime", "--load"],
        ["-e", "runtime", "--platform", "linux-64"],
        ["-e", "runtime", "--platform", "linux-64", "--load", "--push"],
        ["-e", "runtime", "--platform", "linux-64", "--load", "-o", "app.tar"],
        ["-e", "runtime", "--platform", "linux-64", "--push", "-o", "app.tar"],
    ],
    ids=[
        "missing-environment",
        "missing-platform",
        "missing-destination",
        "load-and-push",
        "load-and-archive",
        "push-and-archive",
    ],
)
def test_image_parser_requires_explicit_selection_and_one_destination(
    options: list[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        generate_workspace_parser().parse_args(["image", *options])
    assert error.value.code == 2


@pytest.mark.parametrize("dry_run", [False, True], ids=["build", "preview"])
@pytest.mark.parametrize("json_output", [False, True], ids=["human", "json"])
@pytest.mark.parametrize(
    "destination, expected_destination, expected_verb",
    [
        (
            ["--load"],
            {"load": True, "push": False, "output": None},
            ("Loaded", "Would load"),
        ),
        (
            ["--push"],
            {"load": False, "push": True, "output": None},
            ("Pushed", "Would push"),
        ),
        (
            ["--output", "app.oci.tar"],
            {"load": False, "push": False, "output": Path("app.oci.tar")},
            ("Wrote", "Would write"),
        ),
    ],
    ids=["load", "push", "oci-archive"],
)
def test_image_dispatch_preserves_metadata_and_dry_run(
    pixi_workspace: Path,
    recorded_image: tuple[
        list[tuple[WorkspaceConfig, WorkspaceContext, dict[str, object]]],
        list[str],
    ],
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
    json_output: bool,
    destination: list[str],
    expected_destination: dict[str, object],
    expected_verb: tuple[str, str],
) -> None:
    options = []
    if dry_run:
        options.append("--dry-run")
    if json_output:
        options.append("--json")
    args = generate_workspace_parser().parse_args(
        [
            "--file",
            str(pixi_workspace / "pixi.toml"),
            "image",
            "-e",
            "test",
            "--platform",
            "linux-aarch64",
            "-t",
            "example:latest",
            "--tag",
            "example:v1",
            "--base-image",
            "example/base:latest",
            "--builder",
            "existing-builder",
            *destination,
            *options,
            "--",
            "python",
            "-m",
            "myapp",
            "--json",
        ]
    )

    assert execute_workspace(args) == 0

    prepared, operations = recorded_image
    assert operations == ["preview" if dry_run else "build"]
    assert len(prepared) == 1
    config, _, kwargs = prepared[0]
    assert Path(config.manifest_path) == pixi_workspace / "pixi.toml"
    assert kwargs == {
        "environment": "test",
        "platform": "linux-aarch64",
        "command": ("python", "-m", "myapp", "--json"),
        "tags": ("example:latest", "example:v1"),
        **expected_destination,
        "base_image": "example/base:latest",
        "builder": "existing-builder",
    }
    captured = capsys.readouterr()
    assert captured.err == ("" if dry_run else "BuildKit progress\n")
    if json_output:
        payload = json.loads(captured.out)
        assert payload["success"] is True
        assert payload["environment"] == "test"
        if dry_run:
            assert payload["outputs"] == {"mode": "preview"}
            assert payload["files"] == ["pixi.toml", "src/[app].py"]
        else:
            assert payload["outputs"] == {"digest": "sha256:" + "a" * 64}
    else:
        assert expected_verb[dry_run] in captured.out
        if dry_run:
            assert 'CMD ["python", "-m", "myapp"]' in captured.out
            assert "src/[app].py" in captured.out


@pytest.mark.parametrize("command", [[], ["--"]], ids=["omitted", "separator-only"])
def test_image_missing_command_fails_before_preparing(
    recorded_image: tuple[
        list[tuple[WorkspaceConfig, WorkspaceContext, dict[str, object]]],
        list[str],
    ],
    command: list[str],
) -> None:
    args = generate_workspace_parser().parse_args(
        ["image", "-e", "runtime", "--platform", "linux-64", "--load", *command]
    )
    with pytest.raises(CondaWorkspacesError, match="No image command specified"):
        execute_image(args)
    assert recorded_image == ([], [])
