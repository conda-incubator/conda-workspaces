# conda-workspaces

[![License](https://img.shields.io/github/license/conda-incubator/conda-workspaces)](https://github.com/conda-incubator/conda-workspaces/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.14-blue)](https://github.com/conda-incubator/conda-workspaces)

Project-scoped multi-environment workspace management for conda, with pixi
manifest compatibility.

Define environments in your project manifest, compose them from reusable
features, and let conda handle the solving and installation. Works with
existing pixi manifests -- no new package manager required.

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
```

Install and manage your environments:

```console
$ cw install           # solve + install + generate conda.lock
$ cw run -e test pytest
$ cw list              # list packages in default env
$ cw list --envs       # list defined environments
$ cw install --locked  # reproducible install from conda.lock
```

## Why?

Conda handles environments and packages. [pixi](https://pixi.sh) introduced
a great project manifest format with multi-environment workspaces, but it
brings its own solver and environment management.

conda-workspaces reads pixi-compatible manifests and delegates solving and
installation to conda's own infrastructure. You get workspace management
inside the conda CLI without switching tools.

## What it does

- Reads `pixi.toml`, `conda.toml`, and `pyproject.toml` workspace manifests
- Multi-environment support with composable features
- Project-local environments in `.conda/envs/`
- Lockfile generation (`conda.lock`) in rattler-lock v6 format for reproducible installs
- Per-platform dependency overrides via `[target.<platform>]`
- PyPI dependencies translated and resolved alongside conda packages via conda-pypi
- Activation scripts and environment variables per feature
- System requirements as virtual package constraints
- Per-workspace channel priority override
- Standalone `cw` CLI and `conda workspace` plugin subcommand

## Installation

```bash
conda install -c conda-forge conda-workspaces
```

## CLI

conda-workspaces registers as `conda workspace` and provides `cw` as a
standalone shortcut.

| Command | Description |
|---|---|
| `cw init` | Initialize a new workspace manifest |
| `cw install` | Create/update workspace environments |
| `cw install --locked` | Install from lockfile (skip solving) |
| `cw lock` | Generate/update `conda.lock` |
| `cw list` | List packages in an environment |
| `cw list --envs` | List defined environments |
| `cw info [ENV]` | Show environment details |
| `cw add SPECS...` | Add dependencies |
| `cw remove SPECS...` | Remove dependencies |
| `cw run [-e ENV] CMD` | Run a task or command in an environment |
| `cw shell [ENV]` | Spawn a shell with an environment activated |
| `cw activate [ENV]` | Print activation instructions |
| `cw clean` | Remove installed environments |

## What it doesn't do

conda-workspaces is a workspace manager, not a package manager replacement.
It does not bundle its own solver or bypass conda's installation machinery.
If you want a fully integrated tool that handles both, see
[pixi](https://pixi.sh).

## Documentation

https://conda-incubator.github.io/conda-workspaces/

## Development

```bash
pixi install
pixi run test
pixi run lint
```

## Acknowledgements

The workspace and manifest system in conda-workspaces is directly inspired by
the work of the [prefix.dev](https://prefix.dev) team on
[pixi](https://github.com/prefix-dev/pixi). Their design of workspace
manifests, features, environments, and platform targeting provided the blueprint
for this plugin.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
