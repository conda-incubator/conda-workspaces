"""Tests for shared manifest parser operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import conda_workspaces.manifests.base as base_mod
import conda_workspaces.manifests.pyproject_toml as pyproject_mod
from conda_workspaces.exceptions import (
    ManifestExistsError,
    TaskParseError,
    WorkspaceParseError,
)
from conda_workspaces.manifests.base import ManifestParser
from conda_workspaces.manifests.pixi_toml import PixiTomlParser
from conda_workspaces.manifests.pyproject_toml import PyprojectTomlParser
from conda_workspaces.manifests.toml import CondaTomlParser
from conda_workspaces.models import Task

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


def test_copy_manifest_refuses_dangling_destination_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manifest = source / "conda.toml"
    manifest.write_text("[workspace]\nname = 'source'\n", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    target = destination / manifest.name
    outside = tmp_path / "outside.toml"
    target.symlink_to(outside)

    with pytest.raises(ManifestExistsError, match="already exists"):
        ManifestParser.copy_manifest(manifest, destination)

    assert target.is_symlink()
    assert not outside.exists()


def test_copy_manifest_does_not_replace_raced_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source" / "conda.toml"
    source.parent.mkdir()
    source.write_text("[workspace]\nname = 'source'\n", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    target = destination / "conda.toml"
    original_atomic_write_text = base_mod.atomic_write_text

    def create_before_publication(path: Path, content: str, **kwargs: object) -> None:
        path.write_text("raced", encoding="utf-8")
        original_atomic_write_text(path, content, **kwargs)

    monkeypatch.setattr(base_mod, "atomic_write_text", create_before_publication)

    with pytest.raises(ValueError, match="changed before writing"):
        ManifestParser.copy_manifest(source, destination)

    assert target.read_text(encoding="utf-8") == "raced"


def test_write_workspace_stub_does_not_replace_raced_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "conda.toml"
    original_atomic_write_text = base_mod.atomic_write_text

    def create_before_publication(path: Path, content: str, **kwargs: object) -> None:
        path.write_text("raced", encoding="utf-8")
        original_atomic_write_text(path, content, **kwargs)

    monkeypatch.setattr(base_mod, "atomic_write_text", create_before_publication)

    with pytest.raises(ValueError, match="changed before writing"):
        CondaTomlParser().write_workspace_stub(
            tmp_path,
            "workspace",
            ["conda-forge"],
            ["linux-64"],
        )

    assert target.read_text(encoding="utf-8") == "raced"


def test_pyproject_stub_does_not_overwrite_replaced_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "pyproject.toml"
    target.write_text("[project]\nname = 'original'\n", encoding="utf-8")
    displaced = tmp_path / "original.toml"
    original_atomic_write_text = base_mod.atomic_write_text

    def replace_before_publication(path: Path, content: str, **kwargs: object) -> None:
        path.rename(displaced)
        path.write_text("[project]\nname = 'replacement'\n", encoding="utf-8")
        original_atomic_write_text(path, content, **kwargs)

    monkeypatch.setattr(
        "conda_workspaces.manifests.pyproject_toml.atomic_write_text",
        replace_before_publication,
    )

    with pytest.raises(ValueError, match="changed before writing"):
        PyprojectTomlParser().write_workspace_stub(
            tmp_path,
            "workspace",
            ["conda-forge"],
            ["linux-64"],
        )

    assert "replacement" in target.read_text(encoding="utf-8")
    assert "original" in displaced.read_text(encoding="utf-8")


@pytest.mark.parametrize("operation", ["add", "remove"])
@pytest.mark.parametrize("mutation", ["rewrite", "replace"])
@pytest.mark.parametrize(
    ("parser", "filename", "original", "concurrent", "writer_module"),
    [
        pytest.param(
            CondaTomlParser(),
            "conda.toml",
            '[tasks]\nkeep = "echo keep"\n',
            '[tasks]\nconcurrent = "echo concurrent"\n',
            base_mod,
            id="conda",
        ),
        pytest.param(
            PyprojectTomlParser(),
            "pyproject.toml",
            '[tool.conda.tasks]\nkeep = "echo keep"\n',
            '[tool.conda.tasks]\nconcurrent = "echo concurrent"\n',
            pyproject_mod,
            id="pyproject",
        ),
    ],
)
def test_task_mutation_rejects_manifest_generation_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    mutation: str,
    parser: ManifestParser,
    filename: str,
    original: str,
    concurrent: str,
    writer_module: Any,
) -> None:
    target = tmp_path / filename
    target.write_text(original, encoding="utf-8")
    atomic_write_text = writer_module.atomic_write_text

    def replace_before_publication(
        path: Path,
        content: str,
        **kwargs: object,
    ) -> None:
        if mutation == "rewrite":
            target.write_text(concurrent, encoding="utf-8")
        else:
            replacement = target.with_name("replacement.toml")
            replacement.write_text(concurrent, encoding="utf-8")
            replacement.replace(target)
        atomic_write_text(path, content, **kwargs)

    monkeypatch.setattr(
        writer_module,
        "atomic_write_text",
        replace_before_publication,
    )

    with pytest.raises(ValueError, match="changed before writing"):
        if operation == "add":
            parser.add_task(target, "new", Task(name="new", cmd="echo new"))
        else:
            parser.remove_task(target, "keep")

    assert target.read_text(encoding="utf-8") == concurrent


def test_task_mutation_redacts_malformed_manifest_credentials(tmp_path: Path) -> None:
    target = tmp_path / "conda.toml"
    target.write_text(
        '[tasks]\nleak = "https://user:LEAKME@example.test/[\n',
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceParseError) as caught:
        CondaTomlParser().add_task(
            target,
            "new",
            Task(name="new", cmd="echo new"),
        )

    assert "LEAKME" not in str(caught.value)
    assert "user" not in str(caught.value)


@pytest.mark.parametrize(
    ("parser_class", "filename"),
    [
        pytest.param(CondaTomlParser, "conda.toml", id="conda"),
        pytest.param(PixiTomlParser, "pixi.toml", id="pixi"),
        pytest.param(PyprojectTomlParser, "pyproject.toml", id="pyproject"),
    ],
)
def test_parse_tasks_redacts_malformed_manifest_credentials(
    tmp_path: Path,
    parser_class: type[ManifestParser],
    filename: str,
) -> None:
    target = tmp_path / filename
    target.write_text(
        '[tasks]\nleak = "https://user:LEAKME@example.test/[\n',
        encoding="utf-8",
    )

    with pytest.raises(TaskParseError) as caught:
        parser_class().parse_tasks(target)

    assert "LEAKME" not in str(caught.value)
    assert "user" not in str(caught.value)
