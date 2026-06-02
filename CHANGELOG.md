# Changelog

All notable changes to conda-workspaces will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

## 0.5.0 — 2026-06-02

### Added

- User-level task definitions: tasks defined in `~/.conda/tasks.toml`
  are now available across all projects without repeating them in every
  manifest. Project tasks override user tasks on name collision.
  `conda task list` annotates user-sourced tasks with `(user)` in text
  output and `"source": "user"` in JSON output. XDG paths are also
  supported (`$XDG_CONFIG_HOME/conda/tasks.toml`).
  (<gh-issue:53>, <gh-pr:54>)
- `conda workspace archive` packages a workspace into a portable
  archive. Git repositories include tracked files by default, while
  non-git projects include workspace files filtered by built-in
  exclusions, `[workspace.archive]` settings, and `--exclude`.
  `.tar.zst` is the default output format; `.tar.gz` and `.tar.bz2`
  are also supported. (<gh-issue:57>, <gh-pr:63>)
- `conda workspace archive --lock` refreshes `conda.lock` before
  writing the archive, and `--bundle` includes package archives from
  the local conda package cache for offline or air-gapped installs.
  Bundled package archives are checked against lockfile SHA-256 hashes
  when hashes are available. (<gh-issue:57>, <gh-pr:63>, <gh-pr:71>)
- `conda workspace unarchive` restores an archived workspace, and
  `conda workspace unarchive --install` extracts the workspace and
  installs its environments from the bundled or local package cache in
  one step. (<gh-issue:57>, <gh-pr:63>)
- `conda workspace install` now checks whether `conda.lock` satisfies
  the manifest before deciding whether to solve. In local use it
  prefers the lockfile when it is still valid and solves when it is
  not. In CI (`CI=true`) the default behaves like a locked install and
  fails fast when the lockfile is missing or unsatisfiable.
  (<gh-issue:61>, <gh-pr:67>)
- `conda workspace install --no-lock` forces a solve even when the
  existing lockfile satisfies the manifest. (<gh-issue:61>, <gh-pr:67>)
- `conda workspace info` now reports lockfile status
  (`up-to-date`, `out-of-date`, or `missing`) in text output and as
  `lockfile_status` in JSON output. (<gh-issue:61>, <gh-pr:67>)
- `conda ws` is now a short alias for `conda workspace`, with the same
  subcommands, flags, arguments, and help text. (<gh-issue:68>, <gh-pr:69>)
- Added tutorials and reference material for workspace archives, PyPI
  dependencies, multi-platform locking, and the `conda.toml`
  specification. (<gh-pr:51>, <gh-pr:56>, <gh-pr:65>, <gh-pr:66>)

### Changed

- The minimum supported conda dependency is now `conda >=26.3`, and
  the minimum `conda-pypi` dependency is now `conda-pypi >=0.9.0`.
  conda-workspaces also uses the current conda environment specifier
  and exporter plugin metadata APIs. (<gh-pr:65>)
- Projects that declare PyPI dependencies now receive a clearer
  runtime warning when `conda-rattler-solver` is not installed.
  `conda-rattler-solver` remains an explicit dependency in the pixi
  development environments. (<gh-pr:65>)

### Fixed

- `.tar.zst` archive creation and extraction now work on every
  supported Python version. Python 3.10 through 3.13 use
  `backports.zstd`; Python 3.14+ uses the standard library zstd
  support. (<gh-pr:72>)
- Workspace archive file collection uses POSIX archive paths on
  Windows, so archives are portable across platforms.
  (<gh-issue:57>, <gh-pr:63>)
- Lockfile loading now raises the project-specific platform mismatch
  error for missing-platform cases instead of a generic `ValueError`.
  (<gh-pr:65>)

### Security

- Task templates now render with Jinja2's sandboxed environment, which
  blocks template attribute traversal attacks from malicious task
  definitions. Task argument names can no longer shadow the reserved
  `conda` and `pixi` template context keys. (<gh-pr:55>)
