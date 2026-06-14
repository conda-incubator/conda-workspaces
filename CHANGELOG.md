# Changelog

All notable changes to conda-workspaces will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

## 0.7.0 — 2026-06-14

### Added

- Added `conda_workspaces.archive.WorkspaceArchive`, a public Python
  API for creating, inspecting, verifying, extracting, and installing
  workspace archives without importing CLI handlers. (#107)
- Pixi-style rich entries in `workspace.platforms` are now supported,
  including per-platform virtual package requirements such as `libc`,
  `macos`, and `windows`, and those entries are preserved when
  importing Pixi and `pyproject.toml` manifests. (#87,
  #90)
- `conda workspace archive --receipt [PATH]` writes an external
  in-toto Statement JSON receipt for a workspace archive, binding the
  archive, workspace manifest, `conda.lock`, and per-environment
  package inventory from the lockfile. `conda workspace unarchive
  --receipt [PATH]` verifies that receipt during extraction. The
  receipt schema is published as
  `workspace-archive-receipt-1.schema.json`. (#83,
  #84)
- Added archive receipt documentation, including a dedicated reference
  page, archive tutorial coverage, configuration guidance, README
  coverage, and a receipt demo. (#92)

### Changed

- Documentation publishing now runs for changelog-only changes, keeping
  published release notes in sync with repository updates. (#82)
- CI coverage uploads now use `codecov/codecov-action` 7.0.0.
  (#85)

### Security

- Receipt-verified archive extraction now checks the archive digest
  before extraction, stages extraction into an empty temporary target,
  verifies the extracted manifest, lockfile, and package inventory, and
  only then moves the verified workspace into place. `--require-sha256`
  can require every receipt package record to carry a SHA-256 digest.
  (#84)
- Installing from `conda.lock` now binds package references to declared
  channel URLs and exact package metadata, passes digest-bearing
  explicit specs to conda, and marks off-channel or unhashed lockfile
  refs out of date. (#93)
- Workspace environment names are now rejected when they could resolve
  outside the project-local environment prefix. (#95)
- Archive output and receipt paths now share portable validation, and
  manifest-controlled workspace names can no longer make the default
  archive path escape the workspace root. (#96)
- `conda workspace import --from conda-project` now rejects external,
  parent-traversing, absolute, drive-prefixed, or symlink-escaping
  `env_spec` file references before reading them. (#97)
- `conda workspace unarchive` now rejects targets that already point to
  a file, symlink, or non-empty directory. (#98)
- Non-git workspace archives now exclude common credential material by
  default while keeping documented template files eligible unless users
  exclude them explicitly. (#99)
- Bundled package cache priming during unarchive now requires a
  verified archive receipt before trusting bundled package metadata.
  (#100)
- Merged multi-platform lockfiles now validate conda package references
  before writing, reject conflicting metadata for the same URL, and
  require complete metadata under declared channels. (#104)

### Fixed

- Pixi-compatible rich platform entries in `[workspace].platforms` now
  preserve named platforms such as `linux-64-cuda`, generate Pixi-style
  names for unnamed rich entries, and write lockfile package sections
  under the declared platform name while solving against the backing
  conda subdir. (#106)
- `conda workspace lock`, `sync`, and archive locking now resolve
  workspace environments per target platform, so
  `[target.<platform>.dependencies]` only applies to that platform's
  solve. (#86, #89)
- Multi-platform lockfile merges now preserve manifest-declared
  environment channels as canonical, allowing platform fragments to
  omit unused channels while preserving manifest order. (#88,
  #91)
- `conda workspace archive --receipt` now fails before writing an
  archive when archive filters would omit the workspace manifest or
  `conda.lock`, avoiding archive/receipt pairs that cannot verify.
  (#84)
- Archive receipts now deduplicate identical `noarch` package records
  that appear under multiple target platforms in `conda.lock`.
  (#92)
- List-form task commands now preserve argument boundaries instead of
  being converted into shell strings, templated task arguments are
  quoted for string commands, and cache/display keys are stable across
  string and list command forms. (#101)
- Imported anaconda-project task data fields are now quoted before
  generating task commands, while explicit platform command fields are
  preserved as authored. (#102)
- Task output caching now compares file digests when available before
  reusing outputs, falling back to mtime and size for older cache
  entries. (#103)

## 0.6.0 — 2026-06-05

### Added

- `conda workspace unarchive --install` can install a selected
  environment to an explicit final prefix with `-e/--environment` and
  `--prefix`. `--dest` stages files below a filesystem root while
  preserving the requested runtime prefix inside the installed
  environment, and warns if installed files still reference the
  staging prefix. (#77, #78)
- Added `[workspace.dependencies]` inheritance for conda, pixi, and
  pyproject manifests, matching the workspace dependency feature added
  in pixi 0.70.0. (#80, #79)

## 0.5.0 — 2026-06-02

### Added

- User-level task definitions: tasks defined in `~/.conda/tasks.toml`
  are now available across all projects without repeating them in every
  manifest. Project tasks override user tasks on name collision.
  `conda task list` annotates user-sourced tasks with `(user)` in text
  output and `"source": "user"` in JSON output. XDG paths are also
  supported (`$XDG_CONFIG_HOME/conda/tasks.toml`).
  (#53, #54)
- `conda workspace archive` packages a workspace into a portable
  archive. Git repositories include tracked files by default, while
  non-git projects include workspace files filtered by built-in
  exclusions, `[workspace.archive]` settings, and `--exclude`.
  `.tar.zst` is the default output format; `.tar.gz` and `.tar.bz2`
  are also supported. (#57, #63)
- `conda workspace archive --lock` refreshes `conda.lock` before
  writing the archive, and `--bundle` includes package archives from
  the local conda package cache for offline or air-gapped installs.
  Bundled package archives are checked against lockfile SHA-256 hashes
  when hashes are available. (#57, #63, #71)
- `conda workspace unarchive` restores an archived workspace, and
  `conda workspace unarchive --install` extracts the workspace and
  installs its environments from the bundled or local package cache in
  one step. (#57, #63)
- `conda workspace install` now checks whether `conda.lock` satisfies
  the manifest before deciding whether to solve. In local use it
  prefers the lockfile when it is still valid and solves when it is
  not. In CI (`CI=true`) the default behaves like a locked install and
  fails fast when the lockfile is missing or unsatisfiable.
  (#61, #67)
- `conda workspace install --no-lock` forces a solve even when the
  existing lockfile satisfies the manifest. (#61, #67)
- `conda workspace info` now reports lockfile status
  (`up-to-date`, `out-of-date`, or `missing`) in text output and as
  `lockfile_status` in JSON output. (#61, #67)
- `conda ws` is now a short alias for `conda workspace`, with the same
  subcommands, flags, arguments, and help text. (#68, #69)
- Added tutorials and reference material for workspace archives, PyPI
  dependencies, multi-platform locking, and the `conda.toml`
  specification. (#51, #56, #65, #66)

### Changed

- The minimum supported conda dependency is now `conda >=26.3`, and
  the minimum `conda-pypi` dependency is now `conda-pypi >=0.9.0`.
  conda-workspaces also uses the current conda environment specifier
  and exporter plugin metadata APIs. (#65)
- Projects that declare PyPI dependencies now receive a clearer
  runtime warning when `conda-rattler-solver` is not installed.
  `conda-rattler-solver` remains an explicit dependency in the pixi
  development environments. (#65)

### Fixed

- `.tar.zst` archive creation and extraction now work on every
  supported Python version. Python 3.10 through 3.13 use
  `backports.zstd`; Python 3.14+ uses the standard library zstd
  support. (#72)
- Workspace archive file collection uses POSIX archive paths on
  Windows, so archives are portable across platforms.
  (#57, #63)
- Lockfile loading now raises the project-specific platform mismatch
  error for missing-platform cases instead of a generic `ValueError`.
  (#65)

### Security

- Task templates now render with Jinja2's sandboxed environment, which
  blocks template attribute traversal attacks from malicious task
  definitions. Task argument names can no longer shadow the reserved
  `conda` and `pixi` template context keys. (#55)
- Archive extraction validates every tar member before extraction,
  rejecting absolute paths, `..` path traversal, symlink escapes, and
  special file types such as device nodes and FIFOs. On Python 3.12+
  extraction also uses the standard library `filter="data"` defense.
  (#57, #63)
- Bundled archive package cache priming verifies package SHA-256
  hashes against `conda.lock` before copying archives into the local
  conda package cache. (#57, #63, #71)

## 0.4.0 — 2026-04-29

### Added

- `conda workspace quickstart` bootstraps a workspace in one step,
  composing `init`, `add`, `install`, and `shell`. Run it in an empty
  directory to scaffold a manifest, add the specs passed on the
  command line (`conda workspace quickstart python=3.14 numpy`),
  install the environment, and drop into an activated shell.
  (#22, #39)
- `conda workspace quickstart --copy` (alias `--clone`) copies an
  existing workspace's manifest from a directory or file instead of
  running `init`. `--no-shell` skips the final shell step (implied by
  `--json`). (#22, #39)
- New manifest-format exporter plugins for `conda workspace export`:
  `conda-toml`, `pixi-toml`, and `pyproject-toml`. Registered via the
  same `conda_environment_exporters` hook as `environment-yaml` and
  `conda-workspaces-lock-v1`, so per-platform projection, `--file`
  inference, and `--output` streaming carry over. Together with
  `conda workspace import`, `conda workspace` now translates in both
  directions across every supported manifest dialect.
  (#14, #41, #37, #44)
- The `pyproject-toml` exporter splices its content under
  `[tool.conda]` and preserves peer tables (`[project]`,
  `[build-system]`, `[tool.ruff]`, `[tool.pixi]`, ...) when the
  target file already exists. (#41, #44)
- `conda workspace lock --output <path>` writes the lockfile to an
  arbitrary path (e.g. `conda.lock.linux-64`) so matrix CI runners
  can each emit a per-platform fragment. (#34, #38)
- `conda workspace lock --merge <glob>` (repeatable) stitches
  lockfile fragments back into a single `conda.lock` without
  re-solving. Validates schema version and per-environment channel
  agreement, rejects overlapping `(environment, platform)` pairs
  (raising `LockfileMergeError`), and produces output byte-identical
  to a single-run `lock` over the same inputs. Mutually exclusive
  with `--environment`, `--platform`, `--skip-unsolvable`, and
  `--output`. (#34, #38)
- `conda workspace lock` now writes a single `conda.lock` covering
  every platform declared by each environment, not just the host
  platform. Solves run with `context._subdir` overridden so conda's
  virtual package plugins (`__linux`, `__osx`, `__win`) and solver
  `subdirs` resolution target the correct subdir.
  `CONDA_OVERRIDE_*` and `[system-requirements]` continue to pin
  constraints like `__glibc`, `__cuda`, or `__osx`.
  (#4, #31)
- `conda workspace lock --platform <subdir>` (repeatable) restricts
  the lock run to a subset of declared platforms. Unknown platforms
  raise `PlatformError` before any solve runs. (#4, #31)
- `conda workspace lock --skip-unsolvable` keeps locking the
  remaining `(environment, platform)` pairs when one solve fails,
  printing a yellow `Skipping ...` line for each. Raises
  `AllTargetsUnsolvableError` if every pair fails, so CI never
  writes an empty lockfile. Non-solver errors still abort regardless.
  (#33, #31)
- `--json` is now accepted across every `conda workspace` and
  `conda task` subcommand. Side-effect-only commands (`init`,
  `activate`, `run`, `shell`) used to crash with
  `unrecognized arguments: --json` when CI wrappers passed the flag
  globally; they now accept it silently and rely on the exit code.
  See the `--json contract` section in `AGENTS.md`. (#46)
- `conda workspace info --json` exposes the reachable set of
  platforms as `known_platforms` (and a `Known Platforms` row in
  text output when features broaden the workspace-level set), via
  the new `conda_workspaces.resolver.known_platforms()` helper.
  (#4, #31)
- `SolveError` names the target platform when known, so
  per-platform failures stand out in CI logs. (#4, #31)
- Inside `conda workspace shell`, `add` / `remove` / `install`
  print a hint to re-spawn the shell when a newly installed package
  drops activation scripts into `$PREFIX/etc/conda/activate.d/`.
  (#21, #28)
- New `demos/multi-platform.{tape,gif,mp4}` recording for
  cross-platform locking and the `--platform` flag. The
  `demos/lockfile` recording was refreshed to show multi-platform
  default output. (#4, #31, #45)

### Changed

- `conda workspace add` and `conda workspace remove` now install into
  the affected environment(s) and refresh `conda.lock` by default,
  matching `pixi add` / `pixi remove`. Use `--no-install`,
  `--no-lockfile-update`, `--force-reinstall`, or `--dry-run` to opt
  out. (#21, #28)
- `conda workspace install` shares a single solve/install/lock
  pipeline with `add` and `remove`
  (`conda_workspaces/cli/workspace/sync.py`). (#28)
- `conda_workspaces.parsers` renamed to `conda_workspaces.manifests`
  (named after the subject, not the verb). Class names like
  `CondaTomlParser` are unchanged; public re-exports preserved.
  (#29)
- `conda_workspaces.env_spec` shrunk to the `conda.toml` env-spec
  plugin (`CondaWorkspaceSpec`). `CondaLockSpec` was replaced by
  `conda_workspaces.lockfile.CondaLockLoader`. (#4, #29)
- Plugin metadata moved to module-level `FORMAT` / `ALIASES` /
  `DEFAULT_FILENAMES` constants. The canonical lockfile `FORMAT` is
  now `conda-workspaces-lock-v1`; `conda-workspaces-lock` and
  `workspace-lock` remain as aliases. See
  `docs/reference/format-aliases.md`. (#29, #35)
- `generate_lockfile` now builds
  `conda.models.environment.Environment` objects and delegates YAML
  serialisation to the same `multiplatform_export` hook as
  `conda export --format=conda-workspaces-lock-v1`. `conda workspace
  lock` and `conda export` now produce byte-identical output.
  (#35)
- `conda_workspaces.lockfile` owns both the write path and the
  `CondaEnvironmentSpecifier` plugin (`CondaLockLoader`), and
  delegates YAML→`Environment` conversion to
  `conda_lockfiles.rattler_lock.v6`. `conda.lock` is documented as a
  derivative of rattler-lock v6 (`pixi.lock`): same schema family,
  distinct filename and on-disk version byte. (#4, #29)
- Bumped the optional `conda-spawn` dependency floor from `>=0.0.5` to
  `>=0.1.0` to pick up the new fish/csh/tcsh/xonsh shell support and
  the double-prompt / PowerShell `-NoExit` / `$CONDA_ROOT/condabin`
  fixes. The integration in `cli/workspace/shell.py` is unchanged
  (`conda_spawn.main.spawn` is still the entry point). (#49)
- `conda task run <task>` (and the `ct run` alias) now falls back to
  the workspace's `default` environment when the task doesn't declare
  a `default_environment`, instead of inheriting whichever conda env
  happened to be active at the call site. Pass `-e` explicitly to
  override. (#26, #27)

### Fixed

- `conda workspace quickstart` crashed every invocation with
  `AttributeError: 'Namespace' object has no attribute 'verbose'`
  after conda renamed the namespace dest to `verbosity`. (#46)
- `conda workspace quickstart --json` no longer leaks Rich status
  lines from the sub-handlers (`init`, `add`, `install`) into stdout;
  the JSON payload is the only thing emitted on stdout, matching the
  documented `--json contract`. (#46)

## 0.3.0 — 2026-03-31

### Added

- `conda workspace import` command to convert `environment.yml`,
  `anaconda-project.yml`, `conda-project.yml`, `pixi.toml`, and
  `pyproject.toml` manifests to `conda.toml` (#12, #13)
- Progress output during import (reading, format detection, write status)
  (#13)
- Syntax-highlighted TOML preview in `--dry-run` mode (#13)
- `conda task add` and `conda task remove` support for `pixi.toml` and
  `pyproject.toml` manifests (#8, #9, #10)
- Codecov integration and coverage badge (#15)
- CI, docs, PyPI, and conda-forge badges to README (#15)
- Documentation for `CONDA_PKGS_DIRS` hardlink optimization in CI/Docker
  (#5, #10)
- Diataxis-organized documentation sidebar (#11)

### Changed

- Import format detection uses human-readable labels instead of class names
  (#13)
- Importers use `packaging.Requirement` for robust pip dependency parsing
  (#13)
- Simplified importer registry to a single `find_importer` function
  (#13)
- Unified installation docs (conda install and pixi global install)
  (#15)

### Fixed

- `--dry-run` output no longer strips TOML section headers in non-terminal
  environments (#13)
- Trailing dot suppressed in import status when output is in the current
  directory (#13)

## 0.2.0 — 2026-03-30

### Added

- `conda task` subcommand with `run`, `list`, `add`, `remove`, and `export`
  (#1)
- `conda workspace run` command for one-shot execution in environments
  (#1)
- Task dependencies with topological ordering (`depends-on`) (#1)
- Jinja2 template support in task commands (`{{ conda.platform }}`,
  conditionals) (#1)
- Task output caching with input/output file declarations (#1)
- Per-platform task overrides via `[target.<platform>.tasks]` (#1)
- Task arguments with default values (#1)
- Rich terminal output for all CLI commands (tables, status, errors)
  (#1)
- Structured error rendering with actionable hints (#1)
- Integration tests for CLI workflows (#1)
- Demo recordings for terminal screencasts (#1)

### Changed

- Verb-based status messages (Installing, Installed, etc.) replace
  symbol-based markers (#1)
- All CLI output routed through Rich console for consistent formatting
  (#1)
- Documentation standardized to use `conda workspace` / `conda task`
  as primary CLI forms (`cw` / `ct` noted as aliases) (#1)
- Aligned parsers with pixi workspace semantics for broader manifest
  compatibility (#1)
- Exception hierarchy expanded with type annotations and actionable hints
  (#1)

### Fixed

- JSON output in `conda task list --json` no longer includes ANSI escapes
  (#1)
- Activation script handling on Windows uses correct path validation
  (#1)
- Solver output noise suppressed during lockfile generation (#1)
- Stdout flushed after conda solver and transaction API calls (#1)

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
