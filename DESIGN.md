# DESIGN.md — conda-workspaces Architecture & Pixi Compatibility

This document describes the design of conda-workspaces, its relationship
to pixi's workspace model, and the challenges involved in bridging the
two ecosystems.

## Overview

conda-workspaces is a conda plugin that brings project-scoped,
multi-environment workspace management to conda.  It reads pixi-compatible
manifest files (`pixi.toml`, `conda.toml`, `pyproject.toml`) and manages
project-local conda environments under `.conda/envs/`.

The plugin registers a `conda workspace` subcommand (with `cw` as a
standalone shortcut) that provides `init`, `install`, `lock`, `list`,
`info`, `add`, `remove`, `clean`, `run`, `shell`, and `activate` subcommands.

## Goals

1. **Pixi-compatible manifest format** — read the same `pixi.toml` and
   `[tool.pixi.*]` tables that pixi uses, so projects can share a single
   manifest between both tools.

2. **Project-scoped environments** — environments live in
   `.conda/envs/<name>/` within the project, not in the global
   `~/miniconda3/envs/`.

3. **Multi-environment support** — define multiple named environments
   from composable features, matching pixi's feature/environment model.

4. **Conda-native solver** — use conda's own solver (libmamba) rather
   than bundling a separate resolver.

5. **Plugin architecture** — integrate via conda's plugin system, adding
   no overhead to unrelated conda operations.

## Pixi Compatibility Mapping

### Fully Supported

| Pixi concept | conda-workspaces equivalent | Notes |
|---|---|---|
| `[workspace]` table | Parsed directly | `[project]` legacy also supported |
| `channels` | Mapped to conda channels | Identical semantics |
| `platforms` | Stored and validated | Used for platform filtering |
| `[dependencies]` | `CondaDependency` model | Spec syntax is identical |
| `[pypi-dependencies]` | `PyPIDependency` model | Requires conda-pypi for install |
| `[feature.<name>]` | `Feature` dataclass | Full feature composition |
| `[environments]` | `Environment` dataclass | Including `no-default-feature` |
| `[activation]` | `activation_scripts`, `activation_env` | Scripts copied to activate.d/, env vars set via PrefixData |
| `[system-requirements]` | Virtual package constraints | Added as `__glibc >=X`, `__cuda >=Y` specs during solving |
| `[target.<platform>]` | `target_conda_dependencies` | Per-platform overrides |
| `channel-priority` | Mapped to conda setting | `strict` / `flexible` / `disabled` |
| Inline tables `{version = "...", build = "..."}` | Parsed via tomlkit | Dict-form deps |

### Accepted but Ignored

| Pixi concept | Status |
|---|---|
| `solve-group` | Accepted in manifests for compatibility but has no effect. Conda's solver operates on one environment at a time and does not support cross-environment version coordination. |

### Not Supported (Pixi-Only Concepts)

| Pixi concept | Reason |
|---|---|
| `[package]` / pixi-build | Pixi's build system uses rattler-build with custom backends. conda uses conda-build or rattler-build directly. These are fundamentally different build orchestration systems. |
| `[host-dependencies]` / `[build-dependencies]` | Part of the `[package]` build model. Not applicable outside pixi-build. |
| `deno_task_shell` | Pixi tasks use a Deno-compatible shell. conda-tasks handles task execution separately; no overlap with workspace management. |
| `tool.pixi.project.conda-pypi-map` | Pixi's custom mapping for conda↔PyPI name translation. conda-pypi handles this differently. |

## Key Design Decisions

### 1. Environment Directory Layout

```
project/
├── pixi.toml              # or pyproject.toml
├── conda.lock             # lockfile (rattler-lock v6 format)
├── .conda/
│   └── envs/
│       ├── default/       # default environment
│       ├── test/          # named environment
│       └── docs/          # named environment
└── ...
```

**Rationale**: `.conda/envs/` mirrors conda's existing conventions while
keeping environments project-scoped.  Pixi uses `.pixi/envs/` — we
intentionally use a different path to avoid conflicts when both tools
are used on the same project.

### 2. Feature Composition Model

Features compose via ordered merging: later features override earlier
ones for the same package name.  The `default` feature is always
prepended unless `no-default-feature = true`.

This exactly matches pixi's composition semantics, ensuring that
a manifest written for pixi produces the same dependency set when
parsed by conda-workspaces.

### 3. Solver Strategy

conda-workspaces delegates all solving to conda's solver (libmamba).
For each environment:

1. Resolve all features into a merged dependency set
2. Feed specs + channels to `conda create` / `conda install`
3. Let libmamba handle constraint resolution

### 4. conda-native Format (conda.toml)

While pixi.toml is the primary format, a `conda.toml` is also
supported.  This is structurally identical to pixi.toml but:

- Uses `[workspace]` exclusively (no `[project]` fallback)
- May add conda-specific extensions in the future (e.g., custom solver
  settings, conda-build integration)
- Provides a non-pixi-branded option for teams that only use conda

### 5. Standalone CLI Shortcut (`cw`)

The `cw` console script provides a standalone entry point that doesn't
require going through `conda workspace ...`.  This is registered via
`[project.scripts]` in pyproject.toml:

```toml
[project.scripts]
cw = "conda_workspaces.__main__:main"
```

Similarly, conda-tasks provides `ct` as a shortcut for `conda task`.

## Differences from Pixi

### Architectural

1. **No bundled solver** — conda-workspaces uses conda's solver; pixi
   bundles rattler (a Rust-based solver).  This means solving behavior
   may differ slightly.

2. **No package installation** — conda-workspaces creates real conda
   environments using conda's install machinery.  Pixi uses rattler to
   install packages directly into `.pixi/envs/`, bypassing conda.

