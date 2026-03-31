"""Import ``conda-project.yml`` into a ``conda.toml`` workspace manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit
from conda.base.context import context as conda_context

from .base import ManifestImporter

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any, ClassVar


class CondaProjectImporter(ManifestImporter):
    """Convert a ``conda-project.yml`` file to a ``conda.toml`` document."""

    filenames: ClassVar[tuple[str, ...]] = (
        "conda-project.yml",
        "conda-project.yaml",
    )
    label: ClassVar[str] = "conda-project"

    def convert(self, path: Path) -> tomlkit.TOMLDocument:
        cp = self.load_yaml(path)
        project_dir = path.parent

        doc = tomlkit.document()
        ws = tomlkit.table()
        ws.add("name", cp.get("name", project_dir.name))

        env_specs = cp.get("environments", {})
        base_deps: dict[str, str] = {}
        base_pypi: dict[str, str] = {}
        channels: list[str] = ["conda-forge"]
        platforms: list[str] = [conda_context.subdir]

        for env_file in env_specs.get("default", []):
            env_path = project_dir / env_file
            if env_path.exists():
                env_data = self.load_yaml(env_path)
                raw_deps = env_data.get("dependencies", [])
                base_deps.update(self.parse_conda_deps(raw_deps))
                base_pypi.update(self.parse_pip_deps(raw_deps))
                if env_data.get("channels"):
                    channels = env_data["channels"]
                if env_data.get("platforms"):
                    platforms = env_data["platforms"]

        ws.add("channels", channels)
        ws.add("platforms", platforms)
        doc.add("workspace", ws)

        if base_deps:
            doc.add("dependencies", base_deps)
        if base_pypi:
            doc.add("pypi-dependencies", base_pypi)

        features: dict[str, dict[str, str]] = {}
        environments: dict[str, Any] = {"default": []}

        for env_name, env_files in env_specs.items():
            if env_name == "default":
                continue
            env_deps: dict[str, str] = {}
            for env_file in env_files:
                env_path = project_dir / env_file
                if env_path.exists():
                    env_data = self.load_yaml(env_path)
                    env_deps.update(
                        self.parse_conda_deps(env_data.get("dependencies", []))
                    )
            feature_deps = {k: v for k, v in env_deps.items() if k not in base_deps}
            if feature_deps:
                features[env_name] = feature_deps
            env_features = [env_name] if env_name in features else []
            environments[env_name] = {"features": env_features} if env_features else []

        self.add_features(doc, features, environments)

        tasks: dict[str, Any] = {}
        for cmd_name, spec in cp.get("commands", {}).items():
            if isinstance(spec, str):
                tasks[cmd_name] = spec
            elif isinstance(spec, dict):
                cmd = spec.get("cmd", "")
                if not cmd:
                    continue
                task: dict[str, Any] = {"cmd": cmd}
                if spec.get("environment") and spec["environment"] != "default":
                    task["default-environment"] = spec["environment"]
                env_vars: dict[str, str] = {}
                if spec.get("variables"):
                    for k, v in spec["variables"].items():
                        env_vars[str(k)] = str(v) if v is not None else ""
                if env_vars:
                    task["env"] = env_vars
                tasks[cmd_name] = cmd if len(task) == 1 else task

        if tasks:
            doc.add("tasks", tasks)

        return doc


convert = CondaProjectImporter().convert