- Archive extraction validates every tar member before extraction,
  rejecting absolute paths, `..` path traversal, symlink escapes, and
  special file types such as device nodes and FIFOs. On Python 3.12+
  extraction also uses the standard library `filter="data"` defense.
  (<gh-issue:57>, <gh-pr:63>)
- Bundled archive package cache priming verifies package SHA-256
  hashes against `conda.lock` before copying archives into the local
  conda package cache. (<gh-issue:57>, <gh-pr:63>, <gh-pr:71>)

## 0.4.0 — 2026-04-29

### Added

- `conda workspace quickstart` bootstraps a workspace in one step,
  composing `init`, `add`, `install`, and `shell`. Run it in an empty
  directory to scaffold a manifest, add the specs passed on the
  command line (`conda workspace quickstart python=3.14 numpy`),
  install the environment, and drop into an activated shell.
  (<gh-issue:22>, <gh-pr:39>)
- `conda workspace quickstart --copy` (alias `--clone`) copies an
  existing workspace's manifest from a directory or file instead of
  running `init`. `--no-shell` skips the final shell step (implied by
  `--json`). (<gh-issue:22>, <gh-pr:39>)
- New manifest-format exporter plugins for `conda workspace export`:
  `conda-toml`, `pixi-toml`, and `pyproject-toml`. Registered via the
  same `conda_environment_exporters` hook as `environment-yaml` and
  `conda-workspaces-lock-v1`, so per-platform projection, `--file`
  inference, and `--output` streaming carry over. Together with
  `conda workspace import`, `conda workspace` now translates in both
  directions across every supported manifest dialect.
  (<gh-issue:14>, <gh-issue:41>, <gh-pr:37>, <gh-pr:44>)
- The `pyproject-toml` exporter splices its content under
  `[tool.conda]` and preserves peer tables (`[project]`,
  `[build-system]`, `[tool.ruff]`, `[tool.pixi]`, ...) when the
  target file already exists. (<gh-issue:41>, <gh-pr:44>)
- `conda workspace lock --output <path>` writes the lockfile to an
  arbitrary path (e.g. `conda.lock.linux-64`) so matrix CI runners
  can each emit a per-platform fragment. (<gh-issue:34>, <gh-pr:38>)
- `conda workspace lock --merge <glob>` (repeatable) stitches
  lockfile fragments back into a single `conda.lock` without
  re-solving. Validates schema version and per-environment channel
  agreement, rejects overlapping `(environment, platform)` pairs
  (raising `LockfileMergeError`), and produces output byte-identical
  to a single-run `lock` over the same inputs. Mutually exclusive
  with `--environment`, `--platform`, `--skip-unsolvable`, and
  `--output`. (<gh-issue:34>, <gh-pr:38>)
- `conda workspace lock` now writes a single `conda.lock` covering
  every platform declared by each environment, not just the host
  platform. Solves run with `context._subdir` overridden so conda's
  virtual package plugins (`__linux`, `__osx`, `__win`) and solver
  `subdirs` resolution target the correct subdir.
  `CONDA_OVERRIDE_*` and `[system-requirements]` continue to pin
  constraints like `__glibc`, `__cuda`, or `__osx`.
  (<gh-issue:4>, <gh-pr:31>)
- `conda workspace lock --platform <subdir>` (repeatable) restricts
  the lock run to a subset of declared platforms. Unknown platforms
  raise `PlatformError` before any solve runs. (<gh-issue:4>, <gh-pr:31>)
- `conda workspace lock --skip-unsolvable` keeps locking the
  remaining `(environment, platform)` pairs when one solve fails,
  printing a yellow `Skipping ...` line for each. Raises
  `AllTargetsUnsolvableError` if every pair fails, so CI never
  writes an empty lockfile. Non-solver errors still abort regardless.
  (<gh-issue:33>, <gh-pr:31>)
- `--json` is now accepted across every `conda workspace` and
  `conda task` subcommand. Side-effect-only commands (`init`,
  `activate`, `run`, `shell`) used to crash with
  `unrecognized arguments: --json` when CI wrappers passed the flag
  globally; they now accept it silently and rely on the exit code.
  See the `--json contract` section in `AGENTS.md`. (<gh-pr:46>)
