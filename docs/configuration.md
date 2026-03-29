# Configuration

conda-workspaces searches for workspace manifests in the current directory
and its parents. The first matching file is used.

## Search order

1. `conda.toml` — conda-native workspace manifest
2. `pixi.toml` — pixi-native format (full compatibility)
3. `pyproject.toml` — embedded under `[tool.conda.*]`, `[tool.conda-workspaces.*]` (legacy), or `[tool.pixi.*]`

## File formats

### conda.toml

The conda-native format. Structurally identical to `pixi.toml` but uses
`[workspace]` exclusively (no `[project]` fallback).

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
default = { solve-group = "default" }
test = { features = ["test"], solve-group = "default" }
docs = { features = ["docs"] }

[target.linux-64.dependencies]
linux-headers = ">=5.10"

[activation]
scripts = ["scripts/setup.sh"]
env = { PROJECT_ROOT = "." }
```

### pixi.toml

The pixi-native format. conda-workspaces reads this with full
compatibility for workspace-related fields:

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
default = { solve-group = "default" }
test = { features = ["test"], solve-group = "default" }
```

The legacy `[project]` table is also accepted (pre-workspace pixi
manifests).

### pyproject.toml

Workspace configuration is embedded under `[tool.conda.*]` (preferred),
`[tool.conda-workspaces.*]` (legacy), or `[tool.pixi.*]`:

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
default = { solve-group = "default" }
test = { features = ["test"], solve-group = "default" }
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
default = { solve-group = "default" }
test = { features = ["test"], solve-group = "default" }
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
| `solve-group` | string | Solve-group for version coordination |
| `no-default-feature` | bool | Exclude the default feature (default: false) |

Shorthand forms are supported:

```toml
[environments]
# Full form
test = { features = ["test"], solve-group = "default" }

# Features only
lint = ["lint"]

# Solve-group only
default = { solve-group = "default" }
```
