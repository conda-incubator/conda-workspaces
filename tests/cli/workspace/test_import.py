"""Tests for ``conda workspace import``."""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from typing import TYPE_CHECKING

import pytest
import tomlkit
from conda.base.constants import on_win
from conda.exceptions import DryRunExit
from conda.utils import quote_for_shell
from rich.console import Console

from conda_workspaces.cli.workspace.import_manifest import execute_import
from conda_workspaces.exceptions import ManifestImportError
from conda_workspaces.importers import find_importer
from conda_workspaces.runner import SubprocessShell

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
    assert type(find_importer(p)).__name__ == expected


def test_detect_format_unknown(tmp_path: Path) -> None:
    p = tmp_path / "unknown.txt"
    p.touch()
    with pytest.raises(ValueError, match="Unrecognised manifest format"):
        find_importer(p)


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
    doc = find_importer(p).convert(p)
    assert doc["workspace"]["name"] == expected_name
    assert "channels" in doc["workspace"]


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        pytest.param(
            "pixi.toml",
            """\
[workspace]
name = "rich-platform"
channels = ["conda-forge"]
platforms = [
  "osx-arm64",
  { platform = "linux-64", libc = "2.28" },
]
""",
            id="pixi",
        ),
        pytest.param(
            "pyproject.toml",
            """\
[project]
name = "rich-platform"

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = [
  "osx-arm64",
  { platform = "linux-64", libc = "2.28" },
]
""",
            id="pyproject",
        ),
    ],
)
def test_import_rich_platform_system_requirements(
    tmp_path: Path,
    filename: str,
    content: str,
) -> None:
    p = tmp_path / filename
    p.write_text(
        content,
        encoding="utf-8",
    )

    doc = find_importer(p).convert(p)

    platforms = doc["workspace"]["platforms"]
    assert platforms[0] == "osx-arm64"
    assert dict(platforms[1]) == {"platform": "linux-64", "libc": "2.28"}


@pytest.mark.parametrize(
    "env_file",
    ["environment.yml", "envs/default.yml", "./envs/default.yml"],
    ids=["root-env-file", "nested-env-file", "current-dir-prefix"],
)
def test_import_conda_project(tmp_path: Path, env_file: str) -> None:
    (tmp_path / "conda-project.yml").write_text(
        f"""\
name: cp-demo
environments:
  default:
    - {env_file}
commands:
  test:
    cmd: pytest
""",
        encoding="utf-8",
    )
    env_path = tmp_path / env_file
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(_CONDA_PROJECT_ENV_YML, encoding="utf-8")
    doc = find_importer(tmp_path / "conda-project.yml").convert(
        tmp_path / "conda-project.yml"
    )
    assert doc["workspace"]["name"] == "cp-demo"
    assert "python" in doc["dependencies"]


