"""Tests for conda_workspaces.cli.workspace.init."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import tomlkit

from conda_workspaces.cli.workspace.init import execute_init
from conda_workspaces.exceptions import ManifestExistsError

from ..conftest import make_args

if TYPE_CHECKING:
    from pathlib import Path

_DEFAULTS = {
    "manifest_format": "conda",
    "name": None,
    "channels": None,
    "platforms": ["linux-64", "osx-arm64", "win-64"],
    "file": None,
}


@pytest.mark.parametrize(
    "fmt, filename",
    [
        ("pixi", "pixi.toml"),
        ("conda", "conda.toml"),
        ("pyproject", "pyproject.toml"),
    ],
    ids=["pixi-format", "conda-format", "pyproject-format"],
)
def test_init_creates_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fmt: str, filename: str
) -> None:
    monkeypatch.chdir(tmp_path)
    args = make_args(_DEFAULTS, manifest_format=fmt, name="my-project")
    result = execute_init(args)
    assert result == 0
    assert (tmp_path / filename).exists()


@pytest.mark.parametrize(
    "fmt, filename",
    [
        ("pixi", "pixi.toml"),
        ("conda", "conda.toml"),
    ],
    ids=["pixi-exists", "conda-exists"],
)
def test_init_refuses_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fmt: str,
    filename: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / filename).write_text("existing content", encoding="utf-8")
    args = make_args(_DEFAULTS, manifest_format=fmt, name="proj")
    with pytest.raises(ManifestExistsError, match="already exists"):
        execute_init(args)


def test_init_pixi_toml_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = make_args(
        _DEFAULTS,
        manifest_format="pixi",
        name="structured",
        channels=["conda-forge", "bioconda"],
        platforms=["linux-64"],
    )
    execute_init(args)
    doc = tomlkit.loads((tmp_path / "pixi.toml").read_text(encoding="utf-8"))
    assert doc["workspace"]["name"] == "structured"
    assert doc["workspace"]["channels"] == ["conda-forge", "bioconda"]
    assert doc["workspace"]["platforms"] == ["linux-64"]
    assert "dependencies" in doc


def test_init_conda_toml_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = make_args(_DEFAULTS, manifest_format="conda", name="my-conda")
    execute_init(args)
    doc = tomlkit.loads((tmp_path / "conda.toml").read_text(encoding="utf-8"))
    assert doc["workspace"]["name"] == "my-conda"


def test_init_pyproject_creates_tool_conda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = make_args(_DEFAULTS, manifest_format="pyproject", name="pp")
    execute_init(args)
    doc = tomlkit.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    assert "conda" in doc["tool"]
    assert "pixi" not in doc["tool"]
    ws = doc["tool"]["conda"]["workspace"]
    assert ws["name"] == "pp"
    assert "channels" in ws
    assert "platforms" in ws


def test_init_pyproject_appends_to_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If pyproject.toml already exists, init adds [tool.conda] to it."""
    monkeypatch.chdir(tmp_path)
    existing = '[project]\nname = "existing"\n'
    (tmp_path / "pyproject.toml").write_text(existing, encoding="utf-8")
    args = make_args(_DEFAULTS, manifest_format="pyproject", name="pp")
    execute_init(args)
    doc = tomlkit.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    assert doc["project"]["name"] == "existing"
    assert "conda" in doc["tool"]


@pytest.mark.parametrize(
    "existing_content",
    [
        '[tool.conda.workspace]\nchannels = ["defaults"]\n',
        '[tool.pixi.workspace]\nchannels = ["defaults"]\n',
    ],
    ids=["tool-conda-exists", "tool-pixi-exists"],
)
def test_init_pyproject_refuses_existing_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_content: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(existing_content, encoding="utf-8")
    args = make_args(_DEFAULTS, manifest_format="pyproject", name="pp")
    with pytest.raises(ManifestExistsError, match="already exists"):
        execute_init(args)


def test_init_default_name_from_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = make_args(_DEFAULTS, manifest_format="pixi", name=None)
    execute_init(args)
    doc = tomlkit.loads((tmp_path / "pixi.toml").read_text(encoding="utf-8"))
    assert doc["workspace"]["name"] == tmp_path.name


def test_init_default_channels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = make_args(_DEFAULTS, manifest_format="pixi", name="ch-test", channels=None)
    execute_init(args)
    doc = tomlkit.loads((tmp_path / "pixi.toml").read_text(encoding="utf-8"))
    assert doc["workspace"]["channels"] == ["conda-forge"]


def test_init_auto_detects_single_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no --platform arg, init auto-detects exactly one platform."""
    monkeypatch.chdir(tmp_path)
    args = make_args(
        _DEFAULTS,
        manifest_format="conda",
        name="auto-plat",
        platforms=None,
    )
    execute_init(args)
    doc = tomlkit.loads((tmp_path / "conda.toml").read_text(encoding="utf-8"))
    platforms = doc["workspace"]["platforms"]
    assert len(platforms) == 1


@pytest.mark.parametrize(
    "fmt, filename, ws_path",
    [
        ("pixi", "pixi.toml", ("workspace",)),
        ("conda", "conda.toml", ("workspace",)),
        ("pyproject", "pyproject.toml", ("tool", "conda", "workspace")),
    ],
    ids=["pixi", "conda", "pyproject"],
)
def test_init_no_version_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fmt: str,
    filename: str,
    ws_path: tuple[str, ...],
) -> None:
    """init does not include a version field in the generated manifest."""
    monkeypatch.chdir(tmp_path)
    args = make_args(_DEFAULTS, manifest_format=fmt, name="novr")
    execute_init(args)
    doc = tomlkit.loads((tmp_path / filename).read_text(encoding="utf-8"))
    ws = doc
    for key in ws_path:
        ws = ws[key]
    assert "version" not in ws
