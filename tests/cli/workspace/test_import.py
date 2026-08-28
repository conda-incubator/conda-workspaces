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
from conda.base.context import context as conda_context
from conda.exceptions import DryRunExit
from conda.models.channel import Channel
from conda.utils import quote_for_shell
from rich.console import Console

import conda_workspaces.cli.workspace.import_manifest as import_manifest_mod
from conda_workspaces.cli.main import execute_workspace, generate_workspace_parser
from conda_workspaces.cli.workspace.import_manifest import execute_import
from conda_workspaces.exceptions import (
    CondaWorkspacesError,
    ManifestImportError,
    WorkspaceNotFoundError,
    WorkspaceParseError,
)
from conda_workspaces.importers import EnvironmentYmlImporter, find_importer
from conda_workspaces.importers import base as importer_base
from conda_workspaces.importers.serialize import config_to_toml
from conda_workspaces.manifests import find_parser as find_manifest_parser
from conda_workspaces.models import WorkspaceConfig
from conda_workspaces.runner import SubprocessShell

from ..conftest import make_args

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_DEFAULTS = {
    "manifest_file": None,
    "environment": None,
    "output": None,
    "no_install": False,
    "no_lockfile_update": False,
    "force_reinstall": False,
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
    ("filename", "original", "replacement", "task_name"),
    [
        pytest.param(
            "pixi.toml",
            _PIXI_TOML,
            _PIXI_TOML.replace("pixi-demo", "replacement").replace(
                'build = "python -m build"', 'changed = "echo changed"'
            ),
            "build",
            id="pixi",
        ),
        pytest.param(
            "pyproject.toml",
            _PYPROJECT_TOML,
            _PYPROJECT_TOML.replace("pyproject-demo", "replacement").replace(
                'lint = "ruff check ."', 'changed = "echo changed"'
            ),
            "lint",
            id="pyproject",
        ),
    ],
)
def test_toml_import_uses_one_source_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    original: str,
    replacement: str,
    task_name: str,
) -> None:
    path = tmp_path / filename
    path.write_text(original, encoding="utf-8")
    parser = find_importer(path)
    manifest_parser = find_manifest_parser(path)
    read_manifest_text = manifest_parser.read_manifest_text
    reads = 0

    def replace_after_read(candidate: Path) -> str:
        nonlocal reads
        reads += 1
        content = read_manifest_text(candidate)
        candidate.write_text(replacement, encoding="utf-8")
        return content

    monkeypatch.setattr(
        type(manifest_parser),
        "read_manifest_text",
        staticmethod(replace_after_read),
    )

    doc = parser.convert(path)

    assert reads == 1
    assert doc["workspace"]["name"] != "replacement"
    assert task_name in doc["tasks"]
    assert "changed" not in doc["tasks"]


