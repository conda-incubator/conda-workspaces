# Features

## Tasks

### Task commands

![task quickstart demo](../demos/task-quickstart.gif)

A task's `cmd` can be a simple string or a list of strings joined with
spaces:

```toml
[tasks]
build = "python -m build"
build-alt = { cmd = ["python", "-m", "build", "--wheel"] }
```

### Task dependencies

![depends-on demo](../demos/depends-on.gif)

Tasks can depend on other tasks. Dependencies are resolved with
topological ordering so everything runs in the right sequence:

```toml
[tasks]
compile = { cmd = "gcc -o main main.c", description = "Compile the program" }
test = { cmd = "./main --test", depends-on = ["compile"], description = "Run tests" }

[tasks.check]
depends-on = ["test", "lint"]
description = "Run all checks"
```

Running `conda task run check` resolves the full dependency graph and
runs each task in order.

### Task aliases

Tasks with no `cmd` that only list dependencies act as aliases:

```toml
[tasks.check]
depends-on = ["test", "lint", "typecheck"]
description = "Run all checks"
```

### Hidden tasks

Tasks prefixed with `_` are hidden from `conda task list` but can still
be referenced as dependencies or run explicitly:

```toml
[tasks]
_setup = "mkdir -p build/"

[tasks.build]
cmd = "make"
depends-on = ["_setup"]
```

### Task arguments

![templates demo](../demos/templates.gif)

Tasks can accept named arguments with optional defaults:

```toml
[tasks.test]
cmd = "pytest {{ test_path }} -v"
args = [
  { arg = "test_path", default = "tests/" },
]
description = "Run tests on a path"
```

Run with:

```bash
conda task run test src/tests/
```

### Template variables

Commands support Jinja2 templates with `conda.*` context variables:

| Variable | Description |
|---|---|
| `{{ conda.platform }}` | Current platform (e.g. `osx-arm64`) |
| `{{ conda.environment_name }}` | Name of the active conda environment |
| `{{ conda.environment.name }}` | Active environment name |
| `{{ conda.prefix }}` | Target conda environment prefix path |
| `{{ conda.version }}` | conda version |
| `{{ conda.manifest_path }}` | Path to the task file |
| `{{ conda.init_cwd }}` | Current working directory at rendering time |
| `{{ conda.is_win }}` | `True` on Windows |
| `{{ conda.is_unix }}` | `True` on non-Windows |
| `{{ conda.is_linux }}` | `True` on Linux |
| `{{ conda.is_osx }}` | `True` on macOS |

When reading from `pixi.toml`, `{{ pixi.platform }}` etc. also work as
aliases.

### Task environment variables

```toml
[tasks.test]
cmd = "pytest"
env = { PYTHONPATH = "src", DATABASE_URL = "sqlite:///test.db" }
```

### Clean environment

Run a task with only essential environment variables:

```toml
[tasks]
isolated-test = { cmd = "pytest", clean-env = true }
```

Or via CLI: `conda task run test --clean-env`

### Task caching

![caching demo](../demos/caching.gif)

When `inputs` and `outputs` are specified, tasks are cached and
re-execution is skipped when inputs haven't changed:

```toml
[tasks.build]
cmd = "python -m build"
inputs = ["src/**/*.py", "pyproject.toml"]
outputs = ["dist/*.whl"]
```

:::{tip}
The cache uses a fast `(mtime, size)` pre-check before falling back to
SHA-256 hashing, so the overhead on cache hits is minimal.
:::

### Platform-specific tasks

![platform overrides demo](../demos/platform-overrides.gif)

Override task fields per platform using the `target` key:

::::{tab-set}

:::{tab-item} TOML

```toml
[tasks]
clean = "rm -rf build/"

[target.win-64.tasks]
clean = "rd /s /q build"
```

:::

:::{tab-item} Jinja2 conditional

```toml
[tasks]
clean = "{% if conda.is_win %}rd /s /q build{% else %}rm -rf build/{% endif %}"
```

:::

::::

### Task environment targeting

:::{versionchanged} 0.4.0
When a task is defined in a manifest that also declares workspace
environments, `conda task run` now falls back to the workspace's
`default` environment instead of whatever conda environment happens
to be active. Tasks without a workspace (tasks-only manifests)
still use the current conda environment. `-e <env>` and a task's
`default-environment` key continue to take precedence.
:::

Tasks defined alongside workspace environments run in the workspace's
`default` environment. Override with `-e <env>`:

