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
from conda.models.channel import Channel
from conda.utils import quote_for_shell
from rich.console import Console

import conda_workspaces.cli.workspace.import_manifest as import_manifest_mod
from conda_workspaces.cli.workspace.import_manifest import execute_import
from conda_workspaces.exceptions import ManifestImportError, WorkspaceParseError
from conda_workspaces.importers import base as importer_base
from conda_workspaces.importers import find_importer
from conda_workspaces.importers.serialize import config_to_toml
from conda_workspaces.manifests import find_parser as find_manifest_parser
from conda_workspaces.models import WorkspaceConfig
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
