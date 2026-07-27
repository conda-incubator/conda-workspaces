"""Abstract base class for manifest importers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from io import StringIO
from typing import TYPE_CHECKING, cast

import tomlkit
from conda.common.serialize.yaml import load as yaml_load
from conda.models.match_spec import MatchSpec
from packaging.requirements import Requirement

from ..exceptions import ManifestImportError
from ..manifests.base import (
    MAX_MANIFEST_BYTES,
    MAX_MANIFEST_COLLECTION_ITEMS,
    MAX_MANIFEST_DEPTH,
    MAX_MANIFEST_ITEMS,
    match_spec_to_toml,
)
from ..models import (
    PyPIDependency,
    has_url_credentials,
    redact_channel_name,
    redact_url_text,
)
from ..parsing import validate_document_limits
from ..paths import read_regular_file_bytes

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any, ClassVar

    from tomlkit.items import Table


class ManifestImporter(ABC):
    """Base class for converting foreign manifests to ``conda.toml``.

    Each subclass handles one manifest format. Subclasses declare the
    filenames they handle via *filenames* and implement ``convert``
    to produce a ``tomlkit.TOMLDocument``.
    """

    filenames: ClassVar[tuple[str, ...]] = ()
    label: ClassVar[str] = ""

    def can_handle(self, path: Path) -> bool:
        """Return True if this importer handles *path*."""
        return path.name in self.filenames

    @abstractmethod
    def convert(self, path: Path) -> tomlkit.TOMLDocument:
        """Read *path* and return a ``conda.toml``-shaped TOML document."""

    def load_yaml(self, path: Path) -> dict[str, Any]:
        """Load one bounded regular YAML manifest using conda's serialiser."""
        try:
            content = read_regular_file_bytes(
                path,
                maximum_bytes=MAX_MANIFEST_BYTES,
                label="Manifest YAML",
            ).decode("utf-8")
            data = yaml_load(StringIO(content))
            if not isinstance(data, Mapping):
                raise ValueError("Manifest YAML must contain a mapping")
            validate_document_limits(
                data,
                label="Manifest YAML",
                maximum_depth=MAX_MANIFEST_DEPTH,
                maximum_collection_items=MAX_MANIFEST_COLLECTION_ITEMS,
                maximum_items=MAX_MANIFEST_ITEMS,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise ManifestImportError(path, redact_url_text(str(exc))) from exc
        except Exception as exc:
            raise ManifestImportError(path, "invalid YAML") from exc
        return dict(data)

    def parse_conda_deps(self, packages: object) -> dict[str, object]:
        """Extract conda dependencies from a package list via ``MatchSpec``."""
        if not isinstance(packages, list):
            raise ValueError("Manifest dependencies must be a list.")
        deps: dict[str, object] = {}
        for pkg in packages:
            if isinstance(pkg, dict) and set(pkg) == {"pip"}:
                continue
            if not isinstance(pkg, str):
                raise ValueError("Conda dependency entries must be strings.")
            if has_url_credentials(pkg):
                raise ValueError("Cannot import direct conda package sources safely.")
            try:
                ms = MatchSpec(pkg)
            except Exception as exc:
                raise ValueError("Cannot import an invalid conda dependency.") from exc
            name = ms.get_exact_value("name")
            if not name:
                raise ValueError(
                    "Cannot import a conda dependency without an exact name."
                )
            deps[name] = match_spec_to_toml(ms)
        return deps

    def parse_pip_deps(self, packages: object) -> dict[str, object]:
        """Extract PyPI dependencies from ``pip:`` entries in a package list."""
        if not isinstance(packages, list):
            raise ValueError("Manifest dependencies must be a list.")
        pypi: dict[str, object] = {}
        for pkg in packages:
            if isinstance(pkg, str):
                continue
            if not isinstance(pkg, dict):
                raise ValueError("Unsupported dependency mapping in manifest.")
            package_group = cast("dict[str, object]", pkg)
            if set(package_group) != {"pip"}:
                raise ValueError("Unsupported dependency mapping in manifest.")
            pip_packages = package_group["pip"]
            if not isinstance(pip_packages, list):
                raise ValueError("Manifest pip dependencies must be a list.")
            for pip_pkg in pip_packages:
                if not isinstance(pip_pkg, str):
                    raise ValueError("PyPI dependency entries must be strings.")
                if has_url_credentials(pip_pkg):
                    raise ValueError(
                        "Cannot import direct PyPI package sources safely."
                    )
                try:
                    req = Requirement(pip_pkg)
                except Exception as exc:
                    raise ValueError(
                        "Cannot import an invalid PyPI dependency."
                    ) from exc
                if req.url is not None:
                    raise ValueError(
                        "Cannot import direct PyPI package sources safely."
                    )
                if req.marker is not None:
                    raise ValueError(
                        f"Cannot import PyPI dependency '{req.name}' because"
                        " workspace manifests cannot represent environment markers."
                    )
                dependency = PyPIDependency(
                    name=req.name,
                    spec=str(req.specifier),
                    extras=tuple(sorted(req.extras)),
                )
                pypi[req.name] = dependency.to_manifest_toml()
        return pypi

    @staticmethod
    def redact_channels(channels: object) -> list[str]:
        """Return string channel entries without serialized credentials."""
        if not isinstance(channels, list):
            return []
        return [
            redact_channel_name(channel)
            for channel in channels
            if isinstance(channel, str)
        ]

    def add_features(
        self,
        doc: tomlkit.TOMLDocument,
        features: dict[str, dict[str, object]],
        environments: dict[str, Any],
    ) -> None:
        """Write ``[feature.*]`` and ``[environments]`` tables into *doc*."""
        for feat_name, feat_deps in features.items():
            if "feature" not in doc:
                doc.add("feature", tomlkit.table(is_super_table=True))
            feat_tbl = tomlkit.table(is_super_table=True)
            feat_tbl.add("dependencies", tomlkit.item(feat_deps))
            feature_container = cast("Table", doc["feature"])
            feature_container.add(feat_name, feat_tbl)

        if environments:
            doc.add("environments", tomlkit.item(environments))
