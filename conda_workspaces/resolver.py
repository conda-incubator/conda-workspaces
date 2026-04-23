"""Feature-to-environment resolver.

Takes a ``WorkspaceConfig`` and resolves which conda/PyPI packages
need to be installed for a given environment by composing its
constituent features.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .exceptions import (
    PlatformError,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from conda.models.records import PackageRecord

    from .models import Channel, MatchSpec, PyPIDependency, WorkspaceConfig

log = logging.getLogger(__name__)


@dataclass
class ResolvedEnvironment:
    """The fully resolved dependency set for a single environment.

    This is what the environment manager uses to install or update
    a project-local conda environment.
    """

    name: str
    conda_dependencies: dict[str, MatchSpec] = field(default_factory=dict)
    pypi_dependencies: dict[str, PyPIDependency] = field(default_factory=dict)
    channels: list[Channel] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    activation_scripts: list[str] = field(default_factory=list)
    activation_env: dict[str, str] = field(default_factory=dict)
    system_requirements: dict[str, str] = field(default_factory=dict)
    channel_priority: str | None = None

    def virtual_package_overrides(self, platform: str) -> dict[str, str]:
        """Return ``CONDA_OVERRIDE_*`` env vars that enable a cross-platform solve.

        Mirrors ``rattler_virtual_packages::VirtualPackages::detect_for_platform``
        from ``rattler``: when we solve for a target that the *host* cannot
        detect a virtual package for (e.g. ``linux-64`` from macOS emits
        no ``__glibc`` record), inject conservative defaults so packages
        gated on those virtuals remain resolvable out of the box.

        Precedence (highest to lowest):

        1. ``CONDA_OVERRIDE_*`` already present in :data:`os.environ` — the
           user is explicitly in charge and this helper returns no entry
           for that key, leaving the existing value untouched.
        2. ``[system-requirements]`` declared in the manifest for the same
           virtual package (e.g. ``glibc = "2.28"``) — used as the override
           so the virtual package record lines up with the spec constraint
           :mod:`conda_workspaces.envs._apply_system_requirements` appends.
        3. A conservative built-in baseline (``__glibc == 2.17`` for any
           non-native linux target, ``__osx >= 10.15`` / ``>= 11.0`` for
           ``osx-64`` / ``osx-arm64`` cross-compiles, presence-only
           ``__win`` for win targets).

        ``__cuda`` and ``__archspec`` are *not* seeded — the caller must
        opt in via ``[system-requirements]`` or ``CONDA_OVERRIDE_*`` if
        they want those available.  Native solves (target family matches
        host family) return an empty mapping so byte-for-byte output stays
        unchanged.
        """
        from conda.base.context import context as conda_context

        def family(subdir: str) -> str:
            for fam in ("linux", "osx", "win"):
                if subdir.startswith(f"{fam}-"):
                    return fam
            return ""

        target_family = family(platform)
        if not target_family or family(conda_context.subdir) == target_family:
            return {}

        def req_version(name: str) -> str | None:
            """Look up a ``[system-requirements]`` entry by bare or ``__`` name."""
            return self.system_requirements.get(name) or self.system_requirements.get(
                f"__{name}"
            )

        baseline: dict[str, str] = {}
        if target_family == "linux":
            baseline["CONDA_OVERRIDE_GLIBC"] = req_version("glibc") or "2.17"
        elif target_family == "osx":
            default = "11.0" if platform == "osx-arm64" else "10.15"
            baseline["CONDA_OVERRIDE_OSX"] = req_version("osx") or default
        elif target_family == "win":
            baseline["CONDA_OVERRIDE_WIN"] = req_version("win") or "0"

        return {k: v for k, v in baseline.items() if k not in os.environ}

    @contextmanager
    def scoped_virtual_packages(self, platform: str) -> Iterator[None]:
        """Scope :meth:`virtual_package_overrides` around a solver call.

        Conda deprecated :func:`conda.common.io.env_vars` and its siblings
        in 26.9 (removal targeted for 27.3) and recommends
        ``monkeypatch.setenv`` / ``monkeypatch.delenv`` as replacements —
        but those are test-only.  This production path needs to scope
        ``CONDA_OVERRIDE_*`` overrides around a solver call, for which
        upstream does not ship a drop-in replacement, so we keep a small
        local context manager until conda exposes one (tracked in
        ``conda/conda#14095`` / PR ``conda/conda#15728``).
        """
        overrides = self.virtual_package_overrides(platform)
        if not overrides:
            yield
            return
        saved: dict[str, str | None] = {
            name: os.environ.get(name) for name in overrides
        }
        os.environ.update(overrides)
        try:
            yield
        finally:
            for name, previous in saved.items():
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous

    def solve_for_platform(
        self,
        platform: str,
        *,
        prefix: str | Path,
    ) -> list[PackageRecord]:
        """Solve this environment for *platform* and return package records.

        Uses conda's solver API to resolve dependencies without
        installing, producing the list of exact packages that would be
        installed.  Applies the same transformations as
        :func:`conda_workspaces.envs.install_environment`: PyPI deps
        are translated and merged, system requirements are added as
        virtual package constraints, and channel priority is honoured.

        The solver is targeted at *platform* by (a) constructing it
        with ``subdirs=(platform, "noarch")`` and (b) overriding
        ``context._subdir`` for the duration of the solve.  Conda's
        virtual package plugins (``__linux``, ``__osx``, ``__win``)
        gate on ``context.subdir``, so this single override also
        yields the correct cross-platform virtual package set.

        On cross-compiled targets the host cannot detect
        libc/kernel/macOS versions, so
        :meth:`scoped_virtual_packages` seeds conservative
        ``CONDA_OVERRIDE_*`` defaults for the duration of the solve.
        User knobs stay authoritative: explicit ``CONDA_OVERRIDE_*``
        env vars are left untouched, and ``[system-requirements]``
        versions are lifted into the override so ``__glibc >=2.28``
        in the manifest and the baseline record agree.

        *prefix* is the environment prefix path the solver should
        target — workspace-owned, so callers that run under a
        :class:`~conda_workspaces.context.WorkspaceContext` pass
        ``ctx.env_prefix(resolved.name)``.

        Raises :class:`~conda_workspaces.exceptions.SolveError` when
        the solver cannot satisfy the specs or no backend is
        registered.
        """
        from conda.base.context import context as conda_context
        from conda.common.io import captured
        from conda.exceptions import UnsatisfiableError
        from conda.models.match_spec import MatchSpec as CondaMatchSpec

        from .envs import (
            _apply_system_requirements,
            _build_pypi_specs,
            _channel_priority_override,
        )
        from .exceptions import SolveError

        specs = [
            CondaMatchSpec(dep.conda_build_form())
            for dep in self.conda_dependencies.values()
        ]

        specs.extend(_build_pypi_specs(self))
        _apply_system_requirements(self, specs)

        if not specs:
            return []

        solver_backend = conda_context.plugin_manager.get_cached_solver_backend()
        if solver_backend is None:
            raise SolveError(self.name, "No solver backend found", platform=platform)

        subdirs = (platform, "noarch")

        # The solver unconditionally prints ``Collecting package
        # metadata`` and ``Solving environment`` status lines through
        # conda's reporter plugin (even when ``context.quiet`` is set
        # — ``QuietSpinner`` still writes to stdout).  Route stdout
        # and stderr through ``conda.common.io.captured`` so the Rich
        # progress rendered by the caller is the only thing the user
        # sees.  Any captured output is discarded; diagnostics survive
        # via ``SolveError(str(exc))``.
        with (
            self.scoped_virtual_packages(platform),
            _channel_priority_override(self.channel_priority),
            conda_context._override("_subdir", platform),
            conda_context._override("quiet", True),
            captured(),
        ):
            solver = solver_backend(
                str(prefix),
                list(self.channels),
                subdirs,
                specs_to_add=specs,
            )

            try:
                return list(solver.solve_final_state())
            except (UnsatisfiableError, SystemExit) as exc:
                raise SolveError(self.name, str(exc), platform=platform) from exc

    def target_platforms(
        self,
        *,
        requested: tuple[str, ...] = (),
        fallback: str,
    ) -> tuple[str, ...]:
        """Return the platforms this environment should emit for.

        :attr:`platforms` is the declared set (feature ∩ workspace,
        already merged by :func:`resolve_environment`); if empty,
        *fallback* (typically the host subdir) is used instead.  When
        *requested* is supplied, the result is the intersection with
        that set, preserving caller-supplied order; any value not in
        the declared set raises :class:`PlatformError`.

        Used by :meth:`WorkspaceContext.envs_from_manifest` to decide
        which platforms a manifest-only export emits, and safe to use
        by any caller that needs the same policy.
        """
        declared_set = set(self.platforms) or {fallback}
        if not requested:
            return tuple(sorted(declared_set))
        unknown = [p for p in requested if p not in declared_set]
        if unknown:
            raise PlatformError(unknown[0], sorted(declared_set))
        return tuple(p for p in requested if p in declared_set)


def resolve_environment(
    config: WorkspaceConfig,
    env_name: str,
    platform: str | None = None,
) -> ResolvedEnvironment:
    """Resolve an environment by composing its features.

    Merges conda deps, PyPI deps, channels, activation scripts/env,
    and system requirements across all features in the environment.

    If *platform* is given, target-specific overrides are included
    and platform support is validated.
    """
    env = config.get_environment(env_name)

    # Validate platform if provided
    if platform and config.platforms and platform not in config.platforms:
        raise PlatformError(platform, config.platforms)

    resolved = ResolvedEnvironment(
        name=env_name,
        channel_priority=config.channel_priority,
    )

    # Merge dependencies
    resolved.conda_dependencies = config.merged_conda_dependencies(env, platform)
    resolved.pypi_dependencies = config.merged_pypi_dependencies(env, platform)
    resolved.channels = config.merged_channels(env)

    # Merge platforms: intersect feature platforms with workspace platforms
    feature_platforms: set[str] = set()
    features = config.resolve_features(env)
    for feat in features:
        if feat.platforms:
            if not feature_platforms:
                feature_platforms = set(feat.platforms)
            else:
                feature_platforms &= set(feat.platforms)

    if feature_platforms:
        resolved.platforms = sorted(feature_platforms)
    else:
        if any(f.platforms for f in features):
            log.warning(
                "Feature platform intersection for environment '%s' is empty; "
                "falling back to workspace platforms",
                env_name,
            )
        resolved.platforms = list(config.platforms)

    # Merge activation and system requirements
    for feat in features:
        resolved.activation_scripts.extend(feat.activation_scripts)
        resolved.activation_env.update(feat.activation_env)
        resolved.system_requirements.update(feat.system_requirements)

    return resolved


def resolve_all_environments(
    config: WorkspaceConfig,
    platform: str | None = None,
) -> dict[str, ResolvedEnvironment]:
    """Resolve all environments in the workspace.

    Returns a dict mapping environment name to its resolved deps.
    """
    return {
        name: resolve_environment(config, name, platform)
        for name in config.environments
    }


def known_platforms(
    config: WorkspaceConfig,
    resolved_envs: Iterable[ResolvedEnvironment] = (),
) -> set[str]:
    """All platforms this workspace could legitimately be solved for.

    Returns the union of workspace-level ``config.platforms`` and any
    feature-declared platforms surfaced through *resolved_envs* (i.e.
    the intersection of feature platforms per environment, falling
    back to ``config.platforms`` when no feature declares any).

    A naive ``config.platforms`` check is not sufficient because
    features may declare platforms beyond the workspace level, and
    those reach the solver through :attr:`ResolvedEnvironment.platforms`
    without being clipped against the workspace set.

    Intended for pre-solve CLI validation of ``--platform`` values
    (so typos like ``lixux-64`` fail before any solver work runs) and
    for surfacing the reachable platform set in
    ``conda workspace info``.  Passing an empty *resolved_envs*
    degrades to "workspace platforms only".
    """
    known: set[str] = set(config.platforms)
    for resolved in resolved_envs:
        known.update(resolved.platforms or ())
    return known
