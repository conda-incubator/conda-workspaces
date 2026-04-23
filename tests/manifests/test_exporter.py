"""Tests for ``ManifestParser.export`` — the manifest-format exporter plugins.

Covers :meth:`ManifestParser.export` (the default
``conda.toml`` / ``pixi.toml`` writer) and
:meth:`PyprojectTomlParser.export` (the ``[tool.conda]``-nested
override).  Verifies structural invariants (single-platform
top-level-only, multi-platform top-level intersection plus
``[target.<platform>.*]`` deltas, pypi dependencies, pyproject
nesting) and that the output round-trips through the same
parser's :meth:`parse` back to the declared specs and channels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import tomlkit
from conda.models.environment import Environment
from conda.models.environment import EnvironmentConfig as CondaEnvConfig
from conda.models.match_spec import MatchSpec

from conda_workspaces.manifests.base import ManifestParser
from conda_workspaces.manifests.pixi_toml import PixiTomlParser
from conda_workspaces.manifests.pyproject_toml import PyprojectTomlParser
from conda_workspaces.manifests.toml import CondaTomlParser
from conda_workspaces.resolver import resolve_environment

if TYPE_CHECKING:
    from pathlib import Path


def make_env(
    platform: str,
    *,
    name: str = "default",
    conda_specs: tuple[str, ...] = ("python=3.12",),
    pypi_specs: tuple[str, ...] = (),
    channels: tuple[str, ...] = ("conda-forge",),
) -> Environment:
    """Build a minimal ``Environment`` for export-side tests.

    The three manifest exporters only look at ``requested_packages``,
    ``external_packages["pip"]``, ``config.channels``, ``name``, and
    ``platform`` — ``explicit_packages`` is intentionally left empty
    so each test exercises the declared-specs round-trip, not a
    resolver's output.
    """
    external = {"pip": list(pypi_specs)} if pypi_specs else {}
    return Environment(
        name=name,
        platform=platform,
        config=CondaEnvConfig(channels=channels),
        requested_packages=[MatchSpec(s) for s in conda_specs],
        external_packages=external,
    )


def write_and_parse(parser: ManifestParser, content: str, tmp_path: Path):
    """Write *content* to the parser's canonical filename and parse it back."""
    path = tmp_path / parser.manifest_filename
    path.write_text(content, encoding="utf-8")
    return path, parser.parse(path)


@pytest.fixture
def parsers() -> dict[str, ManifestParser]:
    return {
        "conda-toml": CondaTomlParser(),
        "pixi-toml": PixiTomlParser(),
        "pyproject-toml": PyprojectTomlParser(),
    }


@pytest.mark.parametrize(
    "format_name",
    ["conda-toml", "pixi-toml", "pyproject-toml"],
)
def test_export_single_platform_round_trips(
    format_name: str,
    parsers: dict[str, ManifestParser],
    tmp_path: Path,
) -> None:
    """Every format round-trips declared conda + pypi specs for one platform."""
    parser = parsers[format_name]
    env = make_env(
        "linux-64",
        conda_specs=("python=3.12", "numpy >=1.20"),
        pypi_specs=("requests==2.31", "pandas"),
    )

    output = parser.export([env])
    _, config = write_and_parse(parser, output, tmp_path)

    assert config.platforms == ["linux-64"]
    assert [c.canonical_name for c in config.channels] == ["conda-forge"]

    resolved = resolve_environment(config, "default", "linux-64")
    assert set(resolved.conda_dependencies) == {"python", "numpy"}
    assert resolved.conda_dependencies["python"] == MatchSpec("python=3.12")
    assert resolved.conda_dependencies["numpy"] == MatchSpec("numpy >=1.20")
    assert set(resolved.pypi_dependencies) == {"requests", "pandas"}


@pytest.mark.parametrize(
    "format_name",
    ["conda-toml", "pixi-toml", "pyproject-toml"],
)
def test_export_multi_platform_emits_target_deltas(
    format_name: str,
    parsers: dict[str, ManifestParser],
    tmp_path: Path,
) -> None:
    """Shared specs at the top level; divergent specs under ``[target.*]``."""
    parser = parsers[format_name]
    envs = [
        make_env(
            "linux-64",
            conda_specs=("python=3.12", "numpy >=1.20"),
            pypi_specs=("requests==2.31",),
        ),
        make_env(
            "osx-arm64",
            conda_specs=("python=3.12", "numpy >=1.20", "appnope"),
            pypi_specs=("requests==2.31",),
        ),
    ]

    output = parser.export(envs)
    path, config = write_and_parse(parser, output, tmp_path)

    assert config.platforms == ["linux-64", "osx-arm64"]

    linux = resolve_environment(config, "default", "linux-64")
    osx = resolve_environment(config, "default", "osx-arm64")
    assert set(linux.conda_dependencies) == {"python", "numpy"}
    assert set(osx.conda_dependencies) == {"python", "numpy", "appnope"}
    # Shared specs are written once at the top level — the resolver
    # returns the same object identity-checkable spec on both
    # platforms.  Target-override sections only appear for deltas.
    assert set(linux.pypi_dependencies) == {"requests"}
    assert set(osx.pypi_dependencies) == {"requests"}