3. **Lock files** — conda-workspaces generates a `conda.lock` in
   rattler-lock v6 format (the same structure as `pixi.lock`) after
   every install.  The `--locked` flag installs from the lockfile
   without running the solver, and `cw lock` regenerates the lockfile
   on demand.

4. **Plugin, not standalone** — conda-workspaces is a conda plugin;
   pixi is a standalone tool that replaces conda entirely for its users.

### Behavioral

1. **Channel resolution** — conda and pixi may resolve channel URLs
   differently (e.g., conda's `defaults` channel vs pixi's strict
   conda-forge orientation).

2. **Virtual packages** — conda's virtual package system
   (`__cuda`, `__glibc`, etc.) may produce different solve results
   than pixi's system-requirements handling.

3. **Environment activation** — conda environments are activated via
   `conda activate`; pixi uses `pixi shell` or `pixi run`.
   conda-workspaces environments are standard conda prefixes and work
   with `conda activate <prefix>`.

### Practical Challenges

1. **PyPI dependency installation** depends on conda-pypi being
   installed.  Without it, PyPI deps are parsed but cannot be installed.

2. **Platform parity** — pixi supports platforms that conda may not
   have complete channel coverage for (e.g., `linux-aarch64` has
   fewer packages on some channels).

3. **Manifest drift** — as pixi evolves its manifest format,
   conda-workspaces must track changes to remain compatible.  Pixi's
   format is not formally standardized outside the pixi project.

## Plugin Hook Integrations

conda-workspaces registers three conda plugin hooks so that workspace
manifests and lockfiles are first-class citizens in the wider conda
ecosystem:

### Environment Specifiers (`conda_environment_specifiers`)

Two environment specifiers are registered:

**`conda-workspaces`** — handles `conda.toml` workspace manifests.
When a user runs `conda env create --file conda.toml`, the specifier
parses the manifest, resolves the *default* environment's dependencies,
and returns them as `requested_packages` (a list of `MatchSpec`
objects).  conda's solver then resolves the full dependency tree.

**`conda-workspaces-lock`** — handles `conda.lock` files (rattler-lock
v6 format).  When a user runs `conda env create --file conda.lock`,
the specifier parses the lockfile, extracts the exact package URLs for
the default environment on the current platform, and returns them as
`explicit_packages` (a list of `PackageRecord` objects).  The solver
is bypassed entirely, producing a bit-for-bit reproducible install.

Both specifiers set `detection_supported = True`, so conda can
auto-detect the format from the filename without requiring
`--env-spec=...`.

### Environment Exporter (`conda_environment_exporters`)

One environment exporter is registered:

**`conda-workspaces-lock`** (aliases: `workspace-lock`) — exports
installed environments to `conda.lock` in the rattler-lock v6 format.
This allows `conda export --format=conda-workspaces-lock --file=conda.lock`
to produce a lockfile from any conda environment, not just workspace-
managed ones.

The exporter implements the `multiplatform_export` interface, accepting
an iterable of `Environment` objects (one per platform) and producing
a single YAML document.  It uses the same serialisation helpers as the
built-in `conda workspace lock` command.

### Relationship with conda-lockfiles

The `conda-lockfiles` plugin handles `pixi.lock` files under the
`rattler-lock-v6` format name.  conda-workspaces extends coverage to
`conda.lock` files under the `conda-workspaces-lock` format name.  The
two plugins do not conflict — and neither collides with `conda-lock-v1`
(the format name `conda-lockfiles` uses for `conda-lock.yml` files
from the `conda/conda-lock` tool):

| Plugin | Env-spec name | Filenames | Exporter name |
|---|---|---|---|
| conda-lockfiles | `conda-lock-v1` | `conda-lock.yml` | `conda-lock-v1` |
| conda-lockfiles | `rattler-lock-v6` | `pixi.lock` | `rattler-lock-v6` |
| conda-workspaces | `conda-workspaces-lock` | `conda.lock` | `conda-workspaces-lock` |

Both use the same rattler-lock v6 YAML structure, so a `conda.lock`
can be renamed to `pixi.lock` (and vice versa) and handled by either
plugin.

### conda-pypi Integration

PyPI dependencies are handled in two phases:

1. **Standard PyPI deps** (version-spec only) are translated to conda
   package names via `conda-pypi`'s `pypi_to_conda_name` (using the
   grayskull mapping) and merged directly into the solver call alongside
   conda specs. The rattler solver + conda-pypi's wheel extractor
   resolve and install everything in a single pass.

2. **Path-based PyPI deps** (`path = ".", editable = true`) are built
   into `.conda` packages via `conda-pypi`'s `pypa_to_conda` after the
   main solve completes, then installed with `install_ephemeral_conda`.

Git and URL PyPI dependencies are not yet supported and are skipped
with a warning. If `conda-pypi` is not installed, all PyPI dependencies
are skipped with a warning. This keeps conda-pypi as an optional
dependency — workspaces that only use conda packages never need it.

The environment specifiers also surface PyPI dependencies as
`external_packages` (under the `"pip"` key) so that conda's own
reporting and downstream tools can see them.

## Future Work

- **conda-build integration**: Build packages from workspace members
  using conda-build recipes.
- **Multi-package workspaces**: Support monorepo layouts where
  subdirectories are independent packages that can depend on each other
  (pixi's `[package]` concept, reimagined for conda-build).
- **Hardlink optimization**: Use hardlinks from the package cache to
  reduce disk usage for project-local environments.
- **Multi-platform lockfiles**: Record packages for all declared
  platforms in `conda.lock`, not only the current host platform.
  Requires cross-platform solving or collecting data from CI runners
  on each platform.
