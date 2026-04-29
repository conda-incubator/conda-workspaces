# Changelog

All notable changes to conda-workspaces will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

## 0.4.0 — 2026-04-29

### Added

- `conda workspace quickstart` bootstraps a workspace in one step,
  composing `init`, `add`, `install`, and `shell`. Run it in an empty
  directory to scaffold a manifest, add the specs passed on the
  command line (`conda workspace quickstart python=3.14 numpy`),
  install the environment, and drop into an activated shell.
- `conda workspace quickstart --copy` (alias `--clone`) copies an
  existing workspace's manifest from a directory or file instead of
  running `init`. `--no-shell` skips the final shell step (implied by
  `--json`).
- New manifest-format exporter plugins for `conda workspace export`:
  `conda-toml`, `pixi-toml`, and `pyproject-toml`. Registered via the
  same `conda_environment_exporters` hook as `environment-yaml` and
  `conda-workspaces-lock-v1`, so per-platform projection, `--file`
  inference, and `--output` streaming carry over. Together with
  `conda workspace import`, `conda workspace` now translates in both
  directions across every supported manifest dialect.
- The `pyproject-toml` exporter splices its content under
  `[tool.conda]` and preserves peer tables (`[project]`,
  `[build-system]`, `[tool.ruff]`, `[tool.pixi]`, ...) when the
  target file already exists.
- `conda workspace lock --output <path>` writes the lockfile to an
  arbitrary path (e.g. `conda.lock.linux-64`) so matrix CI runners
  can each emit a per-platform fragment.
- `conda workspace lock --merge <glob>` (repeatable) stitches
  lockfile fragments back into a single `conda.lock` without
  re-solving. Validates schema version and per-environment channel
  agreement, rejects overlapping `(environment, platform)` pairs
  (raising `LockfileMergeError`), and produces output byte-identical
  to a single-run `lock` over the same inputs. Mutually exclusive
  with `--environment`, `--platform`, `--skip-unsolvable`, and
  `--output`.
- `conda workspace lock` now writes a single `conda.lock` covering
  every platform declared by each environment, not just the host
  platform. Solves run with `context._subdir` overridden so conda's
  virtual package plugins (`__linux`, `__osx`, `__win`) and solver
  `subdirs` resolution target the correct subdir.
  `CONDA_OVERRIDE_*` and `[system-requirements]` continue to pin
  constraints like `__glibc`, `__cuda`, or `__osx`.
- `conda workspace lock --platform <subdir>` (repeatable) restricts
  the lock run to a subset of declared platforms. Unknown platforms
  raise `PlatformError` before any solve runs.
- `conda workspace lock --skip-unsolvable` keeps locking the
  remaining `(environment, platform)` pairs when one solve fails,
  printing a yellow `Skipping ...` line for each. Raises
  `AllTargetsUnsolvableError` if every pair fails, so CI never
  writes an empty lockfile. Non-solver errors still abort regardless.
- `--json` is now accepted across every `conda workspace` and
  `conda task` subcommand. Side-effect-only commands (`init`,
  `activate`, `run`, `shell`) used to crash with
  `unrecognized arguments: --json` when CI wrappers passed the flag
  globally; they now accept it silently and rely on the exit code.
  See the `--json contract` section in `AGENTS.md`.
- `conda workspace info --json` exposes the reachable set of
  platforms as `known_platforms` (and a `Known Platforms` row in
  text output when features broaden the workspace-level set), via
  the new `conda_workspaces.resolver.known_platforms()` helper.
- `SolveError` names the target platform when known, so
  per-platform failures stand out in CI logs.
- Inside `conda workspace shell`, `add` / `remove` / `install`
  print a hint to re-spawn the shell when a newly installed package
  drops activation scripts into `$PREFIX/etc/conda/activate.d/`.
- New `demos/multi-platform.{tape,gif,mp4}` recording for
  cross-platform locking and the `--platform` flag. The
  `demos/lockfile` recording was refreshed to show multi-platform
  default output.

### Changed

- `conda workspace add` and `conda workspace remove` now install into
  the affected environment(s) and refresh `conda.lock` by default,
  matching `pixi add` / `pixi remove`. Use `--no-install`,
  `--no-lockfile-update`, `--force-reinstall`, or `--dry-run` to opt
  out.
- `conda workspace install` shares a single solve/install/lock
  pipeline with `add` and `remove`
  (`conda_workspaces/cli/workspace/sync.py`).
- `conda_workspaces.parsers` renamed to `conda_workspaces.manifests`
  (named after the subject, not the verb). Class names like
  `CondaTomlParser` are unchanged; public re-exports preserved.
- `conda_workspaces.env_spec` shrunk to the `conda.toml` env-spec
  plugin (`CondaWorkspaceSpec`). `CondaLockSpec` was replaced by
  `conda_workspaces.lockfile.CondaLockLoader`.
- Plugin metadata moved to module-level `FORMAT` / `ALIASES` /
  `DEFAULT_FILENAMES` constants. The canonical lockfile `FORMAT` is
  now `conda-workspaces-lock-v1`; `conda-workspaces-lock` and
  `workspace-lock` remain as aliases. See
  `docs/reference/format-aliases.md`.
- `generate_lockfile` now builds
  `conda.models.environment.Environment` objects and delegates YAML
  serialisation to the same `multiplatform_export` hook as
  `conda export --format=conda-workspaces-lock-v1`. `conda workspace
  lock` and `conda export` now produce byte-identical output.
- `conda_workspaces.lockfile` owns both the write path and the
  `CondaEnvironmentSpecifier` plugin (`CondaLockLoader`), and
  delegates YAML→`Environment` conversion to
  `conda_lockfiles.rattler_lock.v6`. `conda.lock` is documented as a
  derivative of rattler-lock v6 (`pixi.lock`): same schema family,
  distinct filename and on-disk version byte.
- Bumped the optional `conda-spawn` dependency floor from `>=0.0.5` to
  `>=0.1.0` to pick up the new fish/csh/tcsh/xonsh shell support and
  the double-prompt / PowerShell `-NoExit` / `$CONDA_ROOT/condabin`
  fixes. The integration in `cli/workspace/shell.py` is unchanged
  (`conda_spawn.main.spawn` is still the entry point).
- `conda task run <task>` (and the `ct run` alias) now falls back to
  the workspace's `default` environment when the task doesn't declare
  a `default_environment`, instead of inheriting whichever conda env
  happened to be active at the call site. Pass `-e` explicitly to
  override.

### Fixed

- `conda workspace quickstart` crashed every invocation with
  `AttributeError: 'Namespace' object has no attribute 'verbose'`
  after conda renamed the namespace dest to `verbosity`.
- `conda workspace quickstart --json` no longer leaks Rich status
  lines from the sub-handlers (`init`, `add`, `install`) into stdout;
  the JSON payload is the only thing emitted on stdout, matching the
  documented `--json contract`.

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