@pytest.mark.parametrize(
    "env_file_template",
    [
        "../outside-env.yml",
        "../../outside-env.yml",
        "{outside}",
        r"..\outside-env.yml",
        "C:outside-env.yml",
        r"C:\outside-env.yml",
    ],
    ids=[
        "parent",
        "parents",
        "absolute",
        "windows-parent",
        "windows-drive-relative",
        "windows-absolute",
    ],
)
def test_import_conda_project_rejects_external_environment_files(
    tmp_path: Path,
    env_file_template: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-env.yml"
    outside.write_text(_CONDA_PROJECT_ENV_YML, encoding="utf-8")
    env_file = env_file_template.format(outside=outside)
    manifest = project / "conda-project.yml"
    manifest.write_text(
        f"""\
name: cp-demo
environments:
  default:
    - {env_file}
""",
        encoding="utf-8",
    )

    with pytest.raises(ManifestImportError, match="escapes the project directory"):
        find_importer(manifest).convert(manifest)


def test_import_conda_project_rejects_environment_file_symlink_escape(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (outside / "environment.yml").write_text(
        _CONDA_PROJECT_ENV_YML,
        encoding="utf-8",
    )
    try:
        (project / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    manifest = project / "conda-project.yml"
    manifest.write_text(
        """\
name: cp-demo
environments:
  default:
    - linked/environment.yml
""",
        encoding="utf-8",
    )

    with pytest.raises(ManifestImportError, match="escapes the project directory"):
        find_importer(manifest).convert(manifest)


def test_env_yml_dependencies(tmp_path: Path) -> None:
    p = tmp_path / "environment.yml"
    p.write_text(_ENVIRONMENT_YML, encoding="utf-8")
    doc = find_importer(p).convert(p)
    assert "python" in doc["dependencies"]
    assert "numpy" in doc["dependencies"]
    assert "requests" in doc["pypi-dependencies"]


def test_ap_commands_become_tasks(tmp_path: Path) -> None:
    p = tmp_path / "anaconda-project.yml"
    p.write_text(_ANACONDA_PROJECT_YML, encoding="utf-8")
    doc = find_importer(p).convert(p)
    assert "serve" in doc["tasks"]


@pytest.mark.parametrize(
    ("content", "task_name", "expected_cmd"),
    [
        pytest.param(
            """\
name: ap-demo
commands:
  view:
    notebook: "notebook.ipynb; echo NOTEBOOK_PWN"
""",
            "view",
            quote_for_shell("jupyter", "notebook", "notebook.ipynb; echo NOTEBOOK_PWN"),
            id="notebook",
        ),
        pytest.param(
            """\
name: ap-demo
commands:
  serve:
    bokeh_app: "apps/main.py; echo BOKEH_PWN"
""",
            "serve",
            quote_for_shell("bokeh", "serve", "apps/main.py; echo BOKEH_PWN"),
            id="bokeh",
        ),
        pytest.param(
            """\
name: ap-demo
downloads:
  "data; echo NAME_PWN":
    url: "https://example.invalid/file.csv; echo URL_PWN"
""",
            "download-data; echo name-pwn",
            quote_for_shell(
                "curl",
                "-fsSL",
                "-o",
                "data; echo name-pwn",
                "https://example.invalid/file.csv; echo URL_PWN",
            ),
            id="download",
        ),
    ],
)
def test_anaconda_project_import_quotes_data_task_fields(
    tmp_path: Path,
    content: str,
    task_name: str,
    expected_cmd: str,
) -> None:
    p = tmp_path / "anaconda-project.yml"
    p.write_text(content, encoding="utf-8")

    doc = find_importer(p).convert(p)
    task = doc["tasks"][task_name]
    cmd = task if isinstance(task, str) else task["cmd"]

    assert cmd == expected_cmd


def test_anaconda_project_imported_data_task_reaches_shell_as_data(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorder = bin_dir / "jupyter.py"
    recorder.write_text(
        """\
import json
import sys
from pathlib import Path

Path("argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
""",
        encoding="utf-8",
    )
    if on_win:
        tool = bin_dir / "jupyter.bat"
        tool.write_text(f'@echo off\n"{sys.executable}" "{recorder}" %*\n')
        separator = "&"
    else:
        tool = bin_dir / "jupyter"
        tool.write_text(f"#!{sys.executable}\n{recorder.read_text(encoding='utf-8')}")
        tool.chmod(0o755)
        separator = ";"

    payload = f"notebook.ipynb {separator} echo PWNED > pwned.txt"
    p = tmp_path / "anaconda-project.yml"
    p.write_text(
        f"""\
name: ap-demo
commands:
  view:
    notebook: "{payload}"
""",
        encoding="utf-8",
    )
    doc = find_importer(p).convert(p)
    cmd = doc["tasks"]["view"]

    exit_code = SubprocessShell().run(
        cmd,
        {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
        tmp_path,
    )

    assert exit_code == 0
    assert json.loads((tmp_path / "argv.json").read_text(encoding="utf-8")) == [
        "notebook",
        payload,
    ]
    assert not (tmp_path / "pwned.txt").exists()


@pytest.mark.parametrize(
    ("command_field", "command"),
    [
        ("unix", "python serve.py; echo EXPLICIT_UNIX"),
        ("windows", "python serve.py & echo EXPLICIT_WINDOWS"),
    ],
    ids=["unix", "windows"],
)
def test_anaconda_project_import_preserves_explicit_commands(
    tmp_path: Path,
    command_field: str,
    command: str,
) -> None:
    p = tmp_path / "anaconda-project.yml"
    p.write_text(
        f"""\
name: ap-demo
commands:
  serve:
    {command_field}: "{command}"
""",
        encoding="utf-8",
    )

    doc = find_importer(p).convert(p)

    assert doc["tasks"]["serve"] == command


def test_pixi_tasks_preserved(tmp_path: Path) -> None:
    p = tmp_path / "pixi.toml"
    p.write_text(_PIXI_TOML, encoding="utf-8")
    doc = find_importer(p).convert(p)
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
