"""Tests for ``conda workspace import``."""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

import pytest
import tomlkit
from conda.exceptions import DryRunExit
from rich.console import Console

from conda_workspaces.cli.workspace.import_manifest import execute_import
from conda_workspaces.importers import detect_format, import_manifest

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path


_DEFAULTS = {
    "output": None,
    "quiet": False,
    "dry_run": False,
    "yes": False,
    "json": False,
}


_ENVIRONMENT_YML = """\
name: myenv
channels:
  - conda-forge
dependencies:
  - python>=3.10
  - numpy>=1.24
  - pip:
    - requests>=2.28
"""

_ANACONDA_PROJECT_YML = """\
name: ap-demo
channels:
  - conda-forge
packages:
  - python>=3.10
  - pandas
commands:
  serve:
    unix: python serve.py
    description: Run the server
env_specs:
  default:
    packages: []
"""

_CONDA_PROJECT_YML = """\
name: cp-demo
environments:
  default:
    - environment.yml
commands:
  test:
    cmd: pytest
"""

_CONDA_PROJECT_ENV_YML = """\
name: cp-default
channels:
  - conda-forge
dependencies:
  - python>=3.10
  - pytest
"""

_PIXI_TOML = """\
[workspace]
name = "pixi-demo"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.10"

[tasks]
build = "python -m build"
"""

_PYPROJECT_TOML = """\
[project]
name = "pyproject-demo"

[tool.conda.workspace]
name = "pyproject-demo"
channels = ["conda-forge"]
platforms = ["linux-64"]

[tool.conda.dependencies]
python = ">=3.10"

[tool.conda.tasks]
lint = "ruff check ."
"""


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("environment.yml", "EnvironmentYmlImporter"),
        ("environment.yaml", "EnvironmentYmlImporter"),
        ("anaconda-project.yml", "AnacondaProjectImporter"),
        ("anaconda-project.yaml", "AnacondaProjectImporter"),
        ("conda-project.yml", "CondaProjectImporter"),
        ("conda-project.yaml", "CondaProjectImporter"),
        ("pixi.toml", "PixiTomlImporter"),
        ("pyproject.toml", "PyprojectTomlImporter"),
    ],
)
def test_detect_format(tmp_path: Path, filename: str, expected: str) -> None:
    p = tmp_path / filename
    p.touch()
    assert detect_format(p) == expected


def test_detect_format_unknown(tmp_path: Path) -> None:
    p = tmp_path / "unknown.txt"
    p.touch()
    with pytest.raises(ValueError, match="Unrecognised manifest format"):
        detect_format(p)


@pytest.mark.parametrize(
    "filename, content, expected_name",
    [
        ("environment.yml", _ENVIRONMENT_YML, "myenv"),
        ("anaconda-project.yml", _ANACONDA_PROJECT_YML, "ap-demo"),
        ("pixi.toml", _PIXI_TOML, "pixi-demo"),
        ("pyproject.toml", _PYPROJECT_TOML, "pyproject-demo"),
    ],
    ids=["env-yml", "anaconda-project", "pixi", "pyproject"],
)
def test_import_manifest_produces_workspace(
    tmp_path: Path,
    filename: str,
    content: str,
    expected_name: str,
) -> None:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    doc = import_manifest(p)
    assert doc["workspace"]["name"] == expected_name
    assert "channels" in doc["workspace"]


def test_import_conda_project(tmp_path: Path) -> None:
    (tmp_path / "conda-project.yml").write_text(
        _CONDA_PROJECT_YML, encoding="utf-8"
    )
    (tmp_path / "environment.yml").write_text(
        _CONDA_PROJECT_ENV_YML, encoding="utf-8"
    )
    doc = import_manifest(tmp_path / "conda-project.yml")
    assert doc["workspace"]["name"] == "cp-demo"
    assert "python" in doc["dependencies"]


def test_env_yml_dependencies(tmp_path: Path) -> None:
    p = tmp_path / "environment.yml"
    p.write_text(_ENVIRONMENT_YML, encoding="utf-8")
    doc = import_manifest(p)
    assert "python" in doc["dependencies"]
    assert "numpy" in doc["dependencies"]
    assert "requests" in doc["pypi-dependencies"]


def test_ap_commands_become_tasks(tmp_path: Path) -> None:
    p = tmp_path / "anaconda-project.yml"
    p.write_text(_ANACONDA_PROJECT_YML, encoding="utf-8")
    doc = import_manifest(p)
    assert "serve" in doc["tasks"]


def test_pixi_tasks_preserved(tmp_path: Path) -> None:
    p = tmp_path / "pixi.toml"
    p.write_text(_PIXI_TOML, encoding="utf-8")
    doc = import_manifest(p)
    assert "build" in doc["tasks"]


def test_execute_import_writes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "environment.yml").write_text(_ENVIRONMENT_YML, encoding="utf-8")
    args = make_args(_DEFAULTS, file=tmp_path / "environment.yml")
    console = Console(file=StringIO(), width=200)
    result = execute_import(args, console=console)
    assert result == 0
    assert (tmp_path / "conda.toml").exists()
    doc = tomlkit.parse((tmp_path / "conda.toml").read_text(encoding="utf-8"))
    assert doc["workspace"]["name"] == "myenv"


def test_execute_import_custom_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "environment.yml").write_text(_ENVIRONMENT_YML, encoding="utf-8")
    out = tmp_path / "custom.toml"
    args = make_args(_DEFAULTS, file=tmp_path / "environment.yml", output=out)
    console = Console(file=StringIO(), width=200)
    result = execute_import(args, console=console)
    assert result == 0
    assert out.exists()


def test_execute_import_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "environment.yml").write_text(_ENVIRONMENT_YML, encoding="utf-8")
    args = make_args(_DEFAULTS, file=tmp_path / "environment.yml", dry_run=True)
    buf = StringIO()
    console = Console(file=buf, width=200)
    with pytest.raises(DryRunExit):
        execute_import(args, console=console)
    assert not (tmp_path / "conda.toml").exists()
    assert "[workspace]" in buf.getvalue()


def test_execute_import_file_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = make_args(_DEFAULTS, file=tmp_path / "nonexistent.yml")
    console = Console(file=StringIO(), width=200)
    result = execute_import(args, console=console)
    assert result == 1


def test_execute_import_overwrite_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "environment.yml").write_text(_ENVIRONMENT_YML, encoding="utf-8")
    (tmp_path / "conda.toml").write_text("# old", encoding="utf-8")

    confirm_calls: list[str] = []
    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.import_manifest.confirm_yn",
        lambda msg: confirm_calls.append(msg),
    )

    args = make_args(_DEFAULTS, file=tmp_path / "environment.yml")
    console = Console(file=StringIO(), width=200)
    result = execute_import(args, console=console)
    assert result == 0
    assert len(confirm_calls) == 1
    assert "Overwrite" in confirm_calls[0]
    content = (tmp_path / "conda.toml").read_text(encoding="utf-8")
    assert "[workspace]" in content