- `conda workspace info --json` exposes the reachable set of
  platforms as `known_platforms` (and a `Known Platforms` row in
  text output when features broaden the workspace-level set), via
  the new `conda_workspaces.resolver.known_platforms()` helper.
  (<gh-issue:4>, <gh-pr:31>)
- `SolveError` names the target platform when known, so
  per-platform failures stand out in CI logs. (<gh-issue:4>, <gh-pr:31>)
- Inside `conda workspace shell`, `add` / `remove` / `install`
  print a hint to re-spawn the shell when a newly installed package
  drops activation scripts into `$PREFIX/etc/conda/activate.d/`.
  (<gh-issue:21>, <gh-pr:28>)
- New `demos/multi-platform.{tape,gif,mp4}` recording for
  cross-platform locking and the `--platform` flag. The
  `demos/lockfile` recording was refreshed to show multi-platform
  default output. (<gh-issue:4>, <gh-pr:31>, <gh-pr:45>)

### Changed

- `conda workspace add` and `conda workspace remove` now install into
  the affected environment(s) and refresh `conda.lock` by default,
  matching `pixi add` / `pixi remove`. Use `--no-install`,
  `--no-lockfile-update`, `--force-reinstall`, or `--dry-run` to opt
  out. (<gh-issue:21>, <gh-pr:28>)
- `conda workspace install` shares a single solve/install/lock
  pipeline with `add` and `remove`
  (`conda_workspaces/cli/workspace/sync.py`). (<gh-pr:28>)
- `conda_workspaces.parsers` renamed to `conda_workspaces.manifests`
  (named after the subject, not the verb). Class names like
  `CondaTomlParser` are unchanged; public re-exports preserved.
  (<gh-pr:29>)
- `conda_workspaces.env_spec` shrunk to the `conda.toml` env-spec
  plugin (`CondaWorkspaceSpec`). `CondaLockSpec` was replaced by
  `conda_workspaces.lockfile.CondaLockLoader`. (<gh-issue:4>, <gh-pr:29>)
- Plugin metadata moved to module-level `FORMAT` / `ALIASES` /
  `DEFAULT_FILENAMES` constants. The canonical lockfile `FORMAT` is
  now `conda-workspaces-lock-v1`; `conda-workspaces-lock` and
  `workspace-lock` remain as aliases. See
  `docs/reference/format-aliases.md`. (<gh-pr:29>, <gh-pr:35>)
- `generate_lockfile` now builds
  `conda.models.environment.Environment` objects and delegates YAML
  serialisation to the same `multiplatform_export` hook as
  `conda export --format=conda-workspaces-lock-v1`. `conda workspace
  lock` and `conda export` now produce byte-identical output.
  (<gh-pr:35>)
- `conda_workspaces.lockfile` owns both the write path and the
  `CondaEnvironmentSpecifier` plugin (`CondaLockLoader`), and
  delegates YAML→`Environment` conversion to
  `conda_lockfiles.rattler_lock.v6`. `conda.lock` is documented as a
  derivative of rattler-lock v6 (`pixi.lock`): same schema family,
  distinct filename and on-disk version byte. (<gh-issue:4>, <gh-pr:29>)
- Bumped the optional `conda-spawn` dependency floor from `>=0.0.5` to
  `>=0.1.0` to pick up the new fish/csh/tcsh/xonsh shell support and
  the double-prompt / PowerShell `-NoExit` / `$CONDA_ROOT/condabin`
  fixes. The integration in `cli/workspace/shell.py` is unchanged
  (`conda_spawn.main.spawn` is still the entry point). (<gh-pr:49>)
- `conda task run <task>` (and the `ct run` alias) now falls back to
  the workspace's `default` environment when the task doesn't declare
  a `default_environment`, instead of inheriting whichever conda env
  happened to be active at the call site. Pass `-e` explicitly to
  override. (<gh-issue:26>, <gh-pr:27>)

