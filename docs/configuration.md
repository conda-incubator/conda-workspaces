# Configuration

:::{note}
All paths below use `~/` as shorthand for your home directory:
`/home/<user>/` or `/Users/<user>/` on macOS/Linux,
`%USERPROFILE%` (typically `C:\Users\<user>\`) on Windows.
:::

When the global `--file` option is omitted, conda-workspaces searches for
manifests in the current directory and its parents. The first matching file is
used. `conda workspace --file PATH ...` selects one existing manifest file
exactly. It does not accept a directory or search from the selected path.

For the normative description of every field accepted in `conda.toml` and
the `[tool.conda.*]` embedded form, see the [`conda.toml`
specification](reference/conda-toml-spec.md). This page is the
how-to companion: it shows full examples and table summaries grouped by
task.

## Workspace search order

1. `conda.toml` — conda-native workspace manifest
2. `pixi.toml` — pixi-native format (workspace/task compatibility)
3. `pyproject.toml` — embedded under `[tool.conda.*]` or `[tool.pixi.*]`

## Task search order

1. `conda.toml` — conda-native task manifest
2. `pixi.toml` — pixi-native format (reads `[tasks]` directly)
3. `pyproject.toml` — reads `[tool.conda.tasks]` or `[tool.pixi.tasks]`

`conda task add` and `conda task remove` edit whichever of these files
is selected by that search order, using the same tables as above (for
`pyproject.toml`, under `[tool.conda.tasks]` or `[tool.pixi.tasks]`
according to the same precedence rules as reading).

When both `[tool.conda]` and `[tool.pixi]` exist in the same
`pyproject.toml`, the entire `[tool.conda]` table takes precedence. This
means that if `[tool.conda]` has any content (e.g. workspace settings)
but no `[tool.conda.tasks]`, tasks from `[tool.pixi.tasks]` will not be
loaded. To use pixi tasks, either remove `[tool.conda]` entirely or
define your tasks under `[tool.conda.tasks]`.

When a file defines both workspace and task sections, both are used.

(user-level-tasks)=

## User-level tasks

Tasks can also be defined in a user-level file that applies across all
projects. This is useful for personal utility tasks (formatting, linting,
cleanup) that you want available everywhere without repeating them in
every project manifest.

### User task file search order

1. `$XDG_CONFIG_HOME/conda/tasks.toml` (if `XDG_CONFIG_HOME` is set)
2. `~/.config/conda/tasks.toml` (XDG default)
3. `~/.conda/tasks.toml` (legacy fallback)

First file found wins. If none exist, user-level tasks are not loaded.

The format is the same `[tasks]` table as in `conda.toml`:

```toml
[tasks]
fmt = { cmd = "ruff format .", description = "Format Python files" }
clean = { cmd = "git clean -fdx -e .conda", description = "Remove untracked files" }
check = { cmd = "ruff check --fix .", description = "Lint and auto-fix" }
```

### Merge semantics

User tasks act as a base layer beneath project tasks:

- Project tasks override user tasks on name collision (project always wins)
- User-only tasks (not defined in the project) are included as-is
- `depends-on` can reference tasks from either layer
- `conda task list` shows user tasks with a `(user)` annotation

### No project manifest required

User tasks work even without a project `conda.toml` in the current
directory. Running `conda task run fmt` in any directory executes the
user-defined task. If neither a project manifest nor a user task file
exists, `conda task run` falls back to ad-hoc command execution.

## Lockfile format

`conda workspace lock` and `conda workspace install` produce and
consume `conda.lock`, conda-workspaces' own lockfile.  It is a
derivative of rattler-lock v6 (`pixi.lock`): the YAML schema is the
same, the converters in `conda-lockfiles` are reused, and only the
on-disk `version` byte differs (`conda.lock` uses `version: 1`,
`pixi.lock` uses `version: 6`). A tool can translate between the two
lockfile names by changing that version field. A plain rename is not a
valid conversion.

See [Plugin format names and
aliases](reference/format-aliases.md) for the canonical and alias
strings accepted by `conda env create --file conda.lock` and
`conda export --format=...`.

Generated and merged lockfiles remove basic authentication, Anaconda
`/t/<token>/` path segments, queries, and fragments from channel and package
URLs. Selective updates also scrub unchanged slices copied from an older
lockfile. Regenerate an existing credential-bearing `conda.lock`, rotate any
exposed value, and keep replacement credentials in Conda's local configuration.

## File formats

### conda.toml

The conda-native format. Its core workspace, feature, environment,
dependency, and task tables follow the same shape as `pixi.toml`, but it
uses `[workspace]` exclusively (no `[project]` fallback) and can include
conda-workspaces-specific extensions. Supports both workspace and task
definitions in a single file.

```toml
[workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"
numpy = ">=1.24"

[pypi-dependencies]
my-pkg = { path = ".", editable = true }

[feature.test.dependencies]
pytest = ">=8.0"
pytest-cov = ">=4.0"

[feature.docs.dependencies]
sphinx = ">=7.0"
myst-parser = ">=3.0"

[environments]
default = []
test = { features = ["test"] }
docs = { features = ["docs"] }

[target.linux-64.dependencies]
linux-headers = ">=5.10"

[activation]
scripts = ["scripts/setup.sh"]
env = { PROJECT_ROOT = "." }

[tasks]
build = "python -m build"
test = { cmd = "pytest tests/ -v", depends-on = ["build"] }
lint = { cmd = "ruff check .", description = "Lint the code" }

[tasks.check]
depends-on = ["test", "lint"]

[target.win-64.tasks]
build = "python -m build --wheel"
```

### pixi.toml

The pixi-native format. conda-workspaces reads the workspace and task
fields documented here, including pixi-style rich platform entries:

```toml
[workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"
numpy = ">=1.24"

[feature.test.dependencies]
pytest = ">=8.0"

[environments]
default = []
test = { features = ["test"] }

[tasks]
build = "python -m build"
test = { cmd = "pytest", depends-on = ["build"] }
```

The legacy `[project]` table is also accepted (pre-workspace pixi
manifests).

### pyproject.toml

Workspace and task configuration is embedded under `[tool.conda.*]`
(preferred) or `[tool.pixi.*]`:

::::{tab-set}

:::{tab-item} tool.conda

```toml
[tool.conda.workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[tool.conda.dependencies]
python = ">=3.10"
numpy = ">=1.24"

[tool.conda.feature.test.dependencies]
pytest = ">=8.0"

[tool.conda.environments]
default = []
test = { features = ["test"] }

[tool.conda.tasks]
build = "python -m build"

[tool.conda.tasks.test]
cmd = "pytest"
depends-on = ["build"]
```

:::

:::{tab-item} tool.pixi

```toml
[tool.pixi.workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[tool.pixi.dependencies]
python = ">=3.10"

[tool.pixi.feature.test.dependencies]
pytest = ">=8.0"

[tool.pixi.environments]
default = []
test = { features = ["test"] }

[tool.pixi.tasks]
build = "python -m build"
test = { cmd = "pytest", depends-on = ["build"] }
```

:::

::::

## Workspace table

The `[workspace]` (or `[project]`) table defines workspace metadata:

| Field | Type | Description |
|---|---|---|
| `name` | string | Workspace name (optional, defaults to directory name) |
| `version` | string | Workspace version (optional) |
| `description` | string | Short description (optional) |
| `channels` | list of strings | Conda channels, in priority order |
| `platforms` | list of strings or rich platform tables | Supported platforms (e.g. `linux-64`, `osx-arm64`, `{ platform = "linux-64", cuda = "12.0" }`) |
| `channel-priority` | string | Channel priority mode: `strict`, `flexible`, or `disabled` |

### Channel authentication

Keep credentials out of workspace manifests. Store channel names or
credential-free channel URLs in `channels`, then configure authentication
through Conda so credentials remain in local user configuration.

`conda workspace init` and generated `quickstart` workspaces remove basic
authentication, Anaconda `/t/<token>/` path segments, queries, and fragments
from inherited channel URLs. This includes relative
`t/<token>/<channel>` configured values, which become credential-free absolute
channel URLs. Scheme-relative values such as `//repo.example/conda` become
explicit HTTPS URLs. Manifest mutation and import commands reject exact package
and PyPI source URLs containing sensitive values because silently removing them
could change the selected artifact. A mutation also fails when embedded URL
credentials remain elsewhere in the manifest.

For an existing workspace, remove embedded credentials from the manifest,
rotate any value that may have been committed or shared, and configure the
replacement credential outside the repository. Machine-readable workspace
information also redacts credentials before returning dependency URLs.

Manifest parsing is limited to 16 MiB, 128 nested collection levels, 100,000
items in one collection, and 1,000,000 aggregate collection items. Lockfiles use
the same structural limits with a 128 MiB byte limit, and archive receipts use a
64 MiB byte limit. Split an oversized trusted document or reduce deeply nested
and very large collections before retrying.

## Dependencies

Dependencies use conda match-spec syntax:

```toml
[dependencies]
python = ">=3.10"
numpy = ">=1.24,<2"
scipy = "*"          # any version
cuda-toolkit = { version = ">=12", build = "*cuda*" }
```

Shared conda specs can live under `[workspace.dependencies]`. Any conda
dependency table can opt into a shared spec with `{ workspace = true }`.
The consuming entry may add non-version fields such as `build` or
`channel`. This matches the workspace dependency inheritance syntax
that pixi added in 0.70.0.

```toml
[workspace.dependencies]
numpy = "1.*"
cmake = { version = ">=3.28", channel = "conda-forge" }

[dependencies]
python = ">=3.12"
numpy = { workspace = true }

[feature.build.dependencies]
cmake = { workspace = true, build = "h*" }
```

### Dependency mutation locations

`conda workspace add`, `conda workspace update`, and
`conda workspace remove` address one explicitly selected declaration
instead of searching for the composed winner. See the
{ref}`dependency mutation rules <dependency-mutation-rules>` for the
selector mapping, inheritance behavior, and wrong-location diagnostics.

## Feature table

Each `[feature.<name>]` table can contain:

| Field | Type | Description |
|---|---|---|
| `dependencies` | table | Conda dependencies for this feature |
| `pypi-dependencies` | table | PyPI dependencies for this feature |
| `channels` | list | Additional channels for this feature |
| `platforms` | list | Platform restrictions for this feature |
| `system-requirements` | table | System-level requirements |
| `activation.scripts` | list | Activation scripts |
| `activation.env` | table | Environment variables set on activation |
| `target` | table | Per-platform dependency overrides for this feature |
| `tasks` | table | Tasks contributed by this feature |

## Environments table

Each entry in `[environments]` defines a named environment:

| Field | Type | Description |
|---|---|---|
| `features` | list of strings | Features to include (in addition to default) |
| `solve-group` | string | Accepted for pixi compatibility. Currently ignored by conda-workspaces |
| `no-default-feature` | bool | Exclude the default feature (default: false) |
| `dependencies` | table | Conda dependencies private to this environment |
| `pypi-dependencies` | table | PyPI dependencies private to this environment |
| `target` | table | Per-platform private dependency overrides for this environment |

Shorthand forms are supported:

```toml
[environments]
# Full form
test = { features = ["test"] }

# Features only
lint = ["lint"]

# Default environment shorthand
default = []
```

Environment targets are merged after the environment's unqualified
private dependencies:

```toml
[environments.test]
features = ["test"]

[environments.test.target.win-64.dependencies]
pywin32 = "*"

[environments.test.target.win-64.pypi-dependencies]
colorama = ">=0.4"
```

## Task fields

| Field | Type | Description |
|---|---|---|
| `cmd` | `string` or `list[string]` | Command to execute. Omit for aliases. |
| `args` | `list` | Named arguments with optional defaults. |
| `depends-on` | `list` | Tasks to run before this one. |
| `cwd` | `string` | Working directory for the task. |
| `env` | `dict` | Environment variables to set. |
| `description` | `string` | Human-readable description. |
| `inputs` | `list[string]` | Glob patterns for cache inputs. |
| `outputs` | `list[string]` | Glob patterns for cache outputs. |
| `clean-env` | `bool` | Run with minimal environment variables. |
| `default-environment` | `string` | Conda environment to activate by default. |
| `target` | `dict` | Per-platform overrides (keys are platform strings). |

## Task argument definitions

```toml
[tasks.test]
cmd = "pytest {{ path }} {{ flags }}"
args = [
  { arg = "path", default = "tests/" },
  { arg = "flags", default = "-v" },
]
```

## Task dependency definitions

Simple list:

```toml
[tasks.check]
depends-on = ["compile", "lint"]
```

With arguments:

```toml
[tasks.check]
depends-on = [
  { task = "test", args = ["tests/unit/"] },
]
```

With environment:

```toml
[tasks.check]
depends-on = [
  { task = "test", environment = "py311" },
]
```

(archive-configuration)=

## Archive configuration

The `[workspace.archive]` table controls which files are included and
excluded when creating archives with `conda workspace archive`, as well
as the compression format.

```toml
[workspace.archive]
include = ["src/**", "conda.toml", "conda.lock"]
exclude = ["*.log", "data/raw/**"]
compression = "zst"
compression-level = 19
```

| Field | Type | Description |
|---|---|---|
| `include` | list of strings | Glob patterns for files to include in archives. When set, only matching files are archived. |
| `exclude` | list of strings | Glob patterns for files to exclude from archives |
| `compression` | string | Compression algorithm: `"zst"` (default), `"gz"`, or `"bz2"` |
| `compression-level` | integer | Compression level (algorithm-dependent, omit for library default) |

When both `include` and `exclude` are set, `include` patterns narrow
the file set first, then `exclude` patterns remove from that set. When
`include` is empty (the default), all files are candidates.

Patterns use `fnmatch` matching against paths relative to the
workspace root. Both directory globs (`docs/**`) and file globs
(`*.log`) are supported.

Built-in exclusions always apply regardless of this setting:

- `.git`
- `.conda/envs`
- `.pixi`
- `__pycache__`
- common credential material such as `.env`, `.ssh`, `.aws`, `.azure`,
  `.config/gcloud`, `.docker`, `.gnupg`, `.kube`, `.terraform`,
  `.npmrc`, `.pypirc`, `.netrc`, private keys, certificate/key bundles,
  and Terraform state files

Dotenv templates such as `.env.example`, `.env.sample`, `.env.template`,
and `.env.dist` remain eligible for archives unless excluded explicitly.

Archive inspection and extraction reject GNU sparse tar members. Repack a
sparse input as ordinary files before using it as a workspace archive.

CLI `--exclude` flags are combined with manifest exclusions. In git
repos, only tracked files are considered regardless of exclusion
patterns.

### Receipt-compatible archive filters

`conda workspace archive --receipt` requires the workspace manifest and
`conda.lock` to be included in the archive because the receipt binds and
later verifies both files. If you set `include`, make sure those files
are part of the allowlist:

```toml
[workspace.archive]
include = ["conda.toml", "conda.lock", "src/**"]
```

Do not exclude the manifest or lockfile when writing receipts:

```toml
[workspace.archive]
exclude = ["docs/**", "*.log", "data/raw/**"]
```

If manifest settings or CLI `--exclude` patterns would remove either
file, receipt creation fails before writing the archive or receipt. Run
without `--receipt` only when you intentionally want an archive that is
not receipt-verifiable.
