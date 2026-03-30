# conda-workspaces

[![Tests](https://github.com/conda-incubator/conda-workspaces/actions/workflows/tests.yml/badge.svg)](https://github.com/conda-incubator/conda-workspaces/actions/workflows/tests.yml)
[![Docs](https://github.com/conda-incubator/conda-workspaces/actions/workflows/docs.yml/badge.svg)](https://conda-incubator.github.io/conda-workspaces/)
[![PyPI](https://img.shields.io/pypi/v/conda-workspaces)](https://pypi.org/project/conda-workspaces/)
[![conda-forge](https://img.shields.io/conda-forge/vn/conda-workspaces)](https://anaconda.org/conda-forge/conda-workspaces)
[![License](https://img.shields.io/github/license/conda-incubator/conda-workspaces)](https://github.com/conda-incubator/conda-workspaces/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.14-blue)](https://github.com/conda-incubator/conda-workspaces)

Project-scoped multi-environment workspaces and task runner for conda,
with pixi manifest compatibility.

Define environments and tasks in your project manifest, compose
environments from reusable features, and let conda handle the solving and
installation. Works with existing pixi manifests -- no new package manager
required.

## Quick start

![quickstart demo](demos/quickstart.gif)

Create a `conda.toml` in your project root:

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
pytest-cov = ">=4.0"

[environments]
test = { features = ["test"] }

[tasks]
test = { cmd = "pytest tests/ -v", description = "Run the test suite" }
lint = "ruff check ."

[tasks.check]
depends-on = ["test", "lint"]
```

Install and manage your environments, then run tasks:

```console
$ conda workspace install              # solve + install + generate conda.lock
$ conda workspace envs                 # list defined environments
$ conda task run check                 # runs lint and test in dependency order
$ conda workspace shell -e test        # spawn a shell with test env activated
$ conda workspace install --locked     # reproducible install from conda.lock
```

## Why?

Conda handles environments and packages. [pixi](https://pixi.sh) introduced
a great project model with multi-environment workspaces and a task runner,
but it brings its own solver and environment management.

conda-workspaces reads pixi-compatible manifests and delegates solving and
installation to conda's own infrastructure. You get workspace management
and task running inside the conda CLI without switching tools.

## What it does

- Reads `conda.toml`, `pixi.toml`, and `pyproject.toml` workspace manifests
- Multi-environment support with composable features
- Project-local environments in `.conda/envs/`
- Lockfile generation (`conda.lock`) in rattler-lock v6 format for reproducible installs
- Per-platform dependency overrides via `[target.<platform>]`
- PyPI dependencies translated and resolved alongside conda packages via conda-pypi
- Activation scripts and environment variables per feature
- System requirements as virtual package constraints
- Per-workspace channel priority override
- Task dependencies with topological ordering (`depends-on`)
- Jinja2 templates in commands (`{{ conda.platform }}`, conditionals)
- Task arguments with defaults, input/output caching, and per-platform overrides
- `conda workspace` / `conda task` subcommands (also available as `cw` / `ct` aliases)

## Installation

```bash
conda install -c conda-forge conda-workspaces
```

## CLI

conda-workspaces registers `conda workspace` and `conda task` subcommands,
and also provides `cw` and `ct` as shorter aliases.

### Workspace commands

| Command | Description |
|---|---|
| `conda workspace init` | Initialize a new workspace manifest |
| `conda workspace install` | Create/update workspace environments |
| `conda workspace install --locked` | Install from lockfile (skip solving) |
| `conda workspace lock` | Generate/update `conda.lock` |
| `conda workspace list` | List packages in an environment |
| `conda workspace envs` | List defined environments |
| `conda workspace info [ENV]` | Show environment details |
| `conda workspace add SPECS...` | Add dependencies |
| `conda workspace remove SPECS...` | Remove dependencies |
| `conda workspace shell [ENV]` | Spawn a shell with an environment activated |
| `conda workspace activate [ENV]` | Print activation instructions |
| `conda workspace clean` | Remove installed environments |

### Task commands

| Command | Description |
|---|---|
| `conda task run TASK` | Run a task (with dependency resolution) |
| `conda task list` | List available tasks |
| `conda task add NAME CMD` | Add a task to the manifest |
| `conda task remove NAME` | Remove a task from the manifest |
| `conda task export` | Export tasks to `conda.toml` format |

## What it doesn't do

conda-workspaces is a workspace manager and task runner, not a package
manager replacement. It does not bundle its own solver or bypass conda's
installation machinery. If you want a fully integrated tool that handles
everything including its own solver, see [pixi](https://pixi.sh).

## Documentation

https://conda-incubator.github.io/conda-workspaces/

## Demos

See [demos/README.md](demos/README.md) for animated terminal recordings
of workspace and task features.

## Development

```bash
pixi install
pixi run test
pixi run lint
```

## Acknowledgements

The workspace and task system in conda-workspaces is directly inspired by
the work of the [prefix.dev](https://prefix.dev) team on
[pixi](https://github.com/prefix-dev/pixi). Their design of workspace
manifests, features, environments, platform targeting, task dependencies,
caching, and template variables provided the blueprint for this plugin.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
