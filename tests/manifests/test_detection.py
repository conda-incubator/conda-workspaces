"""Tests for workspace manifest detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from conda_workspaces.exceptions import (
    NoTaskFileError,
    WorkspaceNotFoundError,
    WorkspaceParseError,
)
from conda_workspaces.manifests import (
    cached_parse,
    detect_and_parse,
    detect_and_parse_tasks,
    detect_task_file,
    detect_workspace_file,
    find_parser,
)
from conda_workspaces.manifests.pixi_toml import PixiTomlParser
from conda_workspaces.manifests.pyproject_toml import PyprojectTomlParser
from conda_workspaces.manifests.toml import CondaTomlParser


@pytest.mark.parametrize(
    "fixture_name",
    ["sample_pixi_toml", "sample_pyproject_toml"],
    ids=["pixi-toml", "pyproject-toml"],
)
def test_detect_manifest(fixture_name, request):
    manifest = request.getfixturevalue(fixture_name)
    path = detect_workspace_file(manifest.parent)
    assert path == manifest


def test_detect_walks_up(sample_pixi_toml):
    subdir = sample_pixi_toml.parent / "src" / "pkg"
    subdir.mkdir(parents=True)
    path = detect_workspace_file(subdir)
    assert path == sample_pixi_toml


def test_detect_not_found(tmp_path):
    with pytest.raises(WorkspaceNotFoundError):
        detect_workspace_file(tmp_path)


def test_conda_toml_priority_over_pixi(tmp_path):
    """conda.toml should be preferred when both exist."""
    toml = (
        '[workspace]\nname = "{name}"\nchannels'
        ' = ["conda-forge"]\nplatforms = ["linux-64"]\n'
    )
    conda = tmp_path / "conda.toml"
    conda.write_text(toml.format(name="conda"), encoding="utf-8")
    pixi = tmp_path / "pixi.toml"
    pixi.write_text(toml.format(name="pixi"), encoding="utf-8")
    path = detect_workspace_file(tmp_path)
    assert path.name == "conda.toml"


def test_pixi_toml_priority_over_pyproject(tmp_path):
    """pixi.toml should be preferred over pyproject.toml."""
    pixi = tmp_path / "pixi.toml"
    pixi.write_text(
        '[workspace]\nname = "pixi"\nchannels'
        ' = ["conda-forge"]\nplatforms = ["linux-64"]\n',
        encoding="utf-8",
    )
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.pixi.workspace]\nchannels = ["conda-forge"]\nplatforms = ["linux-64"]\n',
        encoding="utf-8",
    )
    path = detect_workspace_file(tmp_path)
    assert path.name == "pixi.toml"


def test_conda_toml(tmp_path):
    path = tmp_path / "conda.toml"
    path.write_text(
        '[workspace]\nname = "cw"\nchannels'
        ' = ["conda-forge"]\nplatforms = ["linux-64"]\n',
        encoding="utf-8",
    )
    detected = detect_workspace_file(tmp_path)
    assert detected.name == "conda.toml"


@pytest.mark.parametrize(
    "filename, parser_type",
    [
        ("pixi.toml", PixiTomlParser),
        ("conda.toml", CondaTomlParser),
        ("pyproject.toml", PyprojectTomlParser),
    ],
    ids=["pixi", "conda", "pyproject"],
)
def test_find_parser(filename, parser_type):
    parser = find_parser(Path(filename))
    assert isinstance(parser, parser_type)


def test_find_parser_unknown():
    with pytest.raises(WorkspaceParseError, match="No parser"):
        find_parser(Path("setup.cfg"))


def test_detect_and_parse(sample_pixi_toml):
    cached_parse.cache_clear()
    path, config = detect_and_parse(sample_pixi_toml.parent)
    assert path == sample_pixi_toml
    assert config.name == "test-project"


def test_detect_and_parse_not_found(tmp_path):
    with pytest.raises(WorkspaceNotFoundError):
        detect_and_parse(tmp_path)


def test_detect_defaults_to_cwd(sample_pixi_toml, monkeypatch):
    """detect_workspace_file(None) should use cwd."""
    monkeypatch.chdir(sample_pixi_toml.parent)
    path = detect_workspace_file(None)
    assert path == sample_pixi_toml


def test_detect_skips_file_without_workspace(tmp_path):
    """A pixi.toml without [workspace] should be skipped."""
    path = tmp_path / "pixi.toml"
    path.write_text('[dependencies]\npython = ">=3.10"\n', encoding="utf-8")
    with pytest.raises(WorkspaceNotFoundError):
        detect_workspace_file(tmp_path)


@pytest.mark.parametrize(
    ("fixture_name", "expected_filename"),
    [
        ("task_conda_toml", "conda.toml"),
        ("task_pixi_toml", "pixi.toml"),
    ],
)
def test_detect_task_file(fixture_name, expected_filename, request):
    path = request.getfixturevalue(fixture_name)
    found = detect_task_file(path.parent)
    assert found is not None
    assert found.name == expected_filename


def test_detect_task_priority_conda_over_pixi(
    tmp_project, task_pixi_toml, task_conda_toml
):
    """conda.toml takes priority over pixi.toml."""
    found = detect_task_file(tmp_project)
    assert found is not None
    assert found.name == "conda.toml"


def test_detect_task_priority_conda_over_pyproject(
    tmp_project, task_conda_toml, task_pyproject
):
    """conda.toml takes priority over pyproject.toml."""
    found = detect_task_file(tmp_project)
    assert found is not None
    assert found.name == "conda.toml"


def test_detect_task_none(tmp_project):
    assert detect_task_file(tmp_project) is None


@pytest.mark.parametrize(
    ("fixture_name", "parser_class"),
    [
        ("task_conda_toml", CondaTomlParser),
        ("task_pixi_toml", PixiTomlParser),
    ],
)
def test_get_task_parser(fixture_name, parser_class, request):
    path = request.getfixturevalue(fixture_name)
    assert isinstance(find_parser(path), parser_class)


def test_get_task_parser_unknown(tmp_project):
    path = tmp_project / "random.txt"
    path.write_text("hello")
    with pytest.raises(WorkspaceParseError):
        find_parser(path)


def test_detect_and_parse_tasks_with_file(sample_yaml):
    path, tasks, _ = detect_and_parse_tasks(file_path=sample_yaml)
    assert path == sample_yaml.resolve()
    assert "build" in tasks


def test_detect_and_parse_tasks_no_file(tmp_path):
    with pytest.raises(NoTaskFileError):
        detect_and_parse_tasks(start_dir=tmp_path)