```bash
conda task run test -e myenv
```

Tasks can also declare a default environment:

```toml
[tasks.test-legacy]
cmd = "pytest"
default-environment = "py38-compat"
```

### User-level tasks

Define tasks in `~/.config/conda/tasks.toml` to make them available in
every project without repeating definitions:

```toml
[tasks]
fmt = { cmd = "ruff format .", description = "Format Python files" }
clean = { cmd = "git clean -fdx -e .conda", description = "Remove untracked files" }
check = { cmd = "ruff check --fix .", description = "Lint and auto-fix" }
```

User tasks act as defaults — if a project defines a task with the same
name, the project version takes precedence. `conda task list` marks
user-sourced tasks with `(user)` so you can tell where each task comes
from:

```console
$ conda task list
  build    python -m build
  test     pytest tests/ -v
  fmt      ruff format .        (user)
  clean    git clean -fdx ...   (user)
```

User tasks work even outside a workspace. See
{ref}`User-level tasks <user-level-tasks>` for file location details and merge
semantics.

---

## Environments

![multi-env demo](../demos/multi-env.gif)

Environments are named conda prefixes composed from one or more features.
Each environment is installed under `.conda/envs/<name>/` in your project.

```toml
[environments]
default = []
test = { features = ["test"] }
docs = { features = ["docs"] }
```

The `default` environment always exists and includes the top-level
`[dependencies]`. Named environments inherit the default feature unless
`no-default-feature = true` is set.

:::{note}
Pixi's `solve-group` key is accepted in manifests for compatibility but
has no effect. Conda's solver operates on a single environment at a time
and does not support cross-environment version coordination. Each
environment is solved independently.
:::

## Features

Features are composable groups of dependencies, channels, and settings.
They map to `[feature.<name>]` tables in the manifest:

```toml
[feature.test.dependencies]
pytest = ">=8.0"
pytest-cov = ">=4.0"

[feature.docs.dependencies]
sphinx = ">=7.0"
myst-parser = ">=3.0"
```

When an environment includes multiple features, dependencies are merged
in order. Later features override earlier ones for the same package name.

### Workspace dependency inheritance

Use `[workspace.dependencies]` to centralize conda specs that multiple
dependency tables should share. A table entry opts in explicitly with
`{ workspace = true }`. Pixi added the same workspace dependency
inheritance syntax in 0.70.0, and conda-workspaces reads it from
`conda.toml`, `pixi.toml`, and supported `pyproject.toml` tables.

```toml
[workspace.dependencies]
numpy = "1.*"
cmake = { version = ">=3.28", channel = "conda-forge" }

[dependencies]
numpy = { workspace = true }

[feature.build.dependencies]
cmake = { workspace = true, build = "h*" }
```

The root spec supplies the version and any other base match fields. The
consuming entry may add non-version fields such as `build`, `channel`,
or `subdir`; restating `version` alongside `workspace = true` is an
error.

## Channels

Channels are specified at the workspace level and can be overridden per
feature:

```toml
[workspace]
channels = ["conda-forge"]

[feature.special.dependencies]
some-pkg = "*"

[feature.special]
channels = ["conda-forge", "bioconda"]
```

Feature channels are appended after workspace channels, with duplicates
removed.

(platform-targeting)=

## Platform targeting

![multi-platform demo](../demos/multi-platform.gif)

Per-platform dependency overrides use `[target.<platform>]` tables:

```toml
[dependencies]
python = ">=3.10"

[target.linux-64.dependencies]
linux-headers = ">=5.10"

[target.osx-arm64.dependencies]
llvm-openmp = ">=14.0"
```

Platform overrides are merged on top of the base dependencies when
resolving for a specific platform.

### Known vs. declared platforms

:::{versionadded} 0.4.0
`conda workspace info` surfaces the reachable platform set as a
`known_platforms` JSON key (and a matching `Known Platforms` row in
the text view whenever a feature broadens the workspace-level set).
`conda workspace lock --platform <subdir>` validates against this
same set.
:::

The workspace-level `platforms` list is the default set every
environment can be solved for. Individual features may declare
additional platforms, and those are reachable through any
environment that activates that feature. To see the full reachable
set, run:

```bash
conda workspace info            # text view, extra "Known Platforms" row
conda workspace info --json     # JSON "known_platforms" key
```

`conda workspace lock --platform <subdir>` validates against this
reachable set, so typos like `lixux-64` are rejected before the
solver runs.