@pytest.mark.parametrize(
    ("dependency_yaml", "message"),
    [
        (
            "  - https://user:password@packages.test/private/acme-runtime-1.0-0.conda",
            "direct conda package sources",
        ),
        (
            (
                "  - pip:\n"
                "      - acme-client @ https://user:password@packages.test/private/"
                "acme_client-1.0-py3-none-any.whl"
            ),
            "direct PyPI package sources",
        ),
    ],
    ids=["conda", "pypi"],
)
def test_environment_yml_import_rejects_lossy_direct_sources(
    tmp_path: Path,
    dependency_yaml: str,
    message: str,
) -> None:
    path = tmp_path / "environment.yml"
    path.write_text(
        f"name: secure\nchannels:\n  - conda-forge\ndependencies:\n{dependency_yaml}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message) as exc_info:
        find_importer(path).convert(path)

    assert "user" not in str(exc_info.value)
    assert "password" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("dependencies_yaml", "message"),
    [
        ("dependencies: python>=3.12\n", "dependencies must be a list"),
        (
            "dependencies:\n  - pip:\n      requests>=2\n",
            "pip dependencies must be a list",
        ),
    ],
    ids=["dependencies-scalar", "pip-scalar"],
)
def test_environment_yml_import_rejects_scalar_dependency_collections(
    tmp_path: Path,
    dependencies_yaml: str,
    message: str,
) -> None:
    path = tmp_path / "environment.yml"
    path.write_text(f"name: bounded\n{dependencies_yaml}", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        find_importer(path).convert(path)


@pytest.mark.parametrize(
    "dependency_yaml",
    [
        "  - bad name https://user:password@packages.test/private/pkg.conda",
        (
            "  - pip:\n"
            "      - bad name @ https://user:password@packages.test/private/pkg.whl"
        ),
    ],
    ids=["conda", "pypi"],
)
def test_environment_yml_import_does_not_echo_malformed_source_credentials(
    tmp_path: Path,
    dependency_yaml: str,
) -> None:
    path = tmp_path / "environment.yml"
    path.write_text(
        f"name: secure\ndependencies:\n{dependency_yaml}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        find_importer(path).convert(path)

    assert "user" not in str(exc_info.value)
    assert "password" not in str(exc_info.value)


@pytest.mark.parametrize(
    "qualifier",
    ["private", "https://packages.example.test/private"],
    ids=["named-channel", "url-channel"],
)
@pytest.mark.parametrize(
    "manifest_kind",
    ["environment", "anaconda-project", "conda-project"],
)
def test_yaml_import_preserves_channel_qualified_dependencies(
    tmp_path: Path,
    qualifier: str,
    manifest_kind: str,
) -> None:
    dependency = f"{qualifier}::internal-runtime>=1"
    if manifest_kind == "environment":
        path = tmp_path / "environment.yml"
        path.write_text(
            f"name: secure\ndependencies:\n  - {dependency}\n",
            encoding="utf-8",
        )
    elif manifest_kind == "anaconda-project":
        path = tmp_path / "anaconda-project.yml"
        path.write_text(
            f"name: secure\npackages:\n  - {dependency}\n",
            encoding="utf-8",
        )
    else:
        environment_path = tmp_path / "environment.yml"
        environment_path.write_text(
            f"name: secure\ndependencies:\n  - {dependency}\n",
            encoding="utf-8",
        )
        path = tmp_path / "conda-project.yml"
        path.write_text(
            "name: secure\nenvironments:\n  default:\n    - environment.yml\n",
            encoding="utf-8",
        )

    doc = find_importer(path).convert(path)

    imported = doc["dependencies"]["internal-runtime"]
    assert imported["channel"] == qualifier
    assert imported["version"] == ">=1"


@pytest.mark.parametrize(
    "manifest_kind",
    ["environment", "anaconda-project", "conda-project"],
)
def test_yaml_import_preserves_conda_fields_and_pypi_extras(
    tmp_path: Path,
    manifest_kind: str,
) -> None:
    digest = "a" * 64
    packages = (
        "  - \"internal-runtime[version='1.0',build='secure_0',"
        f"sha256='{digest}']\"\n"
        "  - pip:\n"
        "      - secure-client[crypto]>=2\n"
    )
    if manifest_kind == "environment":
        path = tmp_path / "environment.yml"
        path.write_text(
            f"name: secure\ndependencies:\n{packages}",
            encoding="utf-8",
        )
    elif manifest_kind == "anaconda-project":
        path = tmp_path / "anaconda-project.yml"
        path.write_text(
            f"name: secure\npackages:\n{packages}",
            encoding="utf-8",
        )
    else:
        (tmp_path / "environment.yml").write_text(
            f"name: secure\ndependencies:\n{packages}",
            encoding="utf-8",
        )
        path = tmp_path / "conda-project.yml"
        path.write_text(
            "name: secure\nenvironments:\n  default:\n    - environment.yml\n",
            encoding="utf-8",
        )

    doc = find_importer(path).convert(path)

    conda_dependency = doc["dependencies"]["internal-runtime"]
    pypi_dependency = doc["pypi-dependencies"]["secure-client"]
    assert conda_dependency["version"] == "1.0"
    assert conda_dependency["build"] == "secure_0"
    assert conda_dependency["sha256"] == digest
    assert pypi_dependency["version"] == ">=2"
    assert pypi_dependency["extras"] == ["crypto"]


@pytest.mark.parametrize(
    "manifest_kind",
    ["environment", "anaconda-project", "conda-project"],
)
def test_yaml_import_rejects_pypi_markers(
    tmp_path: Path,
    manifest_kind: str,
) -> None:
    packages = "  - pip:\n      - \"secure-client>=2; python_version < '3.13'\"\n"
    if manifest_kind == "environment":
        path = tmp_path / "environment.yml"
        path.write_text(
            f"name: secure\ndependencies:\n{packages}",
            encoding="utf-8",
        )
    elif manifest_kind == "anaconda-project":
        path = tmp_path / "anaconda-project.yml"
        path.write_text(
            f"name: secure\npackages:\n{packages}",
            encoding="utf-8",
        )
    else:
        (tmp_path / "environment.yml").write_text(
            f"name: secure\ndependencies:\n{packages}",
            encoding="utf-8",
        )
        path = tmp_path / "conda-project.yml"
        path.write_text(
            "name: secure\nenvironments:\n  default:\n    - environment.yml\n",
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="cannot represent environment markers"):
        find_importer(path).convert(path)


def test_environment_yml_import_preserves_safe_direct_conda_source(
    tmp_path: Path,
) -> None:
    url = "https://packages.example.test/linux-64/acme-runtime-1.0-0.conda"
    path = tmp_path / "environment.yml"
    path.write_text(
        f"name: secure\ndependencies:\n  - {url}\n",
        encoding="utf-8",
    )

    dependency = find_importer(path).convert(path)["dependencies"]["acme-runtime"]

    assert dependency["url"] == url
    assert dependency["subdir"] == "linux-64"


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        pytest.param(
            "environment.yml",
            """\
name: secure
channels:
  - https://user:password@packages.test/t/secret/private?token=secret#metadata
  - t/relative-secret/private
""",
            id="environment-yml",
        ),
        pytest.param(
            "anaconda-project.yml",
            """\
name: secure
channels:
  - https://user:password@packages.test/t/secret/private?token=secret#metadata
  - t/relative-secret/private
""",
            id="anaconda-project",
        ),
    ],
)
def test_yaml_import_redacts_channel_credentials(
    tmp_path: Path,
    filename: str,
    content: str,
) -> None:
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")

    doc = find_importer(path).convert(path)

    assert doc["workspace"]["channels"] == [
        "https://packages.test/private",
        "https://conda.anaconda.org/private",
    ]


def test_yaml_import_rejects_oversized_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer_base, "MAX_MANIFEST_BYTES", 8)
    path = tmp_path / "environment.yml"
    path.write_text("name: oversized\n", encoding="utf-8")

    with pytest.raises(ManifestImportError, match="maximum size"):
        find_importer(path).convert(path)


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
@pytest.mark.parametrize(
    "content",
    [
        pytest.param(
            """\
name: secure
commands:
  leak: "echo https://user:password@packages.test/file"
""",
            id="command",
        ),
        pytest.param(
            """\
name: secure
commands:
  leak:
    unix: echo ok
    variables:
      SOURCE_URL: https://user:password@packages.test/file
""",
            id="environment",
        ),
    ],
)
def test_execute_import_rejects_credentials_in_rendered_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    dry_run: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "anaconda-project.yml"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(WorkspaceParseError, match="embedded URL credentials"):
        execute_import(
            make_args(_DEFAULTS, file=source, dry_run=dry_run),
            console=Console(file=StringIO(), width=200),
        )

    assert not (tmp_path / "conda.toml").exists()