### Fixed

- `conda workspace quickstart` crashed every invocation with
  `AttributeError: 'Namespace' object has no attribute 'verbose'`
  after conda renamed the namespace dest to `verbosity`. (<gh-pr:46>)
- `conda workspace quickstart --json` no longer leaks Rich status
  lines from the sub-handlers (`init`, `add`, `install`) into stdout;
  the JSON payload is the only thing emitted on stdout, matching the
  documented `--json contract`. (<gh-pr:46>)

## 0.3.0 — 2026-03-31

### Added

- `conda workspace import` command to convert `environment.yml`,
  `anaconda-project.yml`, `conda-project.yml`, `pixi.toml`, and
  `pyproject.toml` manifests to `conda.toml` (<gh-issue:12>, <gh-pr:13>)
- Progress output during import (reading, format detection, write status)
  (<gh-pr:13>)
- Syntax-highlighted TOML preview in `--dry-run` mode (<gh-pr:13>)
- `conda task add` and `conda task remove` support for `pixi.toml` and
  `pyproject.toml` manifests (<gh-issue:8>, <gh-pr:9>, <gh-pr:10>)
- Codecov integration and coverage badge (<gh-pr:15>)
- CI, docs, PyPI, and conda-forge badges to README (<gh-pr:15>)
- Documentation for `CONDA_PKGS_DIRS` hardlink optimization in CI/Docker
  (<gh-issue:5>, <gh-pr:10>)
- Diataxis-organized documentation sidebar (<gh-pr:11>)

### Changed

- Import format detection uses human-readable labels instead of class names
  (<gh-pr:13>)
- Importers use `packaging.Requirement` for robust pip dependency parsing
  (<gh-pr:13>)
- Simplified importer registry to a single `find_importer` function
  (<gh-pr:13>)
- Unified installation docs (conda install and pixi global install)
  (<gh-pr:15>)

### Fixed

- `--dry-run` output no longer strips TOML section headers in non-terminal
  environments (<gh-pr:13>)
- Trailing dot suppressed in import status when output is in the current
  directory (<gh-pr:13>)

## 0.2.0 — 2026-03-30

### Added

- `conda task` subcommand with `run`, `list`, `add`, `remove`, and `export`
  (<gh-pr:1>)
- `conda workspace run` command for one-shot execution in environments
  (<gh-pr:1>)
- Task dependencies with topological ordering (`depends-on`) (<gh-pr:1>)
- Jinja2 template support in task commands (`{{ conda.platform }}`,
  conditionals) (<gh-pr:1>)
- Task output caching with input/output file declarations (<gh-pr:1>)
- Per-platform task overrides via `[target.<platform>.tasks]` (<gh-pr:1>)
- Task arguments with default values (<gh-pr:1>)
- Rich terminal output for all CLI commands (tables, status, errors)
  (<gh-pr:1>)
- Structured error rendering with actionable hints (<gh-pr:1>)
- Integration tests for CLI workflows (<gh-pr:1>)
- Demo recordings for terminal screencasts (<gh-pr:1>)

### Changed

- Verb-based status messages (Installing, Installed, etc.) replace
  symbol-based markers (<gh-pr:1>)
- All CLI output routed through Rich console for consistent formatting
  (<gh-pr:1>)
- Documentation standardized to use `conda workspace` / `conda task`
  as primary CLI forms (`cw` / `ct` noted as aliases) (<gh-pr:1>)
- Aligned parsers with pixi workspace semantics for broader manifest
  compatibility (<gh-pr:1>)
- Exception hierarchy expanded with type annotations and actionable hints
  (<gh-pr:1>)

### Fixed

- JSON output in `conda task list --json` no longer includes ANSI escapes
  (<gh-pr:1>)
- Activation script handling on Windows uses correct path validation
  (<gh-pr:1>)
- Solver output noise suppressed during lockfile generation (<gh-pr:1>)
- Stdout flushed after conda solver and transaction API calls (<gh-pr:1>)

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