(pypi-dependencies)=

## PyPI dependencies

PyPI dependencies are specified separately from conda dependencies:

```toml
[pypi-dependencies]
my-local-pkg = { path = ".", editable = true }
some-pypi-only = ">=1.0"

[feature.test.pypi-dependencies]
pytest-benchmark = ">=4.0"
```

PyPI package names are translated to their conda equivalents (via the
[grayskull mapping](https://github.com/conda/grayskull)) and merged
into the same solver call as conda dependencies. `conda-pypi` delegates
to the configured solver backend to resolve conda and PyPI packages
together in a single pass and handles `.whl` installation.

To use PyPI dependencies you need:

- [conda-pypi](https://github.com/conda/conda-pypi) (`>=0.9.0`) for
  name mapping and wheel extraction
- [conda-rattler-solver](https://github.com/conda-incubator/conda-rattler-solver)
  as the solver backend (no longer a hard dependency of conda-pypi, so
  install it explicitly)
- The `conda-pypi` channel (`conda config --append channels conda-pypi`)
  which serves pure Python packages from PyPI as conda packages using
  sharded repodata (requires the rattler solver)

Editable, git, and URL dependencies (e.g. `path = "."`, `git = "..."`)
are handled separately via `conda-pypi`'s build system after the main
solve completes. If `conda-pypi` is not installed, PyPI dependencies
are skipped with a warning.

See the [PyPI dependencies tutorial](tutorials/pypi-dependencies.md)
for a full walkthrough including editable installs and troubleshooting.

## No-default-feature

An environment can opt out of inheriting the default feature:

```toml
[environments]
minimal = { features = ["minimal"], no-default-feature = true }
```

This is useful for environments that need a completely independent
dependency set.

## Activation

Features can specify activation scripts and environment variables:

```toml
[activation]
scripts = ["scripts/activate.sh"]
env = { MY_VAR = "value" }

[feature.dev.activation]
env = { DEBUG = "1" }
```

Activation settings are merged across features when composing an
environment. After `conda workspace install`, environment variables are written to
the prefix state file (available via `conda activate`) and activation
scripts are copied to `$PREFIX/etc/conda/activate.d/`.

## System requirements

System requirements declare minimum system-level dependencies:

```toml
[system-requirements]
cuda = "12"
glibc = "2.17"
```

System requirements are added as virtual package constraints
(`__cuda >=12`, `__glibc >=2.17`) during environment solving. This
ensures the solver only picks packages compatible with the declared
system capabilities.

## Channel priority

The workspace-level `channel-priority` setting overrides conda's global
channel priority during solving:

```toml
[workspace]
channels = ["conda-forge"]
channel-priority = "strict"
```

Valid values are `strict`, `flexible`, and `disabled`. When not set,
conda's default channel priority applies.

(lock)=

## Lock

:::{versionchanged} 0.4.0
`conda workspace lock` now writes a single `conda.lock` that covers
every platform declared by each environment, not just the host
platform. Target-platform solves run with `context._subdir`
overridden so conda's virtual package plugins (`__linux`, `__osx`,
`__win`) match the target platform.
:::

:::{versionadded} 0.4.0
`--platform <subdir>` (repeatable) restricts the lock run to a
subset of declared platforms; unknown platforms raise
`PlatformError` before any solve runs. `--skip-unsolvable` keeps
locking the remaining `(environment, platform)` pairs when an
individual solve fails, aggregating the failures into
`AllTargetsUnsolvableError` only if *every* pair fails.
`SolveError` now names the target platform for easier CI triage.
:::

conda-workspaces generates a `conda.lock` file in YAML format using a
rattler-lock-derived schema with a conda-workspaces-owned version byte.
The `conda workspace lock` command runs the solver and records the
solution — it does not require environments to be installed first.

```bash
# Generate or update the lockfile for every platform declared in the manifest
conda workspace lock

# Lock only a subset of platforms (repeatable flag)
conda workspace lock --platform linux-64 --platform osx-arm64

# Keep going when individual (environment, platform) pairs fail to solve
conda workspace lock --skip-unsolvable

# Install from lockfile, validating freshness against the manifest
conda workspace install --locked

# Install from lockfile as-is without checking freshness
conda workspace install --frozen
```

`conda workspace lock` solves every environment for every platform it
declares in the manifest. Each solve runs with conda's
`context._subdir` pointed at the target platform so virtual packages
(`__linux`, `__osx`, `__win`) match the target, not the host. Pin
tighter constraints with `CONDA_OVERRIDE_*` or the
`[system-requirements]` table when cross-compiling (for example, to
fix a minimum `__glibc` version when solving `linux-64` from macOS).

Solves are fail-fast by default: the first platform that cannot be
resolved raises an error that names the environment and the platform,
and no lockfile is written. Pass `--skip-unsolvable` to keep locking
the remaining pairs, emitting a yellow `Skipping ...` line for each
one that failed. If *every* pair fails, the command still raises
with an aggregated summary rather than writing an empty lockfile —
non-solver errors (missing channel, invalid manifest, etc.) always
abort regardless of the flag.

The lockfile contains all environments and their resolved packages:

```yaml
version: 1
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
        - conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.12.0-...
  test:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
    packages:
      linux-64:
        - conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.12.0-...
        - conda: https://conda.anaconda.org/conda-forge/linux-64/pytest-8.0.0-...
packages:
  - conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.12.0-...
    sha256: abc123...
    depends:
      - libffi >=3.4
    # ...
```

### Automatic lockfile management

`conda workspace install` checks whether the lockfile satisfies the
manifest before deciding how to install. When the lockfile covers all
declared environments, channels, platforms, and dependency specs, it
installs directly from the lockfile (no solver needed). When the
lockfile is missing or out of date, it falls back to a full solve and
regenerates `conda.lock`.

This means day-to-day installs are fast (no solver) while the lockfile
stays current when the manifest changes. The check compares the
manifest's dependency specs against locked package versions, so
whitespace or comment changes do not trigger a re-solve.

| Lockfile state | Default behavior | `--locked` | `--frozen` | `--no-lock` |
| --- | --- | --- | --- | --- |
| Satisfiable | Install from lockfile | Install from lockfile | Install from lockfile | Full solve |
| Not satisfiable | Full solve + update lockfile | Error | Install from lockfile | Full solve |
| Missing | Full solve + create lockfile | Error | Error | Full solve |

Use `--no-lock` to force a full solve even when the lockfile is
satisfiable (for example, to pick up channel updates without editing
the manifest).

### Lock freshness indicator

`conda workspace info` shows the lockfile status:

```bash
conda workspace info
```

The Lockfile row shows `up-to-date` (green), `out-of-date` (yellow),
or `missing` (red). The JSON output includes a `lockfile_status` field:

```bash
conda workspace info --json | jq .lockfile_status
```

### CI-friendly defaults

When the `CI` environment variable is set (`true`, `1`, or `yes`),
`conda workspace install` behaves like `--locked`: it requires a
satisfiable lockfile and fails if it is missing or out of date. This
prevents accidental re-solves in CI that could produce different
results than local development.

```yaml
# GitHub Actions example
- run: conda workspace install  # uses lockfile, fails if out of date
  env:
    CI: true  # set by GitHub Actions automatically
```

To override this in CI (for example, in a nightly job that refreshes
the lockfile), pass `--no-lock`:

```bash
conda workspace install --no-lock
```

### CI-split locking with `--merge`

![ci-split demo](../demos/ci-split.gif)

:::{versionadded} 0.4.0
`conda workspace lock --output <path>` writes the solved lockfile
to an arbitrary path so matrix runners can each emit one fragment.
`--merge <glob>` stitches fragments into a single `conda.lock`
without running the solver, validating schema version, channel
lists, and rejecting overlapping `(environment, platform)` pairs.
:::

Solving every platform in one job becomes expensive as a workspace
grows. `conda workspace lock` supports matrix pipelines that split
solving across runners and stitch the fragments back together on a
coordinator job:

```bash
# In a matrix job, per platform
conda workspace lock --platform linux-64 --output conda.lock.linux-64
conda workspace lock --platform osx-arm64 --output conda.lock.osx-arm64
conda workspace lock --platform win-64 --output conda.lock.win-64

# On the coordinator — no solver runs, fragments are combined in place
conda workspace lock --merge "conda.lock.*"
```

`--output` writes the solved lockfile to the given path instead of the
default `<workspace>/conda.lock`. It may be combined with `--platform`
so each matrix runner emits exactly one `(env, platform)` slice.

`--merge` loads every fragment, validates that they agree on schema
version and on each shared environment's channel list, and rejects
overlapping `(environment, platform)` pairs. On success the merged
`conda.lock` is byte-stable with what a single-run
`conda workspace lock` would produce for the same inputs. `--merge`
is mutually exclusive with `--environment`, `--platform`,
`--skip-unsolvable`, and `--output`.

You can also point to a specific manifest with `--file` / `-f`:

```bash
conda workspace install -f path/to/conda.toml
```

## Export

![export demo](../demos/export.gif)

:::{versionadded} 0.4.0
`conda workspace export` plugs into conda's
`conda_environment_exporters` plugin hook, so every format
reachable through `conda export` — and anything registered by a
third-party plugin such as `conda-lockfiles` — is also reachable
through `conda workspace export`. `--from-lockfile` and
`--from-prefix` select alternative sources; `--platform`
(repeatable) drives multi-platform exports for exporters that opt
into `multiplatform_export`.
:::

`conda workspace export` converts a workspace environment into any
format registered through conda's `conda_environment_exporters`
plugin hook: the built-in `environment-yaml` / `environment-json`
exporters, the `conda-workspaces-lock-v1` exporter registered by
conda-workspaces itself, the `conda-toml` / `pixi-toml` /
`pyproject-toml` manifest exporters, and any third-party exporter
(e.g. `conda-lockfiles`' rattler-lock-v6) the moment it is installed.
There is no separate writer — every exporter reachable through
`conda export` is reachable through `conda workspace export` and
vice versa.

```bash
# Default: environment-yaml from the declared manifest (no install needed)
conda workspace export -e default --file environment.yml

# environment.json; format auto-detected from the filename
conda workspace export -e default --file environment.json

# Re-emit the workspace as a conda.toml manifest (cross-format export)
conda workspace export --format conda-toml --file conda.toml

# Same content, nested under [tool.conda] — drops into an existing
# pyproject.toml next to [project], [build-system], [tool.ruff], ...
conda workspace export --format pyproject-toml --file pyproject.toml

# Multi-platform conda.lock re-emit via the lockfile exporter
conda workspace export --format conda-workspaces-lock-v1 \
    --platform linux-64 --platform osx-arm64 --file conda.lock

# Build the export from an existing conda.lock rather than re-solving
conda workspace export --from-lockfile --file environment.yml

# Mirror ``conda export`` semantics on an installed prefix
conda workspace export --from-prefix --no-builds --from-history
```

Three sources feed the exporter:

- **Declared (default)**: resolves the declared specs from the
  manifest per platform. No solver, no installed environment required
  — this is what makes the command useful before the first
  `conda workspace install`.
- **`--from-lockfile`**: reconstructs `Environment` objects from an
  existing `conda.lock` via the `CondaLockLoader`.
- **`--from-prefix`**: reads the live installed prefix the same way
  `conda export` does, so `--no-builds`, `--ignore-channels`, and
  `--from-history` behave identically.

`--platform` (repeatable) intersects declared / available platforms
with the chosen subset. Passing multiple platforms requires an
exporter that opts into `multiplatform_export` — the
`conda-workspaces-lock-v1`, rattler-lock, and the three manifest
exporters (`conda-toml`, `pixi-toml`, `pyproject-toml`) do; the
single-platform yaml / json ones raise a clear error.

### Manifest-format exporters

:::{versionadded} 0.4.0
Three new exporter plugins — `conda-toml`, `pixi-toml`, and
`pyproject-toml` — round-trip a workspace back into any manifest
dialect conda-workspaces already reads. Combined with
`conda workspace import`, this makes `conda workspace` a
bidirectional translator across every supported manifest format.
:::

The `conda-toml`, `pixi-toml`, and `pyproject-toml` exporters round-trip
a workspace back into any of the manifest dialects conda-workspaces
already reads, so `conda workspace import` and `conda workspace
export` together form a bidirectional translator across every
supported format. Declared specs that appear on every requested
platform land under the top-level `[dependencies]` /
`[pypi-dependencies]` tables; platform-specific deltas move under
`[target.<platform>.*]`. The `pyproject-toml` exporter wraps the
same content under `[tool.conda]`, and when the target
`pyproject.toml` already exists it splices the `[tool.conda]`
subtree into the existing document so peer `[project]`,
`[build-system]`, `[tool.ruff]`, `[tool.pixi]`, and friends
survive untouched (any stale `[tool.conda]` is replaced).
`conda.toml` and `pixi.toml` keep the default overwrite semantics
of every other conda exporter.

When `--file` is not passed, the format is inferred from the
basename (`conda.toml` → `conda-toml`, `pixi.toml` → `pixi-toml`,
`pyproject.toml` → `pyproject-toml`, `environment.yml` /
`environment.yaml` → `environment-yaml`, `environment.json` →
`environment-json`, `conda.lock` → `conda-workspaces-lock-v1`).
See [Format aliases](reference/format-aliases.md) for the full
alias table.

(archives)=

## Archives

![archive demo](../demos/archives.gif)

Package a workspace into a portable `.tar.zst`, `.tar.gz`, or
`.tar.bz2` archive that includes the manifest, lockfile, and source
files:

```bash
conda workspace archive -o my-project.tar.zst
```

When `-o/--output` is omitted, the archive is written in the workspace
root using the workspace name as the filename stem. That default name
must be a single filename segment; use `-o/--output` for other paths.

In git repos, only tracked files are included. Built-in exclusions
(`.git`, `__pycache__`, `.conda/envs`, `.pixi`, and common credential
material such as `.env`, `.ssh`, `.aws`, and `.npmrc`) always apply.
Configure additional exclusions in the manifest:

```toml
[workspace.archive]
exclude = ["docs/**", "*.log"]
```

Or pass them on the command line:

```bash
conda workspace archive --exclude "benchmarks/**"
```

Extract an archive and install environments in one step:

```bash
conda workspace unarchive my-project.tar.zst --target ./restored --install
```

The extraction target must be empty or absent. Existing files are not
overwritten.

Install one archived environment to a final runtime prefix, optionally
under a staged filesystem root:

```bash
conda workspace unarchive my-project.tar.zst \
  --install \
  --dest /tmp/rootfs \
  -e runtime \
  --prefix /opt/runtime
```

When `--dest` is used, `unarchive` warns if installed files still
reference the physical staging prefix.

Pass `--lock` to solve and update the lockfile before archiving:

```bash
conda workspace archive --lock
```

For offline deployment, `--bundle` includes resolved conda package
archives (`.conda` or `.tar.bz2`) inside the archive. Package hashes
are verified against the lockfile on bundling and before receipt-verified
cache priming:

```bash
conda workspace archive --lock --bundle --receipt -o offline.tar.zst
conda workspace unarchive offline.tar.zst --receipt
```

For handoff workflows that need a separate integrity record, `--receipt`
writes an external in-toto Statement JSON file. `unarchive --receipt`
verifies the archive, extracted manifest, extracted lockfile, and
lockfile package inventory before moving the verified workspace into
place:

```bash
conda workspace archive --lock --receipt -o my-project.tar.zst
conda workspace unarchive my-project.tar.zst --receipt --target ./verified
```

See the [archive tutorial](tutorials/archives.md) for a
full walkthrough and the [archive receipt reference](reference/archive-receipts.md)
for the receipt JSON format.

---

## Project-local environments

All environments are installed under `.conda/envs/` in your project
directory, keeping them isolated from global conda environments:

```
my-project/
├── conda.toml
├── conda.lock
├── .conda/
│   └── envs/
│       ├── default/
│       ├── test/
│       └── docs/
└── src/
```

Environments are standard conda prefixes. Use `conda workspace run -e <name> -- CMD`
to run a command in an environment, `conda workspace shell -e <name>` for an
interactive shell, or `conda activate .conda/envs/<name>` directly.

## CI and Docker

### Optimizing disk usage with hardlinks

conda hardlinks packages from its global cache into environment
prefixes, which saves significant disk space. In CI and Docker the
global cache is often on a different filesystem (or volume) from the
project directory, causing conda to silently fall back to copying
packages — roughly doubling disk usage per environment.

Set the `CONDA_PKGS_DIRS` environment variable to a project-local path
before installing so that the cache and environments share a filesystem:

```bash
export CONDA_PKGS_DIRS="$PWD/.conda/pkgs"
conda workspace install
```

::::{tab-set}

:::{tab-item} GitHub Actions

```yaml
- name: Install workspace
  run: |
    export CONDA_PKGS_DIRS="$PWD/.conda/pkgs"
    conda workspace install
```

:::

:::{tab-item} GitLab CI

```yaml
install:
  script:
    - export CONDA_PKGS_DIRS="$PWD/.conda/pkgs"
    - conda workspace install
```

:::

::::

On developer workstations the global cache is usually on the same
filesystem and hardlinks work without any extra configuration.
