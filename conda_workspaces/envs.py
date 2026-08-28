"""Environment manager — create, update, and remove project-local envs.

Uses conda's Solver API to install packages into project-scoped
environments under ``.conda/envs/<name>/``.  Each environment is
a standard conda prefix that can be activated with ``conda activate``.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import secrets
import shutil
import stat
import sys
import tempfile
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from conda.base.constants import (
    PREFIX_STATE_FILE,
    RESERVED_ENV_VARS,
    ChannelPriority,
    UpdateModifier,
)
from conda.base.context import context as conda_context
from conda.core.envs_manager import PrefixData, unregister_env
from conda.exceptions import PackageNotInstalledError, UnsatisfiableError
from conda.history import History
from conda.models.match_spec import MatchSpec

from .context import isolated_package_cache
from .exceptions import CondaWorkspacesError, EnvironmentNotInstalledError, SolveError
from .models import has_url_credentials, redact_url_text
from .parsing import validate_document_limits
from .paths import (
    anchored_directory,
    atomic_binary_writer,
    has_absolute_path_syntax,
    output_paths_collide,
    read_regular_file_bytes,
    read_regular_file_bytes_with_generation,
    regular_file_generation,
    validate_directory_output,
    validate_file_output,
    validate_path_parent,
)
from .terminal import escape_for_console

if TYPE_CHECKING:
    from typing import Any

    from .context import WorkspaceContext
    from .paths import FileGeneration
    from .resolver import ResolvedEnvironment

log = logging.getLogger(__name__)
MAX_ACTIVATION_METADATA_BYTES = 16 * 1024**2


class PackageRow(TypedDict):
    """JSON-compatible details for an installed package."""

    name: str
    version: str
    build: str


@contextmanager
def _channel_priority_override(priority: str | None):
    """Context manager that temporarily overrides channel_priority."""
    if priority is None:
        yield
        return
    with conda_context._override("channel_priority", ChannelPriority(priority)):
        yield


def _apply_system_requirements(
    resolved: ResolvedEnvironment,
    specs: list[MatchSpec],
) -> list[MatchSpec]:
    """Add virtual package constraints from system_requirements to the spec list."""
    for pkg_name, version in resolved.system_requirements.items():
        virtual_name = pkg_name if pkg_name.startswith("__") else f"__{pkg_name}"
        specs.append(MatchSpec(f"{virtual_name} >={version}"))
    return specs


def _validate_prefix_metadata_path(
    prefix: Path,
    path: Path,
    *,
    directory: bool,
) -> None:
    """Reject linked or non-directory ancestors below an environment prefix."""
    if prefix.is_symlink() or (prefix.exists() and not prefix.is_dir()):
        raise CondaWorkspacesError(
            f"Environment prefix is not a regular directory: {prefix}"
        )
    try:
        relative = path.relative_to(prefix)
    except ValueError as exc:
        raise CondaWorkspacesError(
            f"Activation metadata path escapes the environment prefix: {path}"
        ) from exc
    current = prefix
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(current_stat.st_mode):
            raise CondaWorkspacesError(
                f"Activation metadata path cannot contain a symlink: {current}"
            )
        is_leaf = index == len(relative.parts) - 1
        if not is_leaf or directory:
            if not stat.S_ISDIR(current_stat.st_mode):
                raise CondaWorkspacesError(
                    f"Activation metadata parent is not a directory: {current}"
                )
        elif not stat.S_ISREG(current_stat.st_mode):
            raise CondaWorkspacesError(
                f"Activation metadata path is not a regular file: {current}"
            )


def _read_activation_state(
    path: Path,
) -> tuple[dict[str, Any], FileGeneration | None]:
    """Read a bounded regular prefix state file without following its leaf."""
    if not path.exists() and not path.is_symlink():
        return {}, None
    try:
        content, generation = read_regular_file_bytes_with_generation(
            path,
            maximum_bytes=MAX_ACTIVATION_METADATA_BYTES,
            label="activation state",
        )
        state = json.loads(content)
        validate_document_limits(state, label="Activation state JSON")
    except (UnicodeError, ValueError) as exc:
        raise CondaWorkspacesError(
            f"Cannot read activation state safely: {path}"
        ) from exc
    if not isinstance(state, dict):
        raise CondaWorkspacesError(
            f"Activation state must contain a JSON object: {path}"
        )
    return state, generation


def _apply_activation_env(prefix: Path, env_vars: dict[str, str]) -> None:
    """Write environment variables to the prefix state file.

    These are automatically set/unset by ``conda activate``/``deactivate``.
    """
    if not env_vars:
        return
    state_path = prefix / PREFIX_STATE_FILE
    _validate_prefix_metadata_path(prefix, state_path, directory=False)
    state, state_generation = _read_activation_state(state_path)
    current_env_vars = state.get("env_vars")
    if current_env_vars is None:
        current_env_vars = {}
        state["env_vars"] = current_env_vars
    if not isinstance(current_env_vars, dict):
        raise CondaWorkspacesError(
            f"Activation state env_vars must contain a JSON object: {state_path}"
        )
    invalid_vars = [name for name in RESERVED_ENV_VARS if name in env_vars]
    if invalid_vars:
        names = ", ".join(escape_for_console(name) for name in invalid_vars)
        command_names = " ".join(escape_for_console(name) for name in invalid_vars)
        warnings.warn(
            f"WARNING: the given environment variable(s) are reserved and will be"
            f" ignored: {names}. Setting these environment variables may produce"
            " unexpected results.\n\nRemove the invalid configuration with"
            " `conda env config vars unset -p "
            f"{escape_for_console(prefix)} {command_names}`."
        )
    current_env_vars.update(env_vars)
    serialized_state = json.dumps(state, ensure_ascii=False)
    with atomic_binary_writer(
        state_path,
        expected_generation=state_generation,
    ) as stream:
        stream.write(serialized_state.encode("utf-8"))
    n = len(env_vars)
    noun = "variable" if n == 1 else "variables"
    log.info("Set %d activation environment %s", n, noun)


def activate_d_scripts(prefix: Path) -> set[str]:
    """Return the filenames under ``$PREFIX/etc/conda/activate.d/``.

    Returns an empty set when the directory does not exist.  Used to
    detect new activation hooks installed into an environment, e.g. to
    warn that a ``conda workspace shell`` session needs to be re-spawned.
    """
    activate_d = prefix / "etc" / "conda" / "activate.d"
    _validate_prefix_metadata_path(prefix, activate_d, directory=True)
    if not activate_d.is_dir():
        return set()
    return {p.name for p in activate_d.iterdir()}


def _apply_activation_scripts(prefix: Path, scripts: list[str]) -> None:
    """Copy activation scripts into ``$PREFIX/etc/conda/activate.d/``.

    Conda sources all scripts in this directory on ``conda activate``.
    Scripts are resolved relative to the workspace root (stored in the
    manifest_path parent). Only files that exist are copied.
    """
    if not scripts:
        return
    activate_d = prefix / "etc" / "conda" / "activate.d"
    _validate_prefix_metadata_path(prefix, activate_d, directory=True)
    for script_path in scripts:
        src = Path(script_path)
        if not src.is_absolute():
            log.warning(
                "Activation script '%s' is not an absolute path; skipping. "
                "Scripts should be resolved to absolute paths by the resolver.",
                escape_for_console(script_path),
            )
            continue
        if not src.exists() and not src.is_symlink():
            log.warning(
                "Activation script '%s' not found, skipping",
                escape_for_console(script_path),
            )
            continue
        if src.is_symlink() or not src.is_file():
            raise CondaWorkspacesError(
                f"Activation script is not a regular file: {src}"
            )
        dest = activate_d / src.name
        if output_paths_collide(src, dest):
            continue
        _validate_prefix_metadata_path(prefix, dest, directory=False)
        destination_generation = regular_file_generation(dest)
        try:
            content = read_regular_file_bytes(
                src,
                maximum_bytes=MAX_ACTIVATION_METADATA_BYTES,
                label="activation script",
            )
        except ValueError as exc:
            raise CondaWorkspacesError(
                f"Cannot read activation script safely: {src}"
            ) from exc
        with atomic_binary_writer(
            dest,
            expected_generation=destination_generation,
        ) as stream:
            stream.write(content)
        log.info(
            "Copied activation script: %s -> %s",
            escape_for_console(src),
            escape_for_console(dest),
        )


def validate_activation_metadata(
    prefix: Path,
    resolved: ResolvedEnvironment,
) -> None:
    """Validate activation inputs without changing an environment prefix.

    Solver installs and exact lockfile installs share this validation so a
    multi-environment preflight can reject unsafe metadata before any prefix
    transaction begins.
    """
    activate_d = prefix / "etc" / "conda" / "activate.d"
    _validate_prefix_metadata_path(
        prefix,
        activate_d,
        directory=True,
    )
    if resolved.activation_env:
        state_path = prefix / PREFIX_STATE_FILE
        _validate_prefix_metadata_path(
            prefix,
            state_path,
            directory=False,
        )
        validate_file_output(state_path)
        if state_path.is_file():
            _read_activation_state(state_path)
    if resolved.activation_scripts:
        validate_directory_output(activate_d)
        for script_path in resolved.activation_scripts:
            source = Path(script_path)
            if source.is_absolute() and source.is_symlink():
                raise CondaWorkspacesError(
                    f"Activation script is not a regular file: {source}"
                )
            if source.is_absolute() and source.exists():
                if not source.is_file():
                    raise IsADirectoryError(
                        f"Activation script is not a file: {source}"
                    )
                destination = activate_d / source.name
                _validate_prefix_metadata_path(
                    prefix,
                    destination,
                    directory=False,
                )
                validate_file_output(destination)


def validate_path_dependencies(resolved: ResolvedEnvironment) -> None:
    """Validate local PyPI project inputs without building or installing them.

    Building requires Python in the target prefix, so exact-install preflights
    validate the stable inputs and conda-pypi entry points up front, then leave
    the actual build for execution after the conda packages are installed.
    """
    path_dependencies = [
        dependency
        for dependency in resolved.pypi_dependencies.values()
        if dependency.path
    ]
    if not path_dependencies:
        return

    try:
        from conda_pypi.build import (  # type: ignore[import-untyped]
            pypa_to_conda as _pypa_to_conda,
        )
        from conda_pypi.installer import (  # type: ignore[import-untyped]
            install_ephemeral_conda as _install_ephemeral_conda,
        )
    except ImportError as exc:
        names = ", ".join(
            str(dependency.redacted()) for dependency in path_dependencies
        )
        raise SolveError(
            resolved.name,
            f"Path PyPI dependencies require conda-pypi. Could not install: {names}",
        ) from exc
    del _install_ephemeral_conda, _pypa_to_conda

    for dependency in path_dependencies:
        assert dependency.path is not None
        if has_url_credentials(dependency.path) and (
            not has_absolute_path_syntax(dependency.path)
            or "://" in dependency.path
            or dependency.path.startswith("//")
        ):
            raise SolveError(
                resolved.name,
                f"Path PyPI dependency '{dependency.name}' contains URL credentials.",
            )
        source_path = Path(dependency.path).expanduser().absolute()
        try:
            validate_path_parent(source_path / ".conda-workspaces-source")
            with anchored_directory(source_path):
                pass
        except (OSError, ValueError) as exc:
            raise SolveError(
                resolved.name,
                "Path PyPI dependency "
                f"'{dependency.name}' must be an existing regular directory: "
                f"{source_path}",
            ) from exc


def _build_pypi_specs(
    resolved: ResolvedEnvironment,
) -> list[MatchSpec]:
    """Translate PyPI dependencies into conda MatchSpecs.

    Uses ``conda_pypi.translate.pypi_to_conda_name`` to map PyPI package
    names to their conda equivalents (via the grayskull mapping).  Only
    simple version-spec dependencies are translated; path, git, and URL
    deps are skipped. Local path deps are handled separately by
    ``_install_path_deps``; git and URL deps are not installed yet.

    Returns an empty list if ``conda-pypi`` is not installed.
    """
    pypi_deps = [
        dep
        for dep in resolved.pypi_dependencies.values()
        if not dep.path and not dep.git and not dep.url
    ]
    if not pypi_deps:
        return []

    try:
        from conda_pypi.translate import (  # type: ignore[import-untyped]
            pypi_to_conda_name,
        )
    except ImportError:
        names = ", ".join(escape_for_console(d) for d in pypi_deps)
        log.warning(
            "PyPI dependencies found but conda-pypi is not installed.\n"
            "  Skipped PyPI packages: %s\n"
            "  Install conda-pypi to enable: "
            "conda install -n base conda-forge::conda-pypi",
            names,
        )
        return []

    if importlib.util.find_spec("conda_rattler_solver") is None:
        names = ", ".join(escape_for_console(d) for d in pypi_deps)
        log.warning(
            "PyPI dependencies found but conda-rattler-solver is not installed.\n"
            "  PyPI packages: %s\n"
            "  conda-rattler-solver is required as the solver backend for PyPI\n"
            "  dependencies. Install it with: "
            "conda install -n base conda-forge::conda-rattler-solver",
            names,
        )

    specs: list[MatchSpec] = []
    for dep in pypi_deps:
        conda_name = pypi_to_conda_name(dep.name)
        extras = f"[{','.join(dep.extras)}]" if dep.extras else ""
        base = f"{conda_name}{extras}"
        spec_str = f"{base}{dep.spec}" if dep.spec else base
        specs.append(MatchSpec(spec_str))
    return specs


def _install_path_deps(
    prefix: Path,
    resolved: ResolvedEnvironment,
) -> None:
    """Install local-path PyPI deps via conda-pypi's build system.

    Only ``path`` deps are supported — these point to local Python
    projects that conda-pypi can build into ``.conda`` packages.
    Git and URL deps are not yet supported and are skipped with a
    warning.
    """
    path_deps = []
    for dep in resolved.pypi_dependencies.values():
        if dep.git or dep.url:
            log.warning(
                "Git/URL PyPI dependency '%s' is not yet supported and will be skipped",
                escape_for_console(dep.redacted()),
            )
        elif dep.path:
            path_deps.append(dep)

    if not path_deps:
        return

    try:
        from conda_pypi.build import pypa_to_conda  # type: ignore[import-untyped]
        from conda_pypi.installer import (  # type: ignore[import-untyped]
            install_ephemeral_conda,
        )
    except ImportError as exc:
        names = ", ".join(str(dependency.redacted()) for dependency in path_deps)
        raise SolveError(
            resolved.name,
            f"Path PyPI dependencies require conda-pypi. Could not install: {names}",
        ) from exc

    for dep in path_deps:
        if dep.path is None:
            continue
        if has_url_credentials(dep.path) and (
            not has_absolute_path_syntax(dep.path)
            or "://" in dep.path
            or dep.path.startswith("//")
        ):
            raise SolveError(
                resolved.name,
                f"Path PyPI dependency '{dep.name}' contains URL credentials.",
            )
        source_path = Path(dep.path).expanduser()
        distribution = "editable" if dep.editable else "wheel"
        log.info(
            "Building %s (%s) from %s",
            escape_for_console(dep.name),
            distribution,
            escape_for_console(source_path),
        )
        try:
            with tempfile.TemporaryDirectory("conda-pypi") as output_dir:
                package = pypa_to_conda(
                    source_path,
                    distribution=distribution,
                    output_path=Path(output_dir),
                    prefix=prefix,
                )
                install_ephemeral_conda(prefix, package)
        except Exception as exc:
            raise SolveError(
                resolved.name,
                f"Failed to install path PyPI dependency '{dep.name}': {exc}",
            ) from exc


def install_environment(
    ctx: WorkspaceContext,
    resolved: ResolvedEnvironment,
    *,
    force_reinstall: bool = False,
    dry_run: bool = False,
    prune: bool = False,
    update_names: set[str] | None = None,
) -> Path:
    """Create or update a project-local environment.

    Uses conda's Solver API directly instead of shelling out, which
    avoids the overhead of a subprocess and gives full control over
    the solve/install transaction.

    Version-only PyPI dependencies are translated to conda names and
    merged into the same solver call as conda dependencies, relying on
    ``conda-pypi`` and ``conda-rattler-solver`` to resolve and install
    them in a single pass. Local path PyPI dependencies are built and
    installed after the conda transaction.

    When *dry_run* is true, solving and transaction rendering still run,
    but the prefix and its activation metadata remain unchanged. The
    returned path is the prefix used for solving.

    When *prune* is true, requested specs absent from *resolved* are
    removed in a separate transaction before the remaining specs are
    installed. Conda-libmamba requires add and remove requests to use
    separate solver instances.

    When *update_names* is supplied, the prefix must already exist. Only
    those declared and installed conda roots are passed to the solver,
    while other installed records remain frozen unless satisfying the
    requested update requires a dependency change.

    Raises ``SolveError`` if dependency resolution fails.
    """
    prefix = ctx.env_prefix(resolved.name)
    validate_directory_output(prefix)
    exists = ctx.env_exists(resolved.name)
    solver_prefix = prefix
    metadata_prefix = prefix

    if exists and force_reinstall:
        fresh_prefix = prefix.with_name(f".{prefix.name}.dry-run")
        suffix = 1
        while fresh_prefix.exists() or fresh_prefix.is_symlink():
            fresh_prefix = prefix.with_name(f".{prefix.name}.dry-run-{suffix}")
            suffix += 1
        metadata_prefix = fresh_prefix
        if dry_run:
            solver_prefix = fresh_prefix

    if update_names is not None:
        if force_reinstall or prune:
            raise ValueError(
                "Selective updates cannot be combined with reinstall or prune"
            )
        if not exists:
            raise EnvironmentNotInstalledError(resolved.name)
        missing = update_names - resolved.conda_dependencies.keys()
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"Cannot update undeclared conda dependencies: {names}")
        prefix_data = PrefixData(str(prefix))
        for name in sorted(update_names):
            if prefix_data.get(name, None) is None:
                raise PackageNotInstalledError(str(prefix), name)
        specs = [resolved.conda_dependencies[name] for name in sorted(update_names)]
    else:
        specs = list(resolved.conda_dependencies.values())
        specs.extend(_build_pypi_specs(resolved))

    # Add system requirements as virtual package constraints
    _apply_system_requirements(resolved, specs)

    specs_to_remove: list[MatchSpec] = []
    if prune and exists and not force_reinstall:
        desired_names = {spec.name for spec in specs}
        desired_names.update(
            dependency.name
            for dependency in resolved.pypi_dependencies.values()
            if dependency.path
        )
        specs_to_remove = [
            spec
            for name, spec in History(str(prefix)).get_requested_specs_map().items()
            if name not in desired_names
        ]

    validate_activation_metadata(metadata_prefix, resolved)

    if not specs and not specs_to_remove:
        validate_file_output(metadata_prefix / "conda-meta" / "history")

    if exists and force_reinstall and not dry_run:
        remove_environment(ctx, resolved.name)
        exists = False

    if not specs and not specs_to_remove:
        if not dry_run:
            History(str(prefix)).init_log_file()
    else:
        with isolated_package_cache(dry_run):
            solver_backend = (
                conda_context.plugin_manager.get_cached_solver_backend()  # ty: ignore[missing-argument]
            )
            if solver_backend is None:
                raise SolveError(resolved.name, "No solver backend found")

            channels = list(resolved.channels)
            subdirs = conda_context.subdirs

            with _channel_priority_override(resolved.channel_priority):
                if specs_to_remove and specs:
                    validation_solver = solver_backend(
                        str(solver_prefix),
                        channels,
                        subdirs,
                        specs_to_add=specs,
                    )
                    try:
                        validation_txn = validation_solver.solve_for_transaction(
                            update_modifier=UpdateModifier.UPDATE_SPECS,
                            prune=True,
                        )
                    except (UnsatisfiableError, SystemExit) as exc:
                        raise SolveError(
                            resolved.name,
                            redact_url_text(str(exc)),
                        ) from exc

                    sys.stdout.flush()
                    if dry_run:
                        validation_txn.print_transaction_summary()
                        sys.stdout.flush()
                        return solver_prefix
                    validation_txn.download_and_extract()

                if specs_to_remove:
                    removal_solver = solver_backend(
                        str(solver_prefix),
                        channels,
                        subdirs,
                        specs_to_remove=specs_to_remove,
                    )
                    try:
                        removal_txn = removal_solver.solve_for_transaction(
                            update_modifier=UpdateModifier.UPDATE_SPECS,
                        )
                    except (UnsatisfiableError, SystemExit) as exc:
                        raise SolveError(
                            resolved.name,
                            redact_url_text(str(exc)),
                        ) from exc

                    sys.stdout.flush()
                    if dry_run:
                        removal_txn.print_transaction_summary()
                    elif not removal_txn.nothing_to_do:
                        removal_txn.download_and_extract()
                        removal_txn.execute()
                    sys.stdout.flush()

                if specs:
                    solver_kwargs: dict[str, Any] = (
                        {"command": "update"} if update_names is not None else {}
                    )
                    solver = solver_backend(
                        str(solver_prefix),
                        channels,
                        subdirs,
                        specs_to_add=specs,
                        **solver_kwargs,
                    )

                    try:
                        if exists and not force_reinstall:
                            txn = solver.solve_for_transaction(
                                update_modifier=UpdateModifier.FREEZE_INSTALLED,
                            )
                        else:
                            txn = solver.solve_for_transaction()
                    except (UnsatisfiableError, SystemExit) as exc:
                        raise SolveError(
                            resolved.name,
                            redact_url_text(str(exc)),
                        ) from exc

            sys.stdout.flush()

            if specs and not txn.nothing_to_do:
                if dry_run:
                    txn.print_transaction_summary()
                    sys.stdout.flush()
                    return solver_prefix
                txn.download_and_extract()
                txn.execute()
                sys.stdout.flush()

    if not dry_run:
        _apply_activation_env(prefix, resolved.activation_env)
        _apply_activation_scripts(prefix, resolved.activation_scripts)
        if update_names is None:
            _install_path_deps(prefix, resolved)
    return solver_prefix


def remove_anchored_directory(
    parent_descriptor: int,
    name: str,
    expected_identity: tuple[int, int],
) -> None:
    """Delete one directory tree through descriptors without following leaves.

    ``shutil.rmtree`` only gained its public ``dir_fd`` argument in Python
    3.11. This uses public ``os`` descriptor APIs so supported Python 3.10
    platforms keep the same anchored deletion guarantee.
    """
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise CondaWorkspacesError(
            f"Workspace environment directory changed before deletion: {name}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != expected_identity
        ):
            raise CondaWorkspacesError(
                f"Workspace environment directory changed before deletion: {name}"
            )
        with os.scandir(descriptor) as entries:
            for entry in entries:
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except FileNotFoundError as exc:
                    raise CondaWorkspacesError(
                        "Workspace environment entry changed during deletion: "
                        f"{entry.name}"
                    ) from exc
                if stat.S_ISDIR(entry_stat.st_mode):
                    remove_anchored_directory(
                        descriptor,
                        entry.name,
                        (entry_stat.st_dev, entry_stat.st_ino),
                    )
                else:
                    os.unlink(entry.name, dir_fd=descriptor)
        try:
            current = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError as exc:
            raise CondaWorkspacesError(
                f"Workspace environment directory changed during deletion: {name}"
            ) from exc
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != expected_identity
        ):
            raise CondaWorkspacesError(
                f"Workspace environment directory changed during deletion: {name}"
            )
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent_descriptor)


def remove_environment(
    ctx: WorkspaceContext,
    env_name: str,
    *,
    expected_envs_identity: tuple[int, int] | None = None,
    expected_prefix_identity: tuple[int, int] | None = None,
) -> None:
    """Remove a prefix without following a replaced directory generation."""
    prefix = ctx.env_prefix(env_name)
    envs_dir = ctx.envs_dir
    identity = (
        expected_envs_identity
        if expected_envs_identity is not None
        else ctx.envs_dir_identity()
    )
    if identity is None:
        return
    ctx.require_envs_dir_identity(identity)

    detached = envs_dir / f".{env_name}.remove-{secrets.token_hex(16)}"
    with anchored_directory(envs_dir) as descriptor:
        if descriptor is not None:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != identity:
                raise CondaWorkspacesError(
                    "Workspace environments directory changed while it was opened."
                )
            try:
                prefix_stat = os.stat(
                    env_name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                if expected_prefix_identity is not None:
                    raise CondaWorkspacesError(
                        f"Workspace environment prefix changed before removal: {prefix}"
                    ) from None
                return
            prefix_identity = prefix_stat.st_dev, prefix_stat.st_ino
            if not stat.S_ISDIR(prefix_stat.st_mode) or (
                expected_prefix_identity is not None
                and prefix_identity != expected_prefix_identity
            ):
                raise CondaWorkspacesError(
                    f"Workspace environment prefix changed before removal: {prefix}"
                )
            os.rename(
                env_name,
                detached.name,
                src_dir_fd=descriptor,
                dst_dir_fd=descriptor,
            )
            detached_stat = os.stat(
                detached.name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(detached_stat.st_mode)
                or (detached_stat.st_dev, detached_stat.st_ino) != prefix_identity
            ):
                raise CondaWorkspacesError(
                    "Workspace environment prefix changed while it was detached."
                )
            remove_anchored_directory(
                descriptor,
                detached.name,
                prefix_identity,
            )
        else:
            try:
                prefix_stat = prefix.lstat()
            except FileNotFoundError:
                if expected_prefix_identity is not None:
                    raise CondaWorkspacesError(
                        f"Workspace environment prefix changed before removal: {prefix}"
                    ) from None
                return
            prefix_identity = prefix_stat.st_dev, prefix_stat.st_ino
            if not stat.S_ISDIR(prefix_stat.st_mode) or (
                expected_prefix_identity is not None
                and prefix_identity != expected_prefix_identity
            ):
                raise CondaWorkspacesError(
                    f"Workspace environment prefix changed before removal: {prefix}"
                )
            ctx.require_envs_dir_identity(identity)
            os.replace(prefix, detached)
            detached_stat = detached.lstat()
            if (
                not stat.S_ISDIR(detached_stat.st_mode)
                or (detached_stat.st_dev, detached_stat.st_ino) != prefix_identity
            ):
                raise CondaWorkspacesError(
                    "Workspace environment prefix changed while it was detached."
                )
            ctx.require_envs_dir_identity(identity)
            shutil.rmtree(detached)

    ctx.require_envs_dir_identity(identity)
    unregister_env(str(prefix))


def clean_all(ctx: WorkspaceContext) -> None:
    """Remove all project-local environments."""
    envs_identity = ctx.envs_dir_identity()
    if envs_identity is None:
        return
    prefixes = list(ctx.iter_installed_prefixes())
    ctx.require_envs_dir_identity(envs_identity)
    for prefix, prefix_identity in prefixes:
        remove_environment(
            ctx,
            prefix.name,
            expected_envs_identity=envs_identity,
            expected_prefix_identity=prefix_identity,
        )


def list_installed_environments(ctx: WorkspaceContext) -> list[str]:
    """Return names of environments that are currently installed."""
    return sorted(prefix.name for prefix, _ in ctx.iter_installed_prefixes())


def list_installed_packages(ctx: WorkspaceContext, env_name: str) -> list[PackageRow]:
    """Return installed package details sorted by package name."""
    records = PrefixData(str(ctx.env_prefix(env_name))).iter_records()
    return [
        {"name": record.name, "version": record.version, "build": record.build}
        for record in sorted(records, key=lambda record: record.name)
    ]


def get_environment_info(
    ctx: WorkspaceContext, env_name: str
) -> dict[str, str | int | bool]:
    """Return basic info about an installed environment."""
    prefix = ctx.env_prefix(env_name)
    exists = ctx.env_exists(env_name)
    info: dict[str, str | int | bool] = {
        "name": env_name,
        "prefix": str(prefix),
        "exists": exists,
    }
    if exists:
        # Count installed packages via conda-meta
        meta_dir = prefix / "conda-meta"
        pkg_count = sum(1 for f in meta_dir.glob("*.json") if f.name != "history")
        info["packages"] = pkg_count
    return info
