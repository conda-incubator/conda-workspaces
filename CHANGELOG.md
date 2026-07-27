# Changelog

All notable changes to conda-workspaces will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added

- Added `conda workspace update SPECS...` to update selected conda
  roots while preserving existing constraints and unrelated lockfile
  slices. Use `--no-install` for lock-only updates. Workflows that used
  `conda workspace add PACKAGE` or replaced a declaration with
  `PACKAGE=*` only to permit a newer version should use
  `conda workspace update PACKAGE` instead. (#128)
- `conda workspace info --json` now includes an
  `environment_details` snapshot with every environment's feature
  composition, prefix state, channels, per-platform resolved
  dependencies, and exact manifest declaration provenance.
  `--packages` adds installed package records to the same response.
  `workspace envs --json` now also reports `no_default_feature`.
  Machine consumers that currently call `workspace envs`, then
  `workspace info -e` and `workspace list -e` for each environment can
  migrate to one `workspace info --json --packages` call. (#127)

### Changed

- Selective `conda workspace install -e` and feature-scoped `add` or
  `remove` operations now install only the selected or affected
  prefixes while regenerating the canonical `conda.lock` for every
  declared environment and platform. Workflows that relied on an
  incomplete canonical lock should use `conda workspace lock -e
  <environment> --output <fragment>` instead. (#121)
- `conda workspace lock` now requires an explicit `--output` with
  `--environment`, `--platform`, or `--skip-unsolvable`. Run an
  unfiltered lock to replace the complete canonical `conda.lock`, or
  pass a fragment path for a partial result. Workflows that
  intentionally replace `conda.lock` with a filtered result must now
  pass `--output conda.lock`. (#122)
- `conda workspace init` and generated `quickstart` workspaces now use
  conda's configured channels. Repeated `-c/--channel` values are
  prepended in command-line order, and `--override-channels` keeps only
  those explicit values. Copy and clone continue preserving the source
  manifest. Users who relied on the implicit conda-forge fallback must
  configure that channel or pass
  `-c conda-forge --override-channels`. (#129)
- The global `conda workspace --file` option now selects the exact
  manifest named by the caller. To use upward auto-discovery, omit the
  option instead of passing a directory. The `export` subcommand keeps
  its own `--file` option for the output path, so a source manifest and
  unrelated export destination can be supplied together. Archive
  installation now requires exactly one valid manifest at the archive
  root. Remove unselected manifests before archiving when using
  `unarchive --install`. (#120)

### Fixed

- Receipt-verified bundles now publish the URL, SHA-256, size, and filename
  metadata that conda needs to recognize bundled packages. Both
  `unarchive --install` and a later `install --locked` now work from an empty
  package cache with conda in offline mode. Earlier releases primed only the
  package archive files. Re-run receipt-verified `unarchive` to add the
  missing cache records. If conda created a conflicting extracted cache entry
  during a failed offline install, remove that entry and retry.
- `conda workspace quickstart -e <name> <specs>` now creates the named
  environment when needed, records the specs as private dependencies,
  installs only that prefix, and reports the same environment in JSON.
  This also makes specs private to the default environment instead of
  placing them in the shared top-level `[dependencies]` table. Use
  `conda workspace init`, then `conda workspace add` without
  `-e/--environment`, when specs should be shared across environments.
  `--locked` and `--frozen` with positional specs now fail
  before writing because adding specs must regenerate `conda.lock`.
  Remove the lock mode while bootstrapping specs, then use
  `conda workspace install --locked` or `--frozen` for later installs.
  (#126)
- `conda workspace remove` now clears direct requested specs that are
  absent from the resolved manifest before installing the remaining
  dependency closure. Packages still required transitively remain
  installed without remaining direct requests. Lock generation now
  ignores stale prefix history, including with `remove --no-install`.
  Installing from `conda.lock` now removes conda packages absent from
  the lock and records only manifest roots as direct requests. Declare
  packages in the workspace instead of installing unmanaged additions
  directly into its prefixes.
  Workspaces affected by 0.7.x can run
  `conda workspace install --force-reinstall` once to rebuild existing
  prefixes and `conda.lock` from the manifest. (#117)
- `conda workspace add` and `remove` now mutate one explicit dependency
  declaration location. With no selector, commands address the default
  feature. `--feature default` is accepted as an explicit equivalent.
  Raw `[feature.default]` tables are now rejected. Move their contents
  to the corresponding top-level tables.
  `--feature` addresses a named feature, `--environment` addresses private
  environment dependencies, and `--platform` nests a target table below
  the selected location. Bare adds preserve existing
  `{ workspace = true }` entries, while explicit specs replace only the
  selected membership entry and never change `[workspace.dependencies]`.
  `add` warns when a later declaration still overrides the result.
  `remove` now fails before writing when a package is absent from the
  selected location but declared elsewhere, and lists the exact selector
  for every matching location. Scripts that expected `add` or `remove` to
  find the effective declaration must now pass the selectors for that
  table, such as `--feature test --platform linux-64` or
  `--environment test --platform linux-64`. The broken `tomlkit 0.15.0`
  release is excluded because it can serialize invalid TOML when these
  commands extend an existing inline table. (#125)
- `conda workspace add` and `remove` now treat `--environment` as a
  private dependency location below `[environments.<name>]` instead of
  assuming a same-named feature. Composed environments, the `default`
  environment, and `no-default-feature` environments now retain their
  intended feature composition, while `--feature` remains the selector
  for shared declarations. Review any `[feature.<environment>]` tables
  created by earlier `add -e` commands. Move dependencies intended for
  one environment to `[environments.<environment>.dependencies]`, then
  remove the accidental feature or feature-list entry. Keep genuinely
  shared declarations in place and manage them with `--feature`. (#123)
- `conda workspace add` now writes channel, build, subdir, hash, URL,
  file-name, license, feature, and track-feature MatchSpec fields
  without reducing them to a version string. A bare re-add preserves
  the existing declaration, while an explicit spec replaces it
  completely and unsupported fields fail before the manifest is
  written. Users whose earlier `add` commands lost constraints should
  rerun the explicit spec to restore them. (#124)
- Platform-filtered lock commands now reach platforms declared only by
  a feature without requiring feature-restricted environments to
  support the current host. (#122)
- Successful `workspace add`, `remove`, `install`, `lock`, `clean`,
  `import`, `archive`, and `unarchive` commands now emit exactly
  `{"success": true}` with `--json`. Human status output from nested
  operations is suppressed so stdout remains one parseable value. The
  standalone `cw --json` entry point now initializes conda's JSON
  reporter too. Scripts that parsed status prose should switch to the
  `success` field. (#119)
- `conda workspace --dry-run` now preserves manifests, lockfiles,
  environment prefixes, activation metadata, archives, receipts,
  and extraction targets across quickstart, add, remove, install,
  lock, archive, unarchive, and clean operations. Solver previews use a
  disposable package cache, and archive previews skip bundled package
  cache priming, so configured package caches remain unchanged. Commands
  still perform the read-only validation needed to report the intended
  work. The selected archive manifest and `conda.lock` must now be regular
  root members, and bundled packages must be regular direct children of
  `packages/`. Replace linked required files and move nested package files
  before using an existing archive. Manifest creation and copy destinations
  must also be regular paths. Replace a linked destination manifest before
  running `init` or `quickstart`. (#118)

### Security

- Workspace environment cleanup now rejects a symbolic-link environment
  directory or one that resolves outside the workspace before enumerating or
  deleting prefixes. Replace linked `.conda` or `.conda/envs` paths with real
  directories below the workspace root. Activation state, activation script
  directories, and activation script destinations also reject symbolic links.
  Replace linked metadata with regular files and directories inside the prefix.
- Generated manifests no longer persist channel authentication from Conda
  configuration. Absolute channel origins remain explicit, credential-bearing
  exact package and PyPI source URLs are rejected during manifest mutation or
  import, and workspace information redacts PyPI dependency credentials.
  Relative `t/<token>/<channel>` values are resolved to a credential-free
  channel URL, and scheme-relative `//host/path` channels become explicit
  HTTPS URLs. Every dependency mutation and archive creation rejects credentials
  that remain elsewhere in the manifest. Existing users should remove embedded
  authentication, Anaconda
  `/t/<token>/` paths, queries, and fragments from manifests, rotate any exposed
  values, and configure replacement credentials through Conda outside the
  repository.
- Manifest import and export now preserve representable conda channel, build,
  subdir, hash, and direct URL fields instead of reducing them to a version.
  PyPI extras and credential-free named direct URLs also survive manifest
  export. PyPI environment markers, unparseable requirement lines, and path or
  VCS sources that cross conda's environment exporter interface now fail before
  an output is written instead of silently changing package identity. Replace
  marker-only declarations with target-specific manifest tables, and keep path
  or VCS declarations in the source manifest until the selected exporter can
  represent them losslessly.
- Lockfile package URLs are normalized before channel containment checks, so
  plain, encoded, and double-encoded path traversal cannot escape a declared
  channel. Generated and merged lockfiles strip basic authentication, Anaconda
  token paths, queries, and fragments from channel and package URLs, including
  slices retained by selective updates. Regenerate credential-bearing lockfiles
  and rotate exposed values. Lockfile output also refuses symbolic links.
  Replace a linked `conda.lock` or fragment with a regular file before writing
  it. Archive creation rejects a credential-bearing existing lockfile, so
  regenerate it before packaging the workspace.
- Workspace archive validation now bounds member count, paths, link targets,
  consecutive extension headers, PAX record count, raw extension metadata, and
  total expanded file and link-fallback size while retaining linear memory.
  Unsupported member types and GNU sparse members are rejected before their
  payloads are traversed. Receipt digests are checked before archive inspection,
  and creation rejects a linked repository manifest before reading it. Repack
  sparse or metadata-heavy archive inputs as ordinary bounded members. Bundle
  and receipt lock inputs are read under the publication guard, receipt writes
  are atomic, and a receipt failure removes a new archive output while keeping
  any canonical lockfile that was already published successfully.
  Package cache priming rehashes the bytes it publishes and refuses raced source
  or destination symbolic links. Archive output is published atomically, and
  receipt verification, inspection, and extraction consume one immutable
  snapshot rather than reopening the source path. Extraction targets must now
  be absent, not merely empty, so remove an empty destination before retrying.
  Archive hardlinks are rejected, and Python runtimes without tar extraction
  filters must be updated to a current supported patch release.
- Complete lock solving now succeeds before ordinary sync mutates a selected
  prefix, and that exact solution is installed without a second solve. Pruning
  validates the desired transaction before removal, preserves declared local
  path dependencies, and reports rebuild failures. Add, remove, selective
  update, ordinary install, canonical lock generation and merge, and archive
  creation now share one workspace guard. Retry an interrupted publisher after
  any concurrent manifest edit. Frozen and locked multi-environment installs
  consume one in-memory lock snapshot so their prefixes cannot mix generations.
  Locked installs preflight every requested environment before mutating any
  prefix, and publication binds parsed configuration to the exact manifest
  generation it came from. `conda env create --file conda.lock` now rejects
  external package refs because that loader cannot carry a verifiable digest
  into conda's installer. Declare those dependencies in `conda.toml`, regenerate
  `conda.lock`, then use `conda workspace install`.
- Repository-controlled TOML, YAML, and JSON documents now have explicit byte,
  nesting, collection, and aggregate item limits. Manifest files are limited to
  16 MiB, lockfiles to 128 MiB, and receipts to 64 MiB. Split oversized trusted
  inputs and reduce deeply nested or very large collections before retrying.
  Portable paths and environment names also reject Windows device aliases,
  alternate data stream syntax, trailing dots or spaces, and case-insensitive
  archive collisions on every host. Rename ambiguous entries before moving a
  workspace or archive between platforms.
- Manifest, task, export, lockfile, archive, receipt, activation metadata, and
  environment writes now reject symbolic-link destinations and concurrent file
  generation changes. Replace linked outputs with regular workspace-local
  files before retrying. Human-readable task and workspace output renders
  repository-controlled terminal control bytes visibly instead of forwarding
  them to the terminal. JSON output remains unchanged.
- Path and generation checks reject linked destinations and replacements that
  remain visible at mutation boundaries. Conda and conda-pypi still receive
  filesystem paths, so these checks do not sandbox another process running as
  the same operating-system user that swaps and restores a prefix during one
  downstream call. Archive operations use descriptor-relative protection when
  the platform provides it. On platforms without those APIs, validation and
  path-based publication checks remain in place, but same-user swap-and-restore
  races cannot be excluded.

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
