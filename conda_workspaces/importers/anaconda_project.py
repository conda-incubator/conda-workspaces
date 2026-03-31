"""Import ``anaconda-project.yml`` into a ``conda.toml`` workspace manifest."""

from __future__ import annotations

from typing import TYPE_CHECKING

import tomlkit

from .base import ManifestImporter

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any, ClassVar


class AnacondaProjectImporter(ManifestImporter):
    """Convert an ``anaconda-project.yml`` file to a ``conda.toml`` document."""

    filenames: ClassVar[tuple[str, ...]] = (
        "anaconda-project.yml",
        "anaconda-project.yaml",
    )

    def convert(self, path: Path) -> tomlkit.TOMLDocument:
        ap = self.load_yaml(path)

        doc = tomlkit.document()

        ws = tomlkit.table()
        ws.add("name", ap.get("name", path.parent.name))
        ws.add("channels", ap.get("channels", ["conda-forge"]))
        ws.add("platforms", ap.get("platforms", ["linux-64", "osx-arm64"]))
        doc.add("workspace", ws)

        base_packages = ap.get("packages", [])
        base_deps = self.parse_conda_deps(base_packages)
        base_pypi = self.parse_pip_deps(base_packages)

        env_specs = ap.get("env_specs", {})
        default_spec = env_specs.get("default")
        if default_spec and default_spec.get("packages"):
            for k, v in self.parse_conda_deps(default_spec["packages"]).items():
                if k not in base_deps:
                    base_deps[k] = v
            for k, v in self.parse_pip_deps(default_spec["packages"]).items():
                if k not in base_pypi:
                    base_pypi[k] = v

        if base_deps:
            doc.add("dependencies", base_deps)
        if base_pypi:
            doc.add("pypi-dependencies", base_pypi)

        features: dict[str, dict[str, str]] = {}
        environments: dict[str, Any] = {"default": []}

        for env_name, spec in env_specs.items():
            if env_name == "default":
                continue
            if spec and spec.get("packages"):
                env_deps = self.parse_conda_deps(spec["packages"])
                feature_deps = {k: v for k, v in env_deps.items() if k not in base_deps}
                if feature_deps:
                    features[env_name] = feature_deps
            env_features = [env_name] if env_name in features else []
            environments[env_name] = {"features": env_features} if env_features else []

        self.add_features(doc, features, environments)

        tasks: dict[str, Any] = {}
        for cmd_name, spec in ap.get("commands", {}).items():
            task = self.command_to_task(spec)
            if task:
                tasks[cmd_name] = task

        for dl_name, dl_spec in ap.get("downloads", {}).items():
            url = dl_spec if isinstance(dl_spec, str) else dl_spec.get("url", "")
            if url:
                filename = dl_name.lower().replace("_", "-")
                tasks[f"download-{filename}"] = {
                    "cmd": f"curl -fsSL -o {filename} {url}",
                    "description": f"Download {dl_name}",
                }

        if tasks:
            doc.add("tasks", tasks)

        return doc

    @staticmethod
    def command_to_task(spec: dict[str, Any] | str) -> dict[str, Any] | str:
        """Convert an anaconda-project command spec to a conda task value."""
        if isinstance(spec, str):
            return spec

        cmd: str | None = None
        if spec.get("notebook"):
            cmd = f"jupyter notebook {spec['notebook']}"
        elif spec.get("bokeh_app"):
            cmd = f"bokeh serve {spec['bokeh_app']}"
        elif spec.get("unix"):
            cmd = spec["unix"]
        elif spec.get("windows"):
            cmd = spec["windows"]

        if cmd is None:
            return ""

        task: dict[str, Any] = {"cmd": cmd.strip()}

        if spec.get("description"):
            task["description"] = spec["description"]

        if spec.get("env_spec") and spec["env_spec"] != "default":
            task["default-environment"] = spec["env_spec"]

        env_vars: dict[str, str] = {}
        if spec.get("variables"):
            for k, v in spec["variables"].items():
                if isinstance(v, dict):
                    env_vars[str(k)] = str(v.get("default", ""))
                else:
                    env_vars[str(k)] = str(v) if v is not None else ""
        if env_vars:
            task["env"] = env_vars

        if len(task) == 1 and "cmd" in task:
            return task["cmd"]
        return task


convert = AnacondaProjectImporter().convert
