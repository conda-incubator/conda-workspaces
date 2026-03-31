# Coming from conda-project

[conda-project](https://github.com/conda-incubator/conda-project) and
conda-workspaces both provide project-scoped conda environments. If you
are migrating from conda-project, this guide maps the concepts and
commands to their conda-workspaces equivalents.

## Key differences

| Aspect | conda-project | conda-workspaces |
|---|---|---|
| Manifest | `conda-project.yml` + `environment.yml` | single `conda.toml` (or `pixi.toml` / `pyproject.toml`) |
| Lockfile | `conda-lock.<env>.yml` (conda-lock) | `conda.lock` (rattler-lock v6) |
| Environment location | `envs/<name>/` | `.conda/envs/<name>/` |
| Multiple environments | separate `environment.yml` files | composable features in one manifest |
| Commands | flat `commands:` map | `[tasks]` with dependency graphs, caching, templates |
| Variables | `variables:` with `.env` file | Jinja2 templates + environment variables |
| Solver | conda / conda-lock | conda / libmamba |
| pixi compatibility | no | reads `pixi.toml` and `pyproject.toml` |
| Platform overrides | per-platform lock only | per-platform dependencies and tasks |

## Command mapping

| conda-project | conda-workspaces |
|---|---|
| `conda project init` | `conda workspace init` |
| `conda project add numpy` | `conda workspace add numpy` |
| `conda project add @pip::requests` | `conda workspace add --pypi requests` |
| `conda project remove numpy` | `conda workspace remove numpy` |
| `conda project lock` | `conda workspace lock` |
| `conda project activate` | `conda workspace shell` |
| `conda project run <cmd>` | `conda task run <task>` or `conda workspace run -- <cmd>` |
| `conda project clean` | `conda workspace clean` |

## Automatic conversion

Use the built-in import command to convert your manifest:

```bash
conda workspace import conda-project.yml
```

This reads your `conda-project.yml` (and the `environment.yml` files
it references) and writes a single `conda.toml` with all dependencies,
environments, and commands converted. Use `--dry-run` to preview the
output, or `-o custom.toml` to choose a different output path.

## Migrating the manifest

A typical conda-project setup has two files:

```yaml
# conda-project.yml
name: my-project
environments:
  default:
    - environment.yml
variables:
  DATABASE_URL:
commands:
  test: pytest tests/ -v
  serve: python -m http.server
```

```yaml
# environment.yml
name: my-project
channels:
  - conda-forge
dependencies:
  - python>=3.10
  - numpy>=1.24
  - pip:
    - requests
```

The equivalent single `conda.toml`:

```toml
[workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"
numpy = ">=1.24"

[pypi-dependencies]
requests = "*"

[tasks]
test = "pytest tests/ -v"
serve = "python -m http.server"
```

## Multiple environments

conda-project uses separate `environment.yml` files for each
environment, referenced from `conda-project.yml`:

```yaml
# conda-project.yml
environments:
  default:
    - environment.yml
  dev:
    - environment.yml
    - dev-extras.yml
```

conda-workspaces uses composable features instead:

```toml
[dependencies]
python = ">=3.10"
numpy = ">=1.24"

[feature.dev.dependencies]
pytest = ">=8.0"
ruff = ">=0.4"

[environments]
default = []
dev = { features = ["dev"] }
```

Features compose automatically — `dev` inherits all base dependencies
and adds its own. No need for separate files or duplicate entries.

## Variables and environment

conda-project has a `variables:` block with optional defaults and
`.env` file support:

```yaml
variables:
  FOO: has-default-value
  BAR:
```

conda-workspaces uses Jinja2 templates in task commands and standard
environment variables:

```toml
[tasks]
deploy = "deploy --env {{ env.DEPLOY_TARGET | default('staging') }}"
```

For simple cases, set variables in your shell or `.env` file before
running tasks.

## Commands to tasks

conda-project commands are flat name-to-command mappings. conda-workspaces
tasks add dependency resolution, caching, and descriptions:

```yaml
# conda-project.yml
commands:
  test: pytest tests/ -v
  lint: ruff check src/
```

```toml
# conda.toml
[tasks]
test = { cmd = "pytest tests/ -v", depends-on = ["lint"] }
lint = "ruff check src/"

[tasks.check]
depends-on = ["test", "lint"]
description = "Run all checks"
```

Running `conda task run check` automatically resolves the dependency
graph and runs lint before test.

## Lockfiles

conda-project generated separate `conda-lock.<env>.yml` files using
conda-lock. conda-workspaces generates a single `conda.lock` in
rattler-lock v6 format that covers all environments and platforms:

```bash
conda workspace lock                   # solve only, write conda.lock
conda workspace install                # solve, lock, and install
conda workspace install --locked       # install from lockfile (validates freshness)
conda workspace install --frozen       # install from lockfile (skip freshness check)
```

conda-workspaces registers environment spec plugins so that standard
conda commands understand both workspace manifests and lockfiles:

```bash
conda env create --file conda.toml -n myenv    # solve and create from manifest
conda env create --file conda.lock -n myenv    # install exact lockfile contents
```

The companion [conda-lockfiles](https://github.com/conda/conda-lockfiles)
plugin (installed as a dependency) adds the same `conda env create`
support for other lockfile formats. If you still have `conda-lock.yml`
files from conda-project, they work too:

```bash
conda env create --file conda-lock.yml -n myenv    # via conda-lockfiles plugin
```

| Plugin | Files | Format |
|---|---|---|
| conda-workspaces | `conda.toml` | workspace manifest |
| conda-workspaces | `conda.lock` | rattler-lock v6 |
| conda-lockfiles | `pixi.lock` | rattler-lock v6 |
| conda-lockfiles | `conda-lock.yml` | conda-lock v1 |

## What's new in conda-workspaces

Beyond what conda-project offered, conda-workspaces adds:

- Task dependency graphs with topological ordering
- Input/output caching for tasks (skip unchanged work)
- Jinja2 templates in task commands
- Per-platform task and dependency overrides
- pixi manifest compatibility (`pixi.toml` and `pyproject.toml`)
- A single lockfile covering all environments and platforms
- Standalone CLIs (`cw` / `ct`) alongside the conda plugin

## Next steps

- [Your first project](../first-project.md) — full walkthrough
- [Coming from pixi](pixi.md) — if you also use pixi
- [Features](../../features.md) — environments, tasks, caching, templates
