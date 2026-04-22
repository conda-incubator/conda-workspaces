"""Environment manager — create, update, and remove project-local envs.

Uses conda's Solver API to install packages into project-scoped
environments under ``.conda/envs/<name>/``.  Each environment is
a standard conda prefix that can be activated with ``conda activate``.
"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.constants import ChannelPriority, UpdateModifier
from conda.base.context import context as conda_context
from conda.core.envs_manager import PrefixData, unregister_env
from conda.exceptions import UnsatisfiableError
from conda.gateways.disk.delete import rm_rf
from conda.models.match_spec import MatchSpec

from .exceptions import SolveError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .context import WorkspaceContext
    from .resolver import ResolvedEnvironment

log = logging.getLogger(__name__)


def _iter_installed_prefixes(envs_dir: Path) -> Iterator[Path]:
    """Yield paths of valid conda environments under *envs_dir*."""
    if not envs_dir.is_dir():
        return
    for d in envs_dir.iterdir():
        if d.is_dir() and PrefixData(str(d)).is_environment():
            yield d


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


def _apply_activation_env(prefix: Path, env_vars: dict[str, str]) -> None:
    """Write environment variables to the prefix state file.

    These are automatically set/unset by ``conda activate``/``deactivate``.
    """
    if not env_vars:
        return
    pd = PrefixData(str(prefix))
    pd.set_environment_env_vars(env_vars)
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
    activate_d.mkdir(parents=True, exist_ok=True)
    for script_path in scripts:
        src = Path(script_path)
        if not src.is_absolute():
            log.warning(
                "Activation script '%s' is not an absolute path; skipping. "
                "Scripts should be resolved to absolute paths by the resolver.",
                script_path,
            )
            continue
        if not src.exists():
            log.warning("Activation script '%s' not found; skipping", script_path)
            continue
        dest = activate_d / src.name
        shutil.copy2(src, dest)
        log.info("Copied activation script: %s -> %s", src, dest)


def _build_pypi_specs(
    resolved: ResolvedEnvironment,
) -> list[MatchSpec]:
    """Translate PyPI dependencies into conda MatchSpecs.

    Uses ``conda_pypi.translate.pypi_to_conda_name`` to map PyPI package
    names to their conda equivalents (via the grayskull mapping).  Only
    simple version-spec dependencies are translated; path, git, and URL
    deps are skipped (handled separately by ``_install_editable_deps``).

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
        names = ", ".join(str(d) for d in pypi_deps)
        log.warning(
            "PyPI dependencies found but conda-pypi is not installed.\n"
            "  Skipped PyPI packages: %s\n"
            "  Install conda-pypi to enable: conda install conda-pypi",
            names,
        )
        return []

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
                "Git/URL PyPI dependency '%s' is not yet supported; skipping",
                dep,
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
    except ImportError:
        names = ", ".join(str(d) for d in path_deps)
        log.warning(
            "Path PyPI dependencies found but conda-pypi is not installed.\n"
            "  Skipped: %s\n"
            "  Install conda-pypi to enable: conda install conda-pypi",
            names,
        )
        return

    for dep in path_deps:
        source_path = Path(dep.path).expanduser()  # type: ignore[arg-type]
        distribution = "editable" if dep.editable else "wheel"
        log.info("Building %s (%s) from %s", dep.name, distribution, source_path)
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
            log.warning(
                "Failed to install path PyPI dependency '%s': %s",
                dep.name,
                exc,
            )


def install_environment(
    ctx: WorkspaceContext,
    resolved: ResolvedEnvironment,
    *,
    force_reinstall: bool = False,
    dry_run: bool = False,
) -> None:
    """Create or update a project-local environment.

    Uses conda's Solver API directly instead of shelling out, which
    avoids the overhead of a subprocess and gives full control over
    the solve/install transaction.

    PyPI dependencies are translated to conda names and merged into
    the same solver call as conda dependencies, relying on
    ``conda-pypi``'s wheel extractor and ``conda-rattler-solver`` to
    resolve and install them in a single pass.

    Raises ``SolveError`` if dependency resolution fails.
    """
    prefix = ctx.env_prefix(resolved.name)
    exists = ctx.env_exists(resolved.name)

    if exists and force_reinstall:
        rm_rf(prefix)
        exists = False

    # Build the spec list from resolved dependencies
    specs = [
        MatchSpec(dep.conda_build_form())
        for dep in resolved.conda_dependencies.values()
    ]

    # Translate PyPI deps to conda specs and merge into the same list
    specs.extend(_build_pypi_specs(resolved))

    # Add system requirements as virtual package constraints
    _apply_system_requirements(resolved, specs)

    if not specs:
        prefix.mkdir(parents=True, exist_ok=True)
        _apply_activation_env(prefix, resolved.activation_env)
        _apply_activation_scripts(prefix, resolved.activation_scripts)
        return

    # Get the solver backend (respects solver plugins)
    solver_backend = conda_context.plugin_manager.get_cached_solver_backend()
    if solver_backend is None:
        raise SolveError(resolved.name, "No solver backend found")

    channels = list(resolved.channels)
    subdirs = conda_context.subdirs

    with _channel_priority_override(resolved.channel_priority):
        solver = solver_backend(
            str(prefix),
            channels,
            subdirs,
            specs_to_add=specs,
        )

        try:
            if exists:
                txn = solver.solve_for_transaction(
                    update_modifier=UpdateModifier.FREEZE_INSTALLED,
                )
            else:
                txn = solver.solve_for_transaction()
        except (UnsatisfiableError, SystemExit) as exc:
            raise SolveError(resolved.name, str(exc)) from exc

    sys.stdout.flush()

    if txn.nothing_to_do:
        _apply_activation_env(prefix, resolved.activation_env)
        _apply_activation_scripts(prefix, resolved.activation_scripts)
        return

    if dry_run:
        txn.print_transaction_summary()
        sys.stdout.flush()
        return

    txn.download_and_extract()
    txn.execute()
    sys.stdout.flush()

    _apply_activation_env(prefix, resolved.activation_env)
    _apply_activation_scripts(prefix, resolved.activation_scripts)

    # Install local-path PyPI deps that can't go through the solver
    if not dry_run:
        _install_path_deps(prefix, resolved)


def remove_environment(ctx: WorkspaceContext, env_name: str) -> None:
    """Remove a project-local environment by deleting its prefix."""
    prefix = ctx.env_prefix(env_name)
    if prefix.is_dir():
        unregister_env(str(prefix))
        rm_rf(prefix)


def clean_all(ctx: WorkspaceContext) -> None:
    """Remove all project-local environments."""
    envs_dir = ctx.envs_dir
    for d in _iter_installed_prefixes(envs_dir):
        unregister_env(str(d))
    if envs_dir.is_dir():
        rm_rf(envs_dir)


def list_installed_environments(ctx: WorkspaceContext) -> list[str]:
    """Return names of environments that are currently installed."""
    return sorted(d.name for d in _iter_installed_prefixes(ctx.envs_dir))


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