def test_conda_project_import_redacts_environment_channel_credentials(
    tmp_path: Path,
) -> None:
    project = tmp_path / "conda-project.yml"
    project.write_text(_CONDA_PROJECT_YML, encoding="utf-8")
    (tmp_path / "environment.yml").write_text(
        """\
name: secure
channels:
  - https://user:password@packages.test/t/secret/private?token=secret#metadata
  - t/relative-secret/private
""",
        encoding="utf-8",
    )

    doc = find_importer(project).convert(project)

    assert doc["workspace"]["channels"] == [
        "https://packages.test/private",
        "https://conda.anaconda.org/private",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@packages.test/t/secret/file.txt?token=secret#metadata",
        "t/VALIDATION-LEAK/private/file.txt",
    ],
    ids=["absolute", "relative-token"],
)
def test_anaconda_project_import_rejects_download_credentials(
    tmp_path: Path,
    url: str,
) -> None:
    path = tmp_path / "anaconda-project.yml"
    path.write_text(
        f"""name: secure
downloads:
  artifact:
    url: {url}
""",
        encoding="utf-8",
    )

    with pytest.raises(ManifestImportError) as caught:
        find_importer(path).convert(path)

    message = str(caught.value)
    for secret in ("user", "password", "secret", "VALIDATION-LEAK"):
        assert secret not in message


@pytest.mark.parametrize(
    ("filename", "namespace"),
    [
        pytest.param("pixi.toml", "", id="pixi"),
        pytest.param("pyproject.toml", "tool.conda.", id="pyproject"),
    ],
)
def test_import_preserves_and_redacts_source_channel_identity(
    tmp_path: Path,
    filename: str,
    namespace: str,
) -> None:
    project = '[project]\nname = "channel-import"\n\n' if namespace else ""
    path = tmp_path / filename
    path.write_text(
        f"""{project}[{namespace}workspace]
name = "channel-import"
channels = [
  "conda-forge",
  "https://user:password@conda.anaconda.org/t/secret/private/label/dev?token=secret#metadata",
  "t/relative-secret/private",
]
platforms = ["linux-64"]

[{namespace}dependencies]
named = {{ version = ">=1", channel = "conda-forge" }}
private = {{ version = ">=1", channel = "https://u:p@packages.test/t/s/private?x=y#z" }}
relative = {{ version = ">=1", channel = "t/dependency-secret/private" }}

[{namespace}feature.tools]
channels = [
  {{ channel = "bioconda", priority = 1 }},
  "https://user:password@packages.example.test/t/secret/team/channel?token=secret#metadata",
  {{ channel = "t/feature-secret/private", priority = 2 }},
]

[{namespace}environments.tools]
features = ["tools"]
""",
        encoding="utf-8",
    )

    doc = find_importer(path).convert(path)

    assert doc["workspace"]["channels"] == [
        "conda-forge",
        "https://conda.anaconda.org/private/label/dev",
        "https://conda.anaconda.org/private",
    ]
    assert doc["feature"]["tools"]["channels"] == [
        "bioconda",
        "https://packages.example.test/team/channel",
        "https://conda.anaconda.org/private",
    ]
    assert doc["dependencies"]["named"]["channel"] == "conda-forge"
    assert doc["dependencies"]["private"]["channel"] == "https://packages.test/private"
    assert doc["dependencies"]["relative"]["channel"] == (
        "https://conda.anaconda.org/private"
    )


