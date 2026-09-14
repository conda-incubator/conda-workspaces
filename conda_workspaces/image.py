"""Build selected workspace environments with Docker Buildx."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .archive import (
    collect_archive_files,
    create_archive,
    validate_archive_members_for_create,
)
from .exceptions import (
    CondaWorkspacesError,
    LockfileNotFoundError,
    LockfileStaleError,
)
from .lockfile import (
    MAX_LOCKFILE_BYTES,
    CondaLockLoader,
    check_lockfile_satisfiability,
    load_lockfile_data,
    lockfile_path,
)
from .models import ArchiveConfig, LockfileStatus, has_url_credentials_in_data
from .paths import (
    atomic_binary_writer,
    has_absolute_path_syntax,
    is_path_segment,
    read_regular_file_bytes,
    validate_file_output,
)
from .resolver import resolve_environment

if TYPE_CHECKING:
    from typing import Final

    from .context import WorkspaceContext
    from .models import WorkspaceConfig

DEFAULT_BASE_IMAGE: Final = "debian:bookworm-slim"
BOOTSTRAP_IMAGE: Final = "quay.io/condaforge/miniforge3:26.7.2-0"
BUILD_TOOLS: Final = ("conda-workspaces=0.9.0", "conda-pypi>=0.9.0")
OCI_PLATFORMS: Final = {"linux-64": "linux/amd64", "linux-aarch64": "linux/arm64"}


@dataclass
class WorkspaceImage:
    """A validated image recipe and its selected source files.

    Preparation never invokes Docker or changes the workspace. Builds use an
    isolated archive context and publish an OCI archive only after success.
    """

    config: WorkspaceConfig
    environment: str
    platform: str
    oci_platform: str
    command: tuple[str, ...]
    tags: tuple[str, ...]
    output: Path | None
    load: bool
    push: bool
    base_image: str
    builder: str | None
    files: list[Path]
    input_hashes: dict[str, str]
    workspace: str
    prefix: str

    @classmethod
    def prepare(
        cls,
        config: WorkspaceConfig,
        ctx: WorkspaceContext,
        *,
        environment: str,
        platform: str,
        command: tuple[str, ...],
        tags: tuple[str, ...] = (),
        output: Path | None = None,
        load: bool = False,
        push: bool = False,
        base_image: str = DEFAULT_BASE_IMAGE,
        builder: str | None = None,
    ) -> WorkspaceImage:
        """Validate the lock, source selection, target, and output without building."""
        if not command or any("\0" in arg for arg in command):
            raise CondaWorkspacesError("Provide an application command after --.")
        if sum((output is not None, load, push)) != 1:
            raise CondaWorkspacesError(
                "Choose exactly one of --load, --output, or --push."
            )
        if (load or push) and not tags:
            raise CondaWorkspacesError(
                "--load and --push require at least one -t/--tag."
            )
        for value in (base_image, *tags):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]*", value):
                raise CondaWorkspacesError("Invalid container image reference.")
        if builder is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*", builder
        ):
            raise CondaWorkspacesError("Invalid Buildx builder name.")
        if (
            not config.name
            or not is_path_segment(config.name)
            or any(char in config.name for char in "$'")
        ):
            raise CondaWorkspacesError(
                "Images require a workspace name that is a portable directory name "
                "without dollar signs or apostrophes. "
                "Set the workspace name in the manifest."
            )
        workspace = str(PurePosixPath("/workspaces") / config.name)

        resolved = resolve_environment(config, environment, platform)
        platform = config.resolve_platform_name(
            platform, resolved.platforms or [platform]
        )
        subdir = config.platform_subdir(platform)
        if subdir not in OCI_PLATFORMS:
            raise CondaWorkspacesError(
                "Images currently support linux-64 and linux-aarch64."
            )
        root = ctx.root
        envs_directory = ctx.envs_dir
        prefix = str(
            PurePosixPath(workspace)
            / ctx.env_prefix(environment).relative_to(root).as_posix()
        )
        if any(char in prefix for char in "$'"):
            raise CondaWorkspacesError(
                "Image environment paths cannot contain dollar signs or apostrophes. "
                "Conda activation and Dockerfile parsing interpret these characters."
            )
        manifest = Path(config.manifest_path)
        lock = lockfile_path(ctx)
        if not lock.exists():
            raise LockfileNotFoundError(environment, lock)
        try:
            lock_bytes = read_regular_file_bytes(
                lock, maximum_bytes=MAX_LOCKFILE_BYTES, label="workspace lockfile"
            )
            data = load_lockfile_data(lock_bytes)
            if has_url_credentials_in_data(data):
                raise CondaWorkspacesError(
                    "Image lockfiles must not contain credentials. "
                    "Configure package authentication outside the workspace."
                )
            current = check_lockfile_satisfiability(config, data, platform)
            if current.status != LockfileStatus.UP_TO_DATE:
                raise LockfileStaleError(manifest, lock, reason=current.reason)
            specs = CondaLockLoader(lock, data=data).explicit_package_specs_for(
                platform, environment, package_platform=subdir
            )
            if any(urlsplit(spec).scheme not in {"https", "http"} for spec in specs):
                raise CondaWorkspacesError(
                    "Image lockfiles require HTTP or HTTPS package sources. "
                    "Local package channels are not available inside the builder."
                )
        except (ValueError, OSError) as exc:
            raise CondaWorkspacesError(f"Cannot build from conda.lock: {exc}") from exc

        files = collect_archive_files(
            root, config.archive, extra_files=(manifest, lock)
        )
        files = [path for path in files if not path.is_relative_to(envs_directory)]
        selected = set(files)
        required = {manifest, lock}
        script_names = set()
        for script in resolved.activation_scripts:
            if has_absolute_path_syntax(script) or script.startswith("~"):
                raise CondaWorkspacesError(
                    "Image activation scripts must use workspace-relative paths."
                )
            path = root / script
            if (
                not path.resolve().is_relative_to(root.resolve())
                or path.is_symlink()
                or not path.is_file()
            ):
                raise CondaWorkspacesError(
                    "Image activation scripts must be regular files "
                    "inside the workspace."
                )
            if path.name in script_names:
                raise CondaWorkspacesError(
                    "Image activation scripts must have distinct filenames."
                )
            script_names.add(path.name)
            required.add(path)
        # Source discovery cannot infer arbitrary backend file requirements, so
        # require every archive-eligible file under each local package directory.
        source_files = collect_archive_files(
            root, ArchiveConfig(), extra_files=(manifest, lock)
        )
        source_files = [
            path for path in source_files if not path.is_relative_to(envs_directory)
        ]
        for name, dependency in resolved.pypi_dependencies.items():
            if dependency.git or dependency.url:
                raise CondaWorkspacesError(
                    f"Image dependency '{name}' uses an unsupported Git or URL source."
                )
            if dependency.path is None:
                continue
            if dependency.editable:
                raise CondaWorkspacesError(
                    f"Image dependency '{name}' uses an unsupported editable install. "
                    "Use a non-editable local package."
                )
            raw = dependency.path
            if raw is None or has_absolute_path_syntax(raw) or raw.startswith("~"):
                raise CondaWorkspacesError(
                    f"Image dependency '{name}' must use a workspace-relative path."
                )
            path = root / dependency.path
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_dir():
                raise CondaWorkspacesError(
                    f"Image dependency '{name}' must be a directory "
                    "inside the workspace."
                )
            metadata = [
                path / filename
                for filename in ("pyproject.toml", "setup.py", "setup.cfg")
            ]
            if not any(candidate in selected for candidate in metadata):
                raise CondaWorkspacesError(
                    f"Image dependency '{name}' has no included Python build metadata."
                )
            required.update(
                candidate
                for candidate in source_files
                if candidate.is_relative_to(path)
            )
        if not required.issubset(selected):
            raise CondaWorkspacesError(
                "Archive filters exclude the manifest, lockfile, "
                "activation scripts, or local package sources.",
                hints=[
                    (
                        "Adjust [workspace.archive] include/exclude rules "
                        "and track required source files."
                    )
                ],
            )
        if output is not None:
            output = output.expanduser().absolute()
            validate_file_output(output)
            if output.exists():
                raise CondaWorkspacesError(
                    "Image output already exists. Choose a new output path."
                )
            files = [path for path in files if path != output]
        validate_archive_members_for_create(root=root, files=files)
        manifest_bytes = read_regular_file_bytes(
            manifest, maximum_bytes=MAX_LOCKFILE_BYTES, label="workspace manifest"
        )
        if (
            config._manifest_text is not None
            and manifest_bytes.decode("utf-8") != config._manifest_text
        ):
            raise CondaWorkspacesError(
                "Workspace manifest changed during image preparation. "
                "Retry the command."
            )
        hashes = {
            manifest.relative_to(root).as_posix(): hashlib.sha256(
                manifest_bytes
            ).hexdigest(),
            lock.relative_to(root).as_posix(): hashlib.sha256(lock_bytes).hexdigest(),
        }
        return cls(
            config,
            environment,
            platform,
            OCI_PLATFORMS[subdir],
            command,
            tags,
            output,
            load,
            push,
            base_image,
            builder,
            files,
            hashes,
            workspace,
            prefix,
        )

    @property
    def entrypoint(self) -> str:
        """Keep activation alongside its workspace when images are combined."""
        return str(PurePosixPath(self.workspace) / ".conda/bin/workspace-entrypoint")

    def recipe(self) -> str:
        """Return the BuildKit recipe with argv encoded as Dockerfile JSON arrays."""
        manifest = (
            Path(self.config.manifest_path).relative_to(self.config.root).as_posix()
        )
        install = [
            "/opt/conda/bin/python",
            "-m",
            "conda",
            "workspace",
            "--file",
            str(PurePosixPath(self.workspace) / manifest),
            "install",
            "--locked",
            "--yes",
            "--environment",
            self.environment,
            "--platform",
            self.platform,
            "--prefix",
            self.prefix,
        ]
        bootstrap = [
            "/opt/conda/bin/conda",
            "install",
            "--yes",
            "--override-channels",
            "--channel",
            "conda-forge",
            *BUILD_TOOLS,
        ]
        runtime_path = (
            f"{self.prefix}/bin:{PurePosixPath(self.entrypoint).parent}".replace(
                "\\", "\\\\"
            ).replace('"', '\\"')
        )
        return "\n".join(
            [
                "# syntax=docker/dockerfile:1",
                f"FROM {BOOTSTRAP_IMAGE} AS bootstrap",
                "RUN " + json.dumps(bootstrap),
                f"FROM {self.base_image} AS sources",
                "RUN "
                + json.dumps(
                    [
                        "/bin/bash",
                        "-ec",
                        (
                            'if [[ -e "$1" || -L "$1" ]]; then '
                            'echo "Base image already contains $1. '
                            'Choose a base without an installed workspace." >&2; '
                            "exit 1; fi"
                        ),
                        "--",
                        self.workspace,
                    ]
                ),
                f'WORKDIR "{self.workspace}"',
                "ADD " + json.dumps(["workspace.tar.gz", self.workspace + "/"]),
                f"FROM {self.base_image} AS build",
                "COPY --from=bootstrap /opt/conda /opt/conda",
                "COPY --from=sources " + json.dumps([self.workspace, self.workspace]),
                "COPY tools /build",
                "ENV PYTHONPATH=/build",
                f'WORKDIR "{self.workspace}"',
                "RUN " + json.dumps([*install, "--download-only"]),
                "RUN --network=none " + json.dumps(install),
                "RUN --network=none "
                + json.dumps(
                    [
                        "/opt/conda/bin/python",
                        "-m",
                        "conda_workspaces.image_entrypoint",
                        self.prefix,
                        "/build/entrypoint.sh",
                    ]
                ),
                f"FROM {self.base_image}",
                "COPY --from=build " + json.dumps([self.prefix, self.prefix]),
                "COPY --from=sources " + json.dumps([self.workspace, self.workspace]),
                (
                    "COPY --from=build --chmod=0755 "
                    + json.dumps(["/build/entrypoint.sh", self.entrypoint])
                ),
                f'ENV PATH="{runtime_path}:$PATH"',
                f'WORKDIR "{self.workspace}"',
                "ENTRYPOINT " + json.dumps([self.entrypoint]),
                "CMD " + json.dumps(list(self.command)),
                "",
            ]
        )

    def result(self) -> dict[str, object]:
        """Describe the selected workspace, platform, and image destination."""
        return {
            "success": True,
            "environment": self.environment,
            "workspace": self.workspace,
            "prefix": self.prefix,
            "platform": self.platform,
            "oci_platform": self.oci_platform,
            "tags": list(self.tags),
            "output": str(self.output) if self.output is not None else None,
            "load": self.load,
            "push": self.push,
        }

    def preview(self) -> dict[str, object]:
        """Describe the recipe and inputs without requiring container tooling."""
        return {
            **self.result(),
            "base_image": self.base_image,
            "builder": self.builder,
            "recipe": self.recipe(),
            "files": [
                path.relative_to(self.config.root).as_posix() for path in self.files
            ],
            "build_packages": list(self.build_packages()),
        }

    def build_packages(self) -> dict[str, Path]:
        """Use the invoking tools' Python sources with native builder dependencies."""
        packages = {"conda_workspaces": Path(__file__).parent}
        pypi = find_spec("conda_pypi")
        if pypi is not None and pypi.origin is not None:
            packages["conda_pypi"] = Path(pypi.origin).parent
        return packages

    def run_builder(self, args: list[str], *, quiet: bool = False) -> None:
        """Run Buildx without shell interpolation, keeping build logs off stdout."""
        try:
            subprocess.run(
                ["docker", "buildx", *args],
                check=True,
                text=True,
                stdout=subprocess.DEVNULL if quiet else sys.stderr,
                stderr=sys.stderr,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CondaWorkspacesError(
                "Docker Buildx failed to build the workspace image.",
                hints=[
                    (
                        "Check the builder output. OCI archives require "
                        "an OCI-capable driver such as docker-container."
                    )
                ],
            ) from exc

    def build(self) -> dict[str, object]:
        """Build using an existing builder or a temporary docker-container builder."""
        if shutil.which("docker") is None:
            raise CondaWorkspacesError(
                "Workspace images require Docker with the Buildx plugin.",
                hints=[
                    (
                        "Install Docker and Buildx, then retry. "
                        "--dry-run does not require them."
                    )
                ],
            )
        with tempfile.TemporaryDirectory(prefix="conda-workspace-image-") as temporary:
            directory = Path(temporary)
            context = directory / "context"
            context.mkdir()
            create_archive(
                Path(self.config.root),
                context / "workspace.tar.gz",
                self.config.archive,
                files=self.files,
                regular_members=tuple(self.input_hashes),
                regular_member_hashes=self.input_hashes,
            )
            (context / "Dockerfile").write_text(self.recipe(), encoding="utf-8")
            for name, source in self.build_packages().items():
                shutil.copytree(
                    source,
                    context / "tools" / name,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            builder = self.builder or "conda-workspaces-" + uuid.uuid4().hex
            created = False
            try:
                if self.builder is None:
                    self.run_builder(
                        ["create", "--name", builder, "--driver", "docker-container"],
                        quiet=True,
                    )
                    created = True
                args = [
                    "build",
                    "--builder",
                    builder,
                    "--platform",
                    self.oci_platform,
                    "--metadata-file",
                    str(directory / "metadata.json"),
                ]
                for tag in self.tags:
                    args.extend(["--tag", tag])
                if self.output is not None:
                    args.extend(
                        ["--output", f"type=oci,dest={directory / 'image.tar'}"]
                    )
                else:
                    args.append("--load" if self.load else "--push")
                args.append(str(context))
                self.run_builder(args)
                metadata = json.loads(
                    (directory / "metadata.json").read_text(encoding="utf-8")
                )
                if self.output is not None:
                    with atomic_binary_writer(
                        self.output, expected_generation=None
                    ) as target:
                        with (directory / "image.tar").open("rb") as source:
                            shutil.copyfileobj(source, target)
            except (OSError, ValueError) as exc:
                raise CondaWorkspacesError(
                    f"Cannot publish workspace image output: {exc}"
                ) from exc
            finally:
                if created:
                    try:
                        self.run_builder(["rm", builder], quiet=True)
                    except CondaWorkspacesError:
                        print(
                            f"Could not remove temporary Buildx builder {builder}.",
                            file=sys.stderr,
                        )
        return {
            **self.result(),
            "digest": metadata.get("containerimage.digest"),
            "image_id": metadata.get("containerimage.config.digest"),
        }
