# Changelog

All notable changes to conda-workspaces will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added

- `conda workspace lock` now writes a single `conda.lock` that covers
  every platform declared by each environment, not just the host
  platform. Target-platform solves run with `context._subdir`
  overridden for the duration of the solve so conda's virtual package
  plugins (`__linux`, `__osx`, `__win`) and the solver's `subdirs`
  resolution both target the correct subdir. `CONDA_OVERRIDE_*` and
  the manifest `[system-requirements]` table continue to work as
  before, so downstream constraints like `__glibc`, `__cuda`, or
  `__osx` can be pinned explicitly when solving for platforms the
  host machine would not otherwise advertise.
- `conda workspace lock --platform <subdir>` restricts the lock run
  to a subset of declared platforms. The flag is repeatable
  (`--platform linux-64 --platform osx-arm64`). Unknown platforms
  raise `PlatformError` before any solve runs.
- `SolveError` now names the target platform when it is known, so
  per-platform failures are easy to spot in CI logs.
- `conda workspace lock --skip-unsolvable` keeps locking the remaining
  `(environment, platform)` pairs when an individual solve fails,
  emitting a yellow `Skipping ...` line for each skipped pair. The
  command still raises `AllTargetsUnsolvableError` with an aggregated
  summary if *every* pair fails, so CI never writes an empty lockfile.
  Non-solver errors (missing channel, invalid manifest, etc.) continue
  to abort regardless of the flag.

### Changed

- Internal refactor of the `conda.lock` write path: `generate_lockfile`
  now builds `conda.models.environment.Environment` objects and
  delegates YAML serialisation to the same `multiplatform_export` hook
  used by `conda export --format=conda-workspaces-lock-v1`, removing
  the previously duplicated `_build_lockfile_dict` helper.  `conda
  workspace lock` and `conda export` now produce byte-identical
  output.
- Internal refactor of the `conda.lock` read path: ``conda_workspaces.lockfile``
  now owns both the write path and the `CondaEnvironmentSpecifier`
  plugin (`CondaLockLoader`), and delegates YAML -> `Environment`
  conversion to `conda_lockfiles.rattler_lock.v6` instead of
  re-implementing it.  `conda.lock` is now documented as a derivative
  of rattler-lock v6 (`pixi.lock`), same schema family, distinct
  filename and on-disk version byte.
- `conda_workspaces.parsers` renamed to `conda_workspaces.manifests`
  (the directory is named after its subject, not the verb; class
  names `CondaTomlParser` etc. are unchanged).  Public re-exports are
  preserved via relative imports within the package.
- `conda_workspaces.env_spec` shrunk to the `conda.toml` env-spec
  plugin only (`CondaWorkspaceSpec`).  `CondaLockSpec` has been
  replaced by `conda_workspaces.lockfile.CondaLockLoader`.
- Plugin metadata is now exposed as module-level `FORMAT` / `ALIASES`
  / `DEFAULT_FILENAMES` constants per plugin module.  The canonical
  lockfile `FORMAT` changed from `conda-workspaces-lock` to
  `conda-workspaces-lock-v1`; the old name is registered as an alias
  alongside `workspace-lock`, so existing `conda export
  --format=conda-workspaces-lock` invocations keep working.  See
  `docs/reference/format-aliases.md` for the naming policy.

- `conda workspace add` and `conda workspace remove` now install into
  the affected environment(s) and refresh `conda.lock` by default,
  matching `pixi add` / `pixi remove`. Use `--no-install` to update
  the manifest and lockfile without touching the prefix,
  `--no-lockfile-update` to keep the old manifest-only behaviour,
  `--force-reinstall` to recreate affected environments from scratch,
  or `--dry-run` to solve without writing anything to disk.
- `conda workspace install` now shares a single solve/install/lock
  pipeline with `add` and `remove` (`conda_workspaces/cli/workspace/sync.py`).

### Added

- Inside a `conda workspace shell` session, `add` / `remove` / `install`
  print a hint to re-spawn the shell when a newly installed package
  drops activation scripts into `$PREFIX/etc/conda/activate.d/`.

## 0.3.0 — 2026-03-31

### Added

- `conda workspace import` command to convert `environment.yml`,
  `anaconda-project.yml`, `conda-project.yml`, `pixi.toml`, and
  `pyproject.toml` manifests to `conda.toml`
- Progress output during import (reading, format detection, write status)
- Syntax-highlighted TOML preview in `--dry-run` mode
- `conda task add` and `conda task remove` support for `pixi.toml` and
  `pyproject.toml` manifests
- Codecov integration and coverage badge
- CI, docs, PyPI, and conda-forge badges to README
- Documentation for `CONDA_PKGS_DIRS` hardlink optimization in CI/Docker
- Diataxis-organized documentation sidebar

### Changed

- Import format detection uses human-readable labels instead of class names
- Importers use `packaging.Requirement` for robust pip dependency parsing
- Simplified importer registry to a single `find_importer` function
- Unified installation docs (conda install and pixi global install)

### Fixed

- `--dry-run` output no longer strips TOML section headers in non-terminal
  environments
- Trailing dot suppressed in import status when output is in the current
  directory

## 0.2.0 — 2026-03-30

### Added

- `conda task` subcommand with `run`, `list`, `add`, `remove`, and `export`
- `conda workspace run` command for one-shot execution in environments
- Task dependencies with topological ordering (`depends-on`)
- Jinja2 template support in task commands (`{{ conda.platform }}`, conditionals)
- Task output caching with input/output file declarations
- Per-platform task overrides via `[target.<platform>.tasks]`
- Task arguments with default values
- Rich terminal output for all CLI commands (tables, status, errors)
- Structured error rendering with actionable hints
- Integration tests for CLI workflows
- Demo recordings for terminal screencasts

### Changed

- Verb-based status messages (Installing, Installed, etc.) replace
  symbol-based markers
- All CLI output routed through Rich console for consistent formatting
- Documentation standardized to use `conda workspace` / `conda task`
  as primary CLI forms (`cw` / `ct` noted as aliases)
- Aligned parsers with pixi workspace semantics for broader manifest
  compatibility
- Exception hierarchy expanded with type annotations and actionable hints

### Fixed

- JSON output in `conda task list --json` no longer includes ANSI escapes
- Activation script handling on Windows uses correct path validation
- Solver output noise suppressed during lockfile generation
- Stdout flushed after conda solver and transaction API calls

## 0.1.1 — 2026-03-05

### Changed

- Transferred repository to conda-incubator organization
- Added PyPI release workflow with trusted publishing
- Moved changelog to repository root

## 0.1.0 — 2026-03-05

### Added

- Initial implementation of conda-workspaces plugin
- `conda workspace` subcommand with `init`, `install`, `list`, `info`,
  `add`, `remove`, `clean`, `run`, and `activate` subcommands
- `conda workspace` standalone CLI (also available as `cw`)
- Parser support for `pixi.toml`, `conda.toml`, and `pyproject.toml`
  manifests
- Multi-environment workspace model with composable features
- Solve-group support for version coordination across environments
- Per-platform dependency overrides via `[target.<platform>]`
- PyPI dependency parsing (requires conda-pypi for installation)
- Project-local environments under `.conda/envs/`
- Sphinx documentation with conda-sphinx-theme
- PyPI release workflow with trusted publishing
