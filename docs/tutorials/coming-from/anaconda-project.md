# Coming from anaconda-project

[anaconda-project](https://github.com/anaconda/anaconda-project) was
the original tool for encapsulating conda-based projects with
environments, commands, variables, downloads, and services. It has been
effectively sunset, with
[conda-project](https://github.com/conda-incubator/conda-project) as
its community successor and conda-workspaces continuing that evolution.

If you are migrating from anaconda-project, this guide maps its
concepts and commands to conda-workspaces equivalents.

:::{tip}
Also coming from conda-project? See
[Coming from conda-project](conda-project.md) for the
intermediate step in this lineage.
:::

## Automatic conversion

Use the built-in import command to convert your manifest:

```bash
conda workspace import anaconda-project.yml
```

This reads your `anaconda-project.yml` and writes a `conda.toml`
with packages, env_specs (as features), commands (as tasks), variables,
and downloads converted. Use `--dry-run` to preview the output without
writing a file, or `-o custom.toml` to choose a different output path.

Review the output and adjust as needed — some concepts like services
and interactive variable prompting have no direct equivalent.

## Key differences

| Aspect | anaconda-project | conda-workspaces |
|---|---|---|
| Manifest | `anaconda-project.yml` | `conda.toml` (or `pixi.toml` / `pyproject.toml`) |
| Lockfile | none (re-solves each time) | `conda.lock` (rattler-lock v6) |
| Environment location | `envs/<name>/` | `.conda/envs/<name>/` |
| Multiple environments | `env_specs:` (separate dependency lists) | composable features in one manifest |
| Commands | `commands:` (flat, typed: bokeh/notebook/unix) | `[tasks]` with dependency graphs, caching, templates |
| Variables | `variables:` with prompting | Jinja2 templates + environment variables |
| Downloads | `downloads:` (auto-fetched files) | not supported (use tasks instead) |
| Services | `services:` (e.g. Redis) | not supported (use tasks or Docker instead) |
| Archives | `anaconda-project archive` | not supported |
| Solver | conda | conda / libmamba |
| pixi compatibility | no | reads `pixi.toml` and `pyproject.toml` |

## Command mapping

| anaconda-project | conda-workspaces |
|---|---|
| `anaconda-project init` | `conda workspace init` |
| `anaconda-project add-packages numpy` | `conda workspace add numpy` |
| `anaconda-project remove-packages numpy` | `conda workspace remove numpy` |
| `anaconda-project add-command name "cmd"` | add to `[tasks]` in `conda.toml` (or `conda task add name "cmd"`) |
| `anaconda-project list-commands` | `conda task list` |
| `anaconda-project run` | `conda task run <task>` |
| `anaconda-project run <command>` | `conda task run <task>` or `conda workspace run -- <cmd>` |
| `anaconda-project add-variable FOO` | use `{{ env.FOO }}` in task templates |
| `anaconda-project add-env-spec test` | add `[feature.test.dependencies]` + `[environments]` |
| `anaconda-project prepare` | `conda workspace install` |
| `anaconda-project clean` | `conda workspace clean` |
| `anaconda-project lock` | `conda workspace lock` |

## Migrating the manifest

A typical `anaconda-project.yml`:

```yaml
name: my-analysis

commands:
  notebook:
    notebook: analysis.ipynb
  plot:
    bokeh_app: dashboard
  process:
    unix: python process.py

packages:
  - python=3.10
  - numpy
  - pandas
  - bokeh

channels:
  - conda-forge

env_specs:
  default:
    packages:
      - python=3.10
      - numpy
      - pandas
      - bokeh
  test:
    packages:
      - python=3.10
      - numpy
      - pandas
      - pytest

variables:
  DATA_DIR:
  API_KEY:

downloads:
  IRIS_DATA:
    url: https://example.com/iris.csv
```

The equivalent `conda.toml`:

```toml
[workspace]
name = "my-analysis"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = "3.10.*"
numpy = "*"
pandas = "*"
bokeh = "*"

[feature.test.dependencies]
pytest = "*"

[environments]
default = []
test = { features = ["test"] }

[tasks]
notebook = "jupyter notebook analysis.ipynb"
plot = "bokeh serve dashboard"
process = "python process.py"
download-data = "curl -o iris.csv https://example.com/iris.csv"
```

## Environment specs to features

anaconda-project uses `env_specs:` with fully duplicated dependency
lists per environment. conda-workspaces uses composable features
where each environment inherits base dependencies and adds its own:

```yaml
# anaconda-project.yml
env_specs:
  default:
    packages:
      - python=3.10
      - numpy
      - pandas
  test:
    packages:
      - python=3.10
      - numpy
      - pandas
      - pytest
```

```toml
# conda.toml — no duplication needed
[dependencies]
python = "3.10.*"
numpy = "*"
pandas = "*"

[feature.test.dependencies]
pytest = "*"

[environments]
default = []
test = { features = ["test"] }
```

The `test` environment automatically inherits all base dependencies.

## Variables

anaconda-project prompts for missing variables and supports defaults
in the manifest. conda-workspaces uses Jinja2 templates in task
commands and reads from the shell environment:

```yaml
# anaconda-project.yml
variables:
  DATA_DIR: /data
  API_KEY:
```

```toml
# conda.toml
[tasks]
process = "python process.py --data {{ env.DATA_DIR | default('/data') }}"
```

For required variables without defaults, the task will fail with a
clear Jinja2 error if the variable is not set — there is no
interactive prompting.

## Commands to tasks

anaconda-project commands are typed (unix, notebook, bokeh_app) and
flat. conda-workspaces tasks are shell commands with dependency
graphs, caching, and descriptions:

```yaml
# anaconda-project.yml
commands:
  process:
    unix: python process.py
  test:
    unix: pytest tests/ -v
  check:
    unix: |
      ruff check src/
      pytest tests/ -v
```

```toml
# conda.toml
[tasks]
process = "python process.py"
lint = "ruff check src/"
test = { cmd = "pytest tests/ -v", depends-on = ["lint"] }

[tasks.check]
depends-on = ["lint", "test"]
description = "Run all checks"
```

The multi-line `check` command in anaconda-project becomes a proper
dependency graph — `conda task run check` runs lint before test, and
each step can be cached independently.

For notebook and Bokeh app commands, use the equivalent shell commands
(`jupyter notebook`, `bokeh serve`) in task definitions.

## Downloads and services

anaconda-project can automatically download files and start services
like Redis. conda-workspaces does not have built-in equivalents, but
tasks can fill the role:

```toml
[tasks]
download-data = { cmd = "curl -o data/iris.csv https://example.com/iris.csv", description = "Download dataset" }
start-redis = { cmd = "redis-server --daemonize yes", description = "Start Redis" }

[tasks.process]
cmd = "python process.py"
depends-on = ["download-data"]
```

For services, consider using Docker Compose or similar tools alongside
your workspace.

## What's not carried over

Some anaconda-project concepts have no direct equivalent:

- `anaconda-project archive` — project archives are not supported
- Interactive variable prompting — use environment variables or
  Jinja2 defaults instead
- `services:` — use tasks, Docker Compose, or external service
  management
- `downloads:` — use tasks with `curl`/`wget` and input/output caching
- Bokeh/notebook command types — use the equivalent shell commands

## What's new in conda-workspaces

Beyond what anaconda-project offered, conda-workspaces adds:

- Task dependency graphs with topological ordering
- Input/output caching for tasks (skip unchanged work)
- Jinja2 templates in task commands
- Per-platform task and dependency overrides
- Lockfiles for exact reproducibility across machines
- pixi manifest compatibility (`pixi.toml` and `pyproject.toml`)
- `conda env create --file conda.toml` and `conda env create --file
  conda.lock` integration via environment spec plugins
- Standalone CLIs (`cw` / `ct`) alongside the conda plugin

## Next steps

- [Your first project](../first-project.md) — full walkthrough
- [Coming from conda-project](conda-project.md) — the
  intermediate successor
- [Features](../../features.md) — environments, tasks, caching, templates