@pytest.mark.parametrize(
    ("filename", "namespace"),
    [
        pytest.param("pixi.toml", "", id="pixi"),
        pytest.param("pyproject.toml", "tool.conda.", id="pyproject"),
    ],
)
@pytest.mark.parametrize(
    "table",
    [
        "pypi-dependencies",
        "feature.dev.pypi-dependencies",
        "environments.dev.pypi-dependencies",
        "target.linux-64.pypi-dependencies",
    ],
    ids=["default", "feature", "environment", "target"],
)
def test_import_rejects_credentialed_pypi_direct_urls(
    tmp_path: Path,
    filename: str,
    namespace: str,
    table: str,
) -> None:
    project = '[project]\nname = "pypi-import"\n\n' if namespace else ""
    credential_url = (
        "https://user:password@packages.test/t/secret/"
        "pkg.whl?token=secret#sha256=deadbeef"
    )
    path = tmp_path / filename
    path.write_text(
        f"""{project}[{namespace}workspace]
name = "pypi-import"
channels = ["conda-forge"]
platforms = ["linux-64"]

[{namespace}{table}]
artifact = {{ url = {json.dumps(credential_url)} }}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="direct URL that cannot be written safely"):
        find_importer(path).convert(path)


@pytest.mark.parametrize(
    "value",
    [
        "https://user:password@packages.test/t/secret/pkg.whl?token=secret#fragment",
        " @ https://user:password@packages.test/t/secret/pkg.whl?token=secret#fragment",
    ],
    ids=["bare-url", "pep508-tail"],
)
def test_import_rejects_credentialed_url_in_pypi_version(
    tmp_path: Path,
    value: str,
) -> None:
    path = tmp_path / "pixi.toml"
    path.write_text(
        f"""[workspace]
name = "pypi-import"
channels = ["conda-forge"]
platforms = ["linux-64"]

[pypi-dependencies]
artifact = {json.dumps(value)}
""",
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceParseError, match="credential-bearing URL"):
        find_importer(path).convert(path)


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        pytest.param(
            "conda-forge",
            "https://conda.anaconda.org/conda-forge",
            id="named",
        ),
        pytest.param(
            "HTTPS://user:password@packages.test/t/secret/private?x=y#z",
            "HTTPS://packages.test/private",
            id="uppercase-url",
        ),
        pytest.param(
            "t/SENSITIVE-VALUE/private",
            "https://conda.anaconda.org/private",
            id="relative-token",
        ),
    ],
)
def test_config_to_toml_serializes_channel(channel: str, expected: str) -> None:
    config = WorkspaceConfig(channels=[Channel(channel)])

    doc = config_to_toml(config)

    assert doc["workspace"]["channels"] == [expected]


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


@pytest.mark.parametrize(
    ("filename", "prefix"),
    [
        ("pixi.toml", ""),
        ("pyproject.toml", "tool.conda."),
    ],
    ids=["pixi", "pyproject"],
)
def test_import_preserves_environment_local_dependencies(
    tmp_path: Path,
    filename: str,
    prefix: str,
) -> None:
    project = '[project]\nname = "import-test"\n\n' if prefix else ""
    path = tmp_path / filename
    path.write_text(
        f"""{project}[{prefix}workspace]
name = "import-test"
channels = ["conda-forge"]
platforms = ["linux-64"]

[{prefix}workspace.dependencies]
inherited = {{ version = ">=4", build = "base*" }}

[{prefix}dependencies]
inherited = {{ workspace = true }}

[{prefix}pypi-dependencies]
root-package = {{ git = "https://example.com/root.git", branch = "main" }}

[{prefix}target.linux-64.dependencies]
root-target = {{ version = ">=1", build = "py*", channel = "conda-forge" }}

[{prefix}target.linux-64.pypi-dependencies]
root-target-package = {{ git = "https://example.com/root-target.git", branch = "main" }}

[{prefix}feature.shared.pypi-dependencies]
feature-package = {{ git = "https://example.com/feature.git", tag = "v1.0" }}

[{prefix}feature.shared.target.linux-64.dependencies]
feature-target = {{ version = ">=2", build-number = ">=1" }}

[{prefix}feature.shared.target.linux-64.pypi-dependencies]
feature-target-package = {{ git = "https://example.com/ft.git", tag = "v2.0" }}

[{prefix}environments.default]
no-default-feature = true

[{prefix}environments.qa.dependencies]
coverage = {{ version = ">=7", channel = "conda-forge" }}
inherited = {{ workspace = true }}

[{prefix}environments.qa.pypi-dependencies]
pytest-plugin = {{ version = ">=1", extras = ["reports"] }}
branch-package = {{ git = "https://example.com/branch.git", branch = "main" }}
tag-package = {{ git = "https://example.com/tag.git", tag = "v1.0" }}
rev-package = {{ git = "https://example.com/rev.git", rev = "abc123" }}

[{prefix}environments.qa.target.linux-64.dependencies]
environment-target = {{ version = ">=3", build = "h*" }}
inherited = {{ workspace = true, build = "env*" }}

[{prefix}environments.qa.target.linux-64.pypi-dependencies]
environment-target-package = {{ git = "https://example.com/et.git", rev = "def456" }}
""",
        encoding="utf-8",
    )

    doc = find_importer(path).convert(path)

    assert doc["workspace"]["dependencies"]["inherited"].unwrap() == {
        "version": ">=4",
        "build": "base*",
    }
    assert doc["dependencies"]["inherited"].unwrap() == {"workspace": True}
    assert doc["pypi-dependencies"]["root-package"].unwrap() == {
        "git": "https://example.com/root.git",
        "branch": "main",
    }
    assert doc["target"]["linux-64"]["dependencies"]["root-target"].unwrap() == {
        "version": ">=1",
        "build": "py*",
        "channel": "conda-forge",
    }
    assert doc["target"]["linux-64"]["pypi-dependencies"][
        "root-target-package"
    ].unwrap() == {
        "git": "https://example.com/root-target.git",
        "branch": "main",
    }
    assert doc["feature"]["shared"]["pypi-dependencies"][
        "feature-package"
    ].unwrap() == {
        "git": "https://example.com/feature.git",
        "tag": "v1.0",
    }
    feature_target = doc["feature"]["shared"]["target"]["linux-64"]
    assert feature_target["dependencies"]["feature-target"].unwrap() == {
        "version": ">=2",
        "build-number": ">=1",
    }
    assert feature_target["pypi-dependencies"]["feature-target-package"].unwrap() == {
        "git": "https://example.com/ft.git",
        "tag": "v2.0",
    }
    assert doc["environments"]["default"]["no-default-feature"] is True
    environment = doc["environments"]["qa"]
    assert environment["dependencies"]["coverage"].unwrap() == {
        "version": ">=7",
        "channel": "conda-forge",
    }
    assert environment["dependencies"]["inherited"].unwrap() == {"workspace": True}
    assert environment["pypi-dependencies"]["pytest-plugin"].unwrap() == {
        "version": ">=1",
        "extras": ["reports"],
    }
    assert environment["pypi-dependencies"]["branch-package"].unwrap() == {
        "git": "https://example.com/branch.git",
        "branch": "main",
    }
    assert environment["pypi-dependencies"]["tag-package"].unwrap() == {
        "git": "https://example.com/tag.git",
        "tag": "v1.0",
    }
    assert environment["pypi-dependencies"]["rev-package"].unwrap() == {
        "git": "https://example.com/rev.git",
        "rev": "abc123",
    }
    environment_target = environment["target"]["linux-64"]
    assert environment_target["dependencies"]["environment-target"].unwrap() == {
        "version": ">=3",
        "build": "h*",
    }
    assert environment_target["dependencies"]["inherited"].unwrap() == {
        "workspace": True,
        "build": "env*",
    }
    assert environment_target["pypi-dependencies"][
        "environment-target-package"
    ].unwrap() == {
        "git": "https://example.com/et.git",
        "rev": "def456",
    }


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


@pytest.mark.parametrize(
    "initially_exists",
    [False, True],
    ids=["absent-created", "existing-replaced"],
)
def test_execute_import_rejects_output_changed_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initially_exists: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "environment.yml"
    source.write_text(_ENVIRONMENT_YML, encoding="utf-8")
    output = tmp_path / "conda.toml"
    if initially_exists:
        output.write_text("# original\n", encoding="utf-8")

        def replace_during_prompt(message: str) -> None:
            output.unlink()
            output.write_text("# concurrent\n", encoding="utf-8")

        monkeypatch.setattr(import_manifest_mod, "confirm_yn", replace_during_prompt)
    else:
        atomic_write_text = import_manifest_mod.atomic_write_text

        def create_before_publication(
            path: Path,
            content: str,
            **kwargs: object,
        ) -> None:
            path.write_text("# concurrent\n", encoding="utf-8")
            atomic_write_text(path, content, **kwargs)

        monkeypatch.setattr(
            import_manifest_mod,
            "atomic_write_text",
            create_before_publication,
        )

    with pytest.raises(ValueError, match="changed before writing"):
        execute_import(
            make_args(_DEFAULTS, file=source, output=output),
            console=Console(file=StringIO(), width=200),
        )

    assert output.read_text(encoding="utf-8") == "# concurrent\n"


@pytest.fixture
def named_import_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Create a workspace used by named import lifecycle tests."""
    path = tmp_path / "conda.toml"
    path.write_text(
        """\
# Preserve this comment.
[workspace]
name = "named-import"
channels = ["conda-forge", "bioconda"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.11"

[tasks]
check = "python -V"
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return path


@pytest.fixture
def named_import_sync_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    """Record named import sync calls and publish a synthetic lockfile."""
    calls: list[dict[str, object]] = []

    def record_sync(
        _config: object,
        _ctx: object,
        env_names: list[str],
        *,
        no_install: bool,
        force_reinstall: bool,
        dry_run: bool,
        publish_lockfile: Callable[[str], None] | None,
        validate_workspace: Callable[[], None] | None,
        require_absent_prefixes: list[str],
        console: Console,
    ) -> None:
        assert validate_workspace is not None
        validate_workspace()
        calls.append(
            {
                "env_names": list(env_names),
                "no_install": no_install,
                "force_reinstall": force_reinstall,
                "dry_run": dry_run,
                "require_absent_prefixes": list(require_absent_prefixes),
            }
        )
        assert console is not None
        if not dry_run:
            assert publish_lockfile is not None
            publish_lockfile("rendered-lock")

    monkeypatch.setattr(import_manifest_mod, "sync_environments", record_sync)
    return calls


@pytest.mark.parametrize(
    ("filename", "namespace"),
    [
        pytest.param("conda.toml", "", id="conda-toml"),
        pytest.param("pixi.toml", "", id="pixi-toml"),
        pytest.param("pyproject.toml", "tool.conda.", id="pyproject-conda"),
        pytest.param("pyproject.toml", "tool.pixi.", id="pyproject-pixi"),
    ],
)
def test_execute_named_import_adds_private_environment_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    namespace: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    project = '[project]\nname = "peer-project"\n\n' if namespace else ""
    manifest = tmp_path / filename
    manifest.write_text(
        f"""{project}# Keep this declaration.
[{namespace}workspace]
name = "import-target"
channels = ["conda-forge"]
platforms = ["linux-64"]

[{namespace}dependencies]
python = ">=3.11"

[{namespace}tasks]
keep = "python -V"
""",
        encoding="utf-8",
    )
    source = tmp_path / "environment.yaml"
    source.write_text(
        """\
name: ignored-source-name
prefix: /ignored/source/prefix
channels:
  - conda-forge
platforms:
  - linux-64
dependencies:
  - python>=3.12
  - numpy>=2
  - pip:
      - requests[security]>=2
""",
        encoding="utf-8",
    )
    output = StringIO()

    result = execute_import(
        make_args(
            _DEFAULTS,
            file=source,
            manifest_file=manifest,
            environment="qa",
            no_lockfile_update=True,
        ),
        console=Console(file=output, width=200),
    )

    assert result == 0
    text = manifest.read_text(encoding="utf-8")
    assert "# Keep this declaration." in text
    doc = tomlkit.parse(text)
    workspace = doc
    if namespace:
        tool_name = namespace.split(".")[1]
        workspace = doc["tool"][tool_name]
        assert doc["project"]["name"] == "peer-project"
    assert workspace["tasks"]["keep"] == "python -V"
    assert workspace["environments"]["default"] == []
    imported = workspace["environments"]["qa"]
    assert imported["no-default-feature"] is True
    assert set(imported["dependencies"]) == {"python", "numpy"}
    assert set(imported["pypi-dependencies"]) == {"requests"}
    assert "feature" not in workspace
    assert not (tmp_path / ".conda" / "envs" / "qa").exists()
    rendered = output.getvalue()
    assert "ignoring the environment.yml name" in rendered
    assert "ignoring the environment.yml prefix" in rendered


def test_parse_named_environment_preserves_absent_workspace_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text("dependencies:\n  - python>=3.12\n", encoding="utf-8")

    imported = EnvironmentYmlImporter().parse_named_environment(source)

    assert imported.channels is None
    assert imported.platforms is None
    assert set(imported.conda_dependencies) == {"python"}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        pytest.param("variables: {}\n", "variables cannot be imported", id="variables"),
        pytest.param(
            "unknown: true\n", "Unsupported environment.yml fields", id="unknown"
        ),
        pytest.param(
            "channels: conda-forge\n", "channels must be a list", id="channels"
        ),
        pytest.param(
            "platforms: linux-64\n", "platforms must be a list", id="platforms"
        ),
        pytest.param("name: [bad]\n", "name must be a string", id="name"),
        pytest.param("prefix: 42\n", "prefix must be a string", id="prefix"),
    ],
)
def test_parse_named_environment_rejects_unrepresentable_fields(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ManifestImportError, match=message):
        EnvironmentYmlImporter().parse_named_environment(source)


@pytest.mark.parametrize(
    ("declarations", "message"),
    [
        pytest.param("", None, id="inherit-omitted"),
        pytest.param(
            "channels:\n  - conda-forge\n  - bioconda\n"
            "platforms:\n  - linux-64\n  - osx-arm64\n",
            None,
            id="exact",
        ),
        pytest.param(
            "channels:\n  - https://conda.anaconda.org/conda-forge\n  - bioconda\n",
            None,
            id="normalized-channel",
        ),
        pytest.param(
            "channels:\n  - bioconda\n  - conda-forge\n",
            "channels must match",
            id="channel-order",
        ),
        pytest.param("channels: []\n", "channels must match", id="empty-channels"),
        pytest.param(
            "channels:\n  - conda-forge\n  - nodefaults\n",
            "nodefaults",
            id="nodefaults",
        ),
        pytest.param(
            "platforms:\n  - osx-arm64\n  - linux-64\n",
            None,
            id="platform-order",
        ),
        pytest.param(
            "platforms:\n  - linux-64\n  - win-64\n",
            "platforms must match",
            id="platform-name",
        ),
        pytest.param(
            "platforms: []\n",
            "platforms must match",
            id="empty-platforms",
        ),
    ],
)
def test_execute_named_import_validates_workspace_channels_and_platforms(
    named_import_workspace: Path,
    tmp_path: Path,
    declarations: str,
    message: str | None,
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text(
        f"dependencies:\n  - python>=3.12\n{declarations}",
        encoding="utf-8",
    )
    before = named_import_workspace.read_bytes()
    args = make_args(
        _DEFAULTS,
        file=source,
        environment="qa",
        no_lockfile_update=True,
    )

    if message is None:
        assert execute_import(args, console=Console(file=StringIO())) == 0
        assert (
            "qa"
            in tomlkit.parse(named_import_workspace.read_text(encoding="utf-8"))[
                "environments"
            ]
        )
    else:
        with pytest.raises(ManifestImportError, match=message):
            execute_import(args, console=Console(file=StringIO()))
        assert named_import_workspace.read_bytes() == before


@pytest.mark.parametrize(
    ("environment", "manifest_environment", "message"),
    [
        pytest.param("default", None, "already defined", id="implicit-default"),
        pytest.param("qa", "qa", "already defined", id="existing"),
        pytest.param("QA", "qa", "conflicts with environment", id="portable-collision"),
        pytest.param("../qa", None, "not valid", id="invalid-name"),
    ],
)
def test_execute_named_import_requires_a_new_portable_environment_name(
    named_import_workspace: Path,
    tmp_path: Path,
    environment: str,
    manifest_environment: str | None,
    message: str,
) -> None:
    if manifest_environment is not None:
        with named_import_workspace.open("a", encoding="utf-8") as stream:
            stream.write(f"\n[environments.{manifest_environment}]\n")
    source = tmp_path / "environment.yml"
    source.write_text("dependencies: []\n", encoding="utf-8")
    before = named_import_workspace.read_bytes()

    with pytest.raises(CondaWorkspacesError, match=message):
        execute_import(
            make_args(
                _DEFAULTS,
                file=source,
                environment=environment,
                no_lockfile_update=True,
            ),
            console=Console(file=StringIO()),
        )

    assert named_import_workspace.read_bytes() == before


def test_execute_named_import_existing_environment_hint_keeps_manifest_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    selected_directory = tmp_path / "selected workspace"
    selected_directory.mkdir()
    manifest = selected_directory / "conda.toml"
    environment = "qa $(echo)"
    manifest.write_text(
        f'''\
[workspace]
name = "selected"
channels = []
platforms = ["linux-64"]

[environments."{environment}"]
''',
        encoding="utf-8",
    )
    source = tmp_path / "environment.yml"
    source.write_text("dependencies: []\n", encoding="utf-8")

    with pytest.raises(CondaWorkspacesError, match="already defined") as exc_info:
        execute_import(
            make_args(
                _DEFAULTS,
                file=source,
                manifest_file=manifest,
                environment=environment,
                no_lockfile_update=True,
            ),
            console=Console(file=StringIO()),
        )

    command = quote_for_shell(
        "conda",
        "workspace",
        "--file",
        str(manifest),
        "install",
        "-e",
        environment,
    )
    assert any(command in hint for hint in exc_info.value.hints)


def test_execute_named_import_uses_exact_selected_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    conda_toml = tmp_path / "conda.toml"
    conda_toml.write_text(
        '[workspace]\nname = "first"\nchannels = []\nplatforms = ["linux-64"]\n',
        encoding="utf-8",
    )
    pixi_toml = tmp_path / "pixi.toml"
    pixi_toml.write_text(
        '[workspace]\nname = "selected"\nchannels = []\nplatforms = ["linux-64"]\n',
        encoding="utf-8",
    )
    source = tmp_path / "environment.yml"
    source.write_text("dependencies: []\n", encoding="utf-8")
    conda_before = conda_toml.read_bytes()

    execute_import(
        make_args(
            _DEFAULTS,
            file=source,
            manifest_file=pixi_toml,
            environment="qa",
            no_lockfile_update=True,
        ),
        console=Console(file=StringIO()),
    )

    assert conda_toml.read_bytes() == conda_before
    assert "qa" in tomlkit.parse(pixi_toml.read_text(encoding="utf-8"))["environments"]


def test_execute_named_import_preserves_inline_environment_table(
    named_import_workspace: Path,
    tmp_path: Path,
) -> None:
    named_import_workspace.write_text(
        """\
environments = { default = [] }

[workspace]
name = "inline-environments"
channels = []
platforms = ["linux-64"]
""",
        encoding="utf-8",
    )
    source = tmp_path / "environment.yml"
    source.write_text(
        "dependencies:\n  - python>=3.12\n  - pip:\n      - requests>=2\n",
        encoding="utf-8",
    )

    execute_import(
        make_args(
            _DEFAULTS,
            file=source,
            environment="qa",
            no_lockfile_update=True,
        ),
        console=Console(file=StringIO()),
    )

    doc = tomlkit.parse(named_import_workspace.read_text(encoding="utf-8"))
    assert doc["environments"]["default"] == []
    assert doc["environments"]["qa"]["no-default-feature"] is True
    assert "python" in doc["environments"]["qa"]["dependencies"]
    assert "requests" in doc["environments"]["qa"]["pypi-dependencies"]


def test_execute_whole_import_rejects_global_manifest_selection(
    named_import_workspace: Path,
    tmp_path: Path,
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text("dependencies: []\n", encoding="utf-8")

    with pytest.raises(CondaWorkspacesError, match="--file option requires"):
        execute_import(
            make_args(
                _DEFAULTS,
                file=source,
                manifest_file=named_import_workspace,
            ),
            console=Console(file=StringIO()),
        )


def test_execute_named_import_rejects_other_source_formats(
    named_import_workspace: Path,
    tmp_path: Path,
) -> None:
    source = tmp_path / "pixi.toml"
    source.write_text(
        '[workspace]\nname = "source"\nchannels = []\nplatforms = ["linux-64"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ManifestImportError, match="only supports environment"):
        execute_import(
            make_args(_DEFAULTS, file=source, environment="qa"),
            console=Console(file=StringIO()),
        )

    assert "qa" not in named_import_workspace.read_text(encoding="utf-8")


def test_execute_named_import_requires_existing_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "environment.yml"
    source.write_text("dependencies: []\n", encoding="utf-8")

    with pytest.raises(WorkspaceNotFoundError):
        execute_import(
            make_args(_DEFAULTS, file=source, environment="qa"),
            console=Console(file=StringIO()),
        )


@pytest.mark.parametrize(
    ("force_reinstall", "active", "message", "expected_calls"),
    [
        pytest.param(False, False, "prefix already exists", 0, id="reject"),
        pytest.param(True, False, None, 1, id="replace-inactive"),
        pytest.param(True, True, "Cannot replace active", 0, id="reject-active"),
    ],
)
def test_execute_named_import_handles_existing_target_prefix(
    named_import_workspace: Path,
    named_import_sync_calls: list[dict[str, object]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    force_reinstall: bool,
    active: bool,
    message: str | None,
    expected_calls: int,
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text("dependencies: []\n", encoding="utf-8")
    prefix = tmp_path / ".conda" / "envs" / "qa"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(import_manifest_mod, "is_active_prefix", lambda _path: active)
    args = make_args(
        _DEFAULTS,
        file=source,
        environment="qa",
        force_reinstall=force_reinstall,
    )

    if message is None:
        assert execute_import(args, console=Console(file=StringIO())) == 0
    else:
        with pytest.raises(CondaWorkspacesError, match=message):
            execute_import(args, console=Console(file=StringIO()))

    assert len(named_import_sync_calls) == expected_calls
    if named_import_sync_calls:
        assert named_import_sync_calls[0]["force_reinstall"] is True


def test_execute_named_import_rejects_active_missing_target_prefix(
    named_import_workspace: Path,
    named_import_sync_calls: list[dict[str, object]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text("dependencies: []\n", encoding="utf-8")
    monkeypatch.setattr(import_manifest_mod, "is_active_prefix", lambda _path: True)

    with pytest.raises(CondaWorkspacesError, match="active workspace environment"):
        execute_import(
            make_args(_DEFAULTS, file=source, environment="qa"),
            console=Console(file=StringIO()),
        )

    assert not named_import_sync_calls
    assert not (tmp_path / ".conda" / "envs" / "qa").exists()


def test_execute_named_import_rechecks_prefix_before_manifest_only_publication(
    named_import_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text("dependencies: []\n", encoding="utf-8")
    before = named_import_workspace.read_bytes()
    identities = iter((None, (1, 2)))
    monkeypatch.setattr(
        import_manifest_mod.LockfileInstallPlan,
        "prefix_identity",
        staticmethod(lambda _path: next(identities)),
    )

    with pytest.raises(CondaWorkspacesError, match="prefix appeared"):
        execute_import(
            make_args(
                _DEFAULTS,
                file=source,
                environment="qa",
                no_lockfile_update=True,
            ),
            console=Console(file=StringIO()),
        )

    assert named_import_workspace.read_bytes() == before


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param({"no_install": True}, id="no-install"),
        pytest.param({"no_lockfile_update": True}, id="no-lockfile-update"),
        pytest.param(
            {"no_install": True, "no_lockfile_update": True},
            id="both",
        ),
    ],
)
def test_execute_named_import_rejects_force_with_non_installing_modes(
    named_import_workspace: Path,
    tmp_path: Path,
    flags: dict[str, bool],
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text("dependencies: []\n", encoding="utf-8")

    with pytest.raises(CondaWorkspacesError, match="--force-reinstall cannot"):
        execute_import(
            make_args(
                _DEFAULTS,
                file=source,
                environment="qa",
                force_reinstall=True,
                **flags,
            ),
            console=Console(file=StringIO()),
        )


@pytest.mark.parametrize(
    ("flags", "expected_sync", "expected_lock", "expected_manifest", "sync_flags"),
    [
        pytest.param(
            {"no_lockfile_update": True},
            False,
            False,
            True,
            None,
            id="manifest-only",
        ),
        pytest.param(
            {"no_install": True},
            True,
            True,
            True,
            {"no_install": True, "dry_run": False},
            id="lock-without-install",
        ),
        pytest.param(
            {},
            True,
            True,
            True,
            {"no_install": False, "dry_run": False},
            id="lock-and-install",
        ),
        pytest.param(
            {"dry_run": True},
            True,
            False,
            False,
            {"no_install": False, "dry_run": True},
            id="dry-run",
        ),
    ],
)
def test_execute_named_import_lifecycle_modes(
    named_import_workspace: Path,
    named_import_sync_calls: list[dict[str, object]],
    tmp_path: Path,
    flags: dict[str, bool],
    expected_sync: bool,
    expected_lock: bool,
    expected_manifest: bool,
    sync_flags: dict[str, bool] | None,
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text(
        "dependencies:\n  - python>=3.12\n  - numpy\n  - pip:\n      - requests>=2\n",
        encoding="utf-8",
    )
    before = named_import_workspace.read_bytes()
    output = StringIO()

    result = execute_import(
        make_args(_DEFAULTS, file=source, environment="qa", **flags),
        console=Console(file=output, width=200),
    )

    assert result == 0
    assert bool(named_import_sync_calls) is expected_sync
    lockfile = tmp_path / "conda.lock"
    assert lockfile.exists() is expected_lock
    if expected_manifest:
        assert named_import_workspace.read_bytes() != before
        assert (
            "qa"
            in tomlkit.parse(named_import_workspace.read_text(encoding="utf-8"))[
                "environments"
            ]
        )
    else:
        assert named_import_workspace.read_bytes() == before
    if sync_flags is not None:
        assert {
            name: named_import_sync_calls[0][name] for name in sync_flags
        } == sync_flags
        assert named_import_sync_calls[0]["require_absent_prefixes"] == ["qa"]
    rendered = output.getvalue()
    assert "qa" in rendered
    assert "python" in rendered
    assert "numpy" in rendered
    assert "requests" in rendered


@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
def test_execute_named_import_json_keeps_stdout_machine_readable(
    named_import_workspace: Path,
    named_import_sync_calls: list[dict[str, object]],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text(
        "name: source-name\nprefix: /ignored\ndependencies:\n  - python>=3.12\n",
        encoding="utf-8",
    )
    arguments = [
        "--file",
        str(named_import_workspace),
        "import",
        "--environment",
        "qa",
        "--json",
    ]
    if dry_run:
        arguments.append("--dry-run")
    else:
        arguments.append("--no-lockfile-update")
    arguments.append(str(source))
    args = generate_workspace_parser().parse_args(arguments)

    with conda_context._override("json", True):
        result = execute_workspace(args)

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == {"success": True}
    assert "Imported" not in captured.out
    assert "Would import" not in captured.out
    assert "Warning" in captured.err
    assert bool(named_import_sync_calls) is dry_run
    manifest = named_import_workspace.read_text(encoding="utf-8")
    environments = tomlkit.parse(manifest).get("environments", {})
    assert ("qa" in environments) is not dry_run


@pytest.mark.parametrize(
    "failure_stage",
    [None, "preflight", "lockfile", "install"],
    ids=["success", "preflight-failure", "lockfile-failure", "install-failure"],
)
def test_execute_named_import_runs_complete_lifecycle_before_prefix_install(
    named_import_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str | None,
    replace_lockfile_install_plan: Callable[..., None],
    replace_publication_writer: Callable[..., None],
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text("dependencies:\n  - python>=3.12\n", encoding="utf-8")
    before = named_import_workspace.read_bytes()
    previous_lock = "previous lock\n"
    lockfile = tmp_path / "conda.lock"
    lockfile.write_text(previous_lock, encoding="utf-8")
    rendered_lock = "version: 1\nenvironments: {}\npackages: []\n"
    events: list[str] = []
    prepare_kwargs: list[dict[str, object]] = []

    def render_lock(_ctx, resolved, *, config, **_kwargs: object) -> str:
        events.append("render")
        assert set(resolved) == {"default", "qa"}
        imported = config.environments["qa"]
        assert imported.no_default_feature is True
        assert set(config.merged_conda_dependencies(imported)) == {"python"}
        return rendered_lock

    def install_plan(phase, _ctx, name, kwargs) -> None:
        assert name == "qa"
        events.append(f"{phase}-qa")
        validator = kwargs["validate_workspace"]
        assert callable(validator)
        validator()
        if phase == "prepare":
            prepare_kwargs.append(kwargs)
            if failure_stage == "preflight":
                raise RuntimeError("preflight failed")
        elif failure_stage == "install":
            raise RuntimeError("install failed")

    def publish(path: Path, content: str, write: Callable[[str], None]) -> None:
        event = "lockfile" if path.name == "conda.lock" else "manifest"
        events.append(event)
        if failure_stage == "lockfile" and event == "lockfile":
            raise RuntimeError("lockfile failed")
        write(content)

    monkeypatch.setattr(
        "conda_workspaces.cli.workspace.sync.render_lockfile",
        render_lock,
    )
    replace_lockfile_install_plan(
        "conda_workspaces.cli.workspace.sync",
        install_plan,
    )
    replace_publication_writer(publish)

    output = StringIO()
    with conda_context._override("_subdir", "linux-64"):
        if failure_stage == "preflight":
            with pytest.raises(RuntimeError, match="preflight failed"):
                execute_import(
                    make_args(_DEFAULTS, file=source, environment="qa"),
                    console=Console(file=output),
                )
        elif failure_stage is not None:
            with pytest.raises(
                CondaWorkspacesError,
                match="did not finish",
            ) as exc_info:
                execute_import(
                    make_args(_DEFAULTS, file=source, environment="qa"),
                    console=Console(file=output),
                )
            assert any("install -e qa" in hint for hint in exc_info.value.hints)
        else:
            assert (
                execute_import(
                    make_args(_DEFAULTS, file=source, environment="qa"),
                    console=Console(file=output),
                )
                == 0
            )

    assert prepare_kwargs[0]["require_absent"] is True
    assert prepare_kwargs[0]["replace_existing"] is False
    if failure_stage == "preflight":
        assert events == ["render", "prepare-qa"]
        assert named_import_workspace.read_bytes() == before
        assert lockfile.read_text(encoding="utf-8") == previous_lock
    elif failure_stage == "lockfile":
        assert events == ["render", "prepare-qa", "manifest", "lockfile"]
        assert named_import_workspace.read_bytes() != before
        assert lockfile.read_text(encoding="utf-8") == previous_lock
    else:
        assert named_import_workspace.read_bytes() != before
        assert lockfile.read_text(encoding="utf-8") == rendered_lock
        assert events == [
            "render",
            "prepare-qa",
            "manifest",
            "lockfile",
            "execute-qa",
        ]
    assert ("Imported" in output.getvalue()) is (failure_stage is None)
