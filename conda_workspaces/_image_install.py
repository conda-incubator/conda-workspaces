"""Build-stage entry point copied into the generated container context.

This script uses the conda-workspaces installation APIs in the build image.
It never runs when starting the finished image.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context as conda_context
from conda.models.match_spec import MatchSpec

# Absolute imports are required when Docker invokes this as a standalone script.
from conda_workspaces.context import WorkspaceContext
from conda_workspaces.exceptions import CondaWorkspacesError
from conda_workspaces.lockfile import LockfileInstallPlan
from conda_workspaces.manifests import detect_and_parse
from conda_workspaces.resolver import resolve_environment

if TYPE_CHECKING:
    from typing import Any

    from conda_workspaces.models import WorkspaceConfig


class ImageInstaller(WorkspaceContext):
    """Install one lock platform, including named platform variants."""

    def __init__(
        self,
        config: WorkspaceConfig,
        environment: str,
        platform: str,
        prefix: Path | None = None,
    ) -> None:
        super().__init__(config)
        self.environment_name = environment
        self.lock_platform = platform
        self.prefix = prefix if prefix is not None else self.env_prefix(environment)
        self.resolved = resolve_environment(config, environment, platform)
        self.resolved.activation_scripts = [
            str(Path(config.root) / script)
            for script in self.resolved.activation_scripts
        ]

    @property
    def platform(self) -> str:
        """Select the requested lock entry without changing native virtual packages."""
        return self.lock_platform

    def install_packages(self) -> None:
        """Install exact conda records and activation metadata through conda."""
        plan = LockfileInstallPlan.prepare(
            self, self.environment_name, prefix=self.prefix
        )
        # Local builds run separately with networking disabled. conda-pypi's
        # ordinary path installer can solve missing backend requirements.
        plan.update_path_dependencies = False
        plan.resolved = self.resolved
        actual_virtuals = {
            record.name: record
            for record in conda_context.plugin_manager.get_virtual_package_records()
        }
        requirements = [
            MatchSpec(dependency)
            for record in plan.records
            for dependency in record.depends
            if dependency.startswith("__")
        ]
        requirements.extend(
            MatchSpec(f"{name if name.startswith('__') else '__' + name} >={version}")
            for name, version in self.resolved.system_requirements.items()
        )
        for requirement in requirements:
            actual = actual_virtuals.get(requirement.name)
            if actual is None or not requirement.match(actual):
                raise CondaWorkspacesError(
                    f"The Linux build environment does not satisfy {requirement}. "
                    "Choose a compatible --base-image and builder."
                )
        plan.execute()

    def install_local_packages(self) -> None:
        """Build local packages using only the already installed locked requirements."""
        # These dependencies are only needed inside the optional image builder.
        from build import ProjectBuilder
        from conda_pypi import dependencies
        from conda_pypi.build import build_conda
        from conda_pypi.installer import install_ephemeral_conda

        local_requirements = set()
        for dependency in self.resolved.pypi_dependencies.values():
            if dependency.path is None:
                continue
            distribution = "wheel"
            local_requirements.add(
                dependency.name
                + (f"[{','.join(dependency.extras)}]" if dependency.extras else "")
            )
            source = Path(self.config.root) / dependency.path
            builder = ProjectBuilder(
                source, python_executable=str(self.prefix / "bin/python")
            )
            self.require_dependencies(builder.build_system_requires, dependencies)
            self.require_dependencies(
                builder.get_requires_for_build(distribution), dependencies
            )
            with tempfile.TemporaryDirectory(
                prefix="workspace-local-package-"
            ) as directory:
                temporary = Path(directory)
                wheel = builder.build(distribution, temporary)
                package = build_conda(
                    Path(wheel),
                    temporary / "build",
                    temporary,
                    sys.executable,
                    project_path=source,
                    is_editable=False,
                )
                install_ephemeral_conda(self.prefix, package)
        if local_requirements:
            self.require_dependencies(local_requirements, dependencies, phase="runtime")

    def require_dependencies(
        self, requirements: set[str], dependencies: Any, *, phase: str = "build"
    ) -> None:
        """Require backend dependencies to be installed from the workspace lock."""
        try:
            missing = dependencies.check_dependencies(requirements, prefix=self.prefix)
        except dependencies.MissingDependencyError as exc:
            missing = exc.dependencies
        if missing:
            raise CondaWorkspacesError(
                f"Local package {phase} requirements are absent "
                "from the locked environment: "
                + ", ".join(sorted(missing))
                + ". Add their conda packages to the workspace "
                "and regenerate conda.lock. "
                "The PyPI build package is named python-build on conda-forge."
            )

    def write_entrypoint(self, destination: Path) -> None:
        """Freeze activation while preserving runtime PATH and application argv."""
        activation = subprocess.run(
            [
                sys.executable,
                "-m",
                "conda",
                "shell.posix+json",
                "activate",
                str(self.prefix),
            ],
            check=True,
            capture_output=True,
            text=True,
            env={"PATH": "/__workspace_runtime_path__", "CONDA_CHANGEPS1": "false"},
        )
        destination.write_text(
            self.activation_script(json.loads(activation.stdout)), encoding="utf-8"
        )

    @staticmethod
    def activation_script(activation: dict[str, Any]) -> str:
        """Render conda's activation data as a quoted bash entrypoint."""
        lines = ["#!/bin/bash", "set -e"]
        bootstrap_variables = {
            "CONDA_EXE",
            "_CONDA_EXE",
            "CONDA_PYTHON_EXE",
            "_CONDA_ROOT",
            "_CE_M",
            "_CE_CONDA",
            "CONDA_PROMPT_MODIFIER",
        }
        unset = set(activation["vars"]["unset"]) | bootstrap_variables
        exports = activation["vars"]["export"]
        for name in [*unset, *exports]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise CondaWorkspacesError(
                    "Invalid activation environment variable name."
                )
        if unset:
            lines.append("unset " + " ".join(sorted(unset)))
        paths = [
            value
            for value in activation["path"]["PATH"]
            if not value.startswith("/opt/conda/")
        ]
        path = ":".join(paths)
        pieces = path.split("/__workspace_runtime_path__")
        runtime_path = (
            '"${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"'
        )
        lines.append(
            "export PATH=" + runtime_path.join(shlex.quote(piece) for piece in pieces)
        )
        for name, value in exports.items():
            if name not in bootstrap_variables:
                lines.append(f"export {name}={shlex.quote(str(value))}")
        for script in activation["scripts"]["activate"]:
            lines.append(". " + shlex.quote(script))
        lines.append('exec "$@"')
        return "\n".join(lines) + "\n"


def main() -> None:
    """Run the package or local-build phase selected by the generated recipe."""
    mode, manifest, environment, platform, prefix = sys.argv[1:]
    _, config = detect_and_parse(Path(manifest))
    installer = ImageInstaller(config, environment, platform, Path(prefix))
    if mode == "packages":
        installer.install_packages()
        installer.write_entrypoint(Path("/build/entrypoint.sh"))
    elif mode == "local":
        installer.install_local_packages()
        installer.write_entrypoint(Path("/build/entrypoint.sh"))
    else:
        raise ValueError(f"Unknown image build phase: {mode}")


if __name__ == "__main__":
    main()
