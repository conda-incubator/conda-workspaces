"""Abstract base class for manifest importers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import tomlkit
from conda.common.serialize.yaml import load as yaml_load
from conda.models.match_spec import MatchSpec
from packaging.requirements import Requirement

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any, ClassVar


class ManifestImporter(ABC):
    """Base class for converting foreign manifests to ``conda.toml``.

    Each subclass handles one manifest format. Subclasses declare the
    filenames they handle via *filenames* and implement ``convert``
    to produce a ``tomlkit.TOMLDocument``.
    """

    filenames: ClassVar[tuple[str, ...]] = ()

    def can_handle(self, path: Path) -> bool:
        """Return True if this importer handles *path*."""
        return path.name in self.filenames

    @abstractmethod
    def convert(self, path: Path) -> tomlkit.TOMLDocument:
        """Read *path* and return a ``conda.toml``-shaped TOML document."""

    def load_yaml(self, path: Path) -> dict[str, Any]:
        """Load a YAML file using conda's serialiser."""
        with path.open() as f:
            return yaml_load(f)

    def parse_conda_deps(self, packages: list[Any]) -> dict[str, str]:
        """Extract conda dependencies from a package list via ``MatchSpec``."""
        deps: dict[str, str] = {}
        for pkg in packages:
            if isinstance(pkg, str):
                ms = MatchSpec(pkg)
                deps[ms.name] = str(ms.version) if ms.version else "*"
        return deps

    def parse_pip_deps(self, packages: list[Any]) -> dict[str, str]:
        """Extract PyPI dependencies from ``pip:`` entries in a package list."""
        pypi: dict[str, str] = {}
        for pkg in packages:
            if isinstance(pkg, dict) and "pip" in pkg:
                for pip_pkg in pkg["pip"]:
                    req = Requirement(pip_pkg)
                    pypi[req.name] = str(req.specifier) or "*"
        return pypi

    def add_features(
        self,
        doc: tomlkit.TOMLDocument,
        features: dict[str, dict[str, str]],
        environments: dict[str, Any],
    ) -> None:
        """Write ``[feature.*]`` and ``[environments]`` tables into *doc*."""
        for feat_name, feat_deps in features.items():
            if "feature" not in doc:
                doc.add("feature", tomlkit.table(is_super_table=True))
            feat_tbl = tomlkit.table(is_super_table=True)
            feat_tbl.add("dependencies", feat_deps)
            doc["feature"].add(feat_name, feat_tbl)

        if environments:
            doc.add("environments", environments)