def test_export_pyproject_nests_under_tool_conda(
    parsers: dict[str, ManifestParser],
) -> None:
    """PyprojectTomlParser wraps every table under ``[tool.conda]``."""
    parser = parsers["pyproject-toml"]
    env = make_env("linux-64", conda_specs=("python=3.12",))

    output = parser.export([env])
    data = tomlkit.loads(output).unwrap()

    assert set(data) == {"tool"}
    assert set(data["tool"]) == {"conda"}
    conda = data["tool"]["conda"]
    assert conda["workspace"]["platforms"] == ["linux-64"]
    assert conda["dependencies"] == {"python": "3.12.*"}


@pytest.mark.parametrize(
    "format_name",
    ["conda-toml", "pixi-toml"],
)
def test_export_conda_and_pixi_write_top_level_tables(
    format_name: str,
    parsers: dict[str, ManifestParser],
) -> None:
    """conda.toml / pixi.toml keep ``[workspace]`` + ``[dependencies]`` at root."""
    parser = parsers[format_name]
    env = make_env("linux-64", conda_specs=("python=3.12",))

    data = tomlkit.loads(parser.export([env])).unwrap()

    assert data["workspace"]["platforms"] == ["linux-64"]
    assert data["dependencies"] == {"python": "3.12.*"}
    assert "tool" not in data


def test_export_omits_empty_pypi_and_target_tables(
    parsers: dict[str, ManifestParser],
) -> None:
    """No pypi deps + single-platform → no ``[pypi-dependencies]`` / ``[target]``."""
    parser = parsers["conda-toml"]
    env = make_env("linux-64", conda_specs=("python=3.12",))

    data = tomlkit.loads(parser.export([env])).unwrap()

    assert "pypi-dependencies" not in data
    assert "target" not in data


def test_export_empty_envs_raises(parsers: dict[str, ManifestParser]) -> None:
    """``manifest_data([])`` is nonsensical — fail fast."""
    with pytest.raises(ValueError, match="At least one Environment"):
        parsers["conda-toml"].export([])


@pytest.mark.parametrize(
    ("parser_cls", "filename", "exporter_format"),
    [
        (CondaTomlParser, "conda.toml", "conda-toml"),
        (PixiTomlParser, "pixi.toml", "pixi-toml"),
        (PyprojectTomlParser, "pyproject.toml", "pyproject-toml"),
    ],
)
def test_parser_exporter_class_attrs(
    parser_cls: type[ManifestParser],
    filename: str,
    exporter_format: str,
) -> None:
    """Each parser advertises its exporter plugin identity + filename."""
    assert parser_cls.exporter_format == exporter_format
    assert parser_cls.filenames[0] == filename


def test_export_handles_wildcard_pypi_spec(
    parsers: dict[str, ManifestParser],
) -> None:
    """Wildcard PyPI specs (``name = "*"``) round-trip through invalid PEP 508.

    :meth:`~conda_workspaces.models.PyPIDependency.__str__` returns
    ``"requests*"`` for ``requests = "*"``, which is not a valid PEP
    508 requirement and makes
    :class:`packaging.requirements.Requirement` raise.  The exporter
    falls back to splitting name from tail so the output matches
    ``environment-yaml``'s resilience (emit ``requests*`` /
    ``requests = "*"`` rather than crashing).
    """
    parser = parsers["conda-toml"]
    env = Environment(
        name="default",
        platform="linux-64",
        config=CondaEnvConfig(channels=("conda-forge",)),
        requested_packages=[MatchSpec("python=3.12")],
        external_packages={"pip": ["requests*"]},
    )

    data = tomlkit.loads(parser.export([env])).unwrap()
    assert data["pypi-dependencies"] == {"requests": "*"}


def test_intersect_rows_filters_mismatches() -> None:
    """Rows only survive the intersection when every platform has the same value."""
    per_platform = {
        "linux-64": {"a": "1", "b": "1", "c": "1"},
        "osx-arm64": {"a": "1", "b": "2"},
    }
    common = ManifestParser._intersect_rows(per_platform)
    assert common == {"a": "1"}
