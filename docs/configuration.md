# Configuration

conda-workspaces searches for manifests in the current directory and its
parents. The first matching file is used.

## Workspace search order

1. `conda.toml` — conda-native workspace manifest
2. `pixi.toml` — pixi-native format (full compatibility)
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

## File formats

### conda.toml

The conda-native format. Structurally identical to `pixi.toml` but uses
`[workspace]` exclusively (no `[project]` fallback). Supports both
workspace and task definitions in a single file.

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

The pixi-native format. conda-workspaces reads this with full
compatibility for workspace and task fields:

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
| `platforms` | list of strings | Supported platforms (e.g. `linux-64`, `osx-arm64`) |
| `channel-priority` | string | Channel priority mode: `strict`, `flexible`, or `disabled` |

## Dependencies

Dependencies use conda match-spec syntax:

```toml
[dependencies]
python = ">=3.10"
numpy = ">=1.24,<2"
scipy = "*"          # any version
cuda-toolkit = { version = ">=12", build = "*cuda*" }
```

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

## Environments table

Each entry in `[environments]` defines a named environment:

| Field | Type | Description |
|---|---|---|
| `features` | list of strings | Features to include (in addition to default) |
| `no-default-feature` | bool | Exclude the default feature (default: false) |

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
