# conda-workspaces

Project-scoped multi-environment workspace management for conda, with pixi
manifest compatibility.

Define your environments, features, and dependencies in a single manifest.
conda-workspaces reads `conda.toml`, `pixi.toml`, or `pyproject.toml` and
delegates solving and installation to conda — no extra solver, no new
package manager, just workspaces on top of the tools you already use.

## Install

::::{tab-set}

:::{tab-item} conda

```bash
conda install -c conda-forge conda-workspaces
```

:::

:::{tab-item} pixi

```bash
pixi global install conda-workspaces
```

:::

::::

## Define a workspace

![quickstart demo](../demos/quickstart.gif)

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

[environments]
default = []
test = { features = ["test"] }
```

Then install and use your environments:

```bash
cw install                    # solve + install + generate conda.lock
cw run -e test pytest -v      # run a command in an environment
cw shell -e test              # spawn a shell with test env activated
cw install --locked           # reproducible install from conda.lock
cw list                       # list packages in default env
cw info                       # workspace overview
```

Environments are standard conda prefixes stored in `.conda/envs/` inside
your project directory. They work with `conda activate` and all existing
conda tooling.

## Why conda-workspaces?

[pixi](https://pixi.sh) introduced an excellent workspace model for
managing multi-environment projects, but it brings its own solver and
installation machinery. conda-workspaces reuses that same manifest format
while delegating all solving and installation to conda's existing
infrastructure.

This means:

- Workspaces read from `pixi.toml`, `conda.toml`, or `pyproject.toml` —
  one manifest, multiple tools
- Environments are solved by conda / libmamba and installed as regular
  conda prefixes
- Lock files (`conda.lock`) capture exact package URLs for reproducible
  installs without re-solving
- Composable features, platform overrides, and PyPI
  dependencies all work out of the box
- Ships as a conda plugin (`conda workspace`) and a standalone `cw` CLI

Read more in [](motivation.md).

---

::::{grid} 2
:gutter: 3

:::{grid-item-card} {octicon}`rocket` Getting started
:link: quickstart
:link-type: doc

Set up your first workspace in under a minute.
:::

:::{grid-item-card} {octicon}`mortar-board` Tutorials
:link: tutorials/index
:link-type: doc

Step-by-step guides: your first workspace, migrating from pixi, CI setup.
:::

:::{grid-item-card} {octicon}`list-unordered` Features
:link: features
:link-type: doc

Environments, features, platform overrides, PyPI dependencies,
and more.
:::

:::{grid-item-card} {octicon}`gear` Configuration
:link: configuration
:link-type: doc

All manifest fields and file formats (`conda.toml`, `pixi.toml`,
`pyproject.toml`).
:::

:::{grid-item-card} {octicon}`terminal` CLI reference
:link: reference/cli
:link-type: doc

Complete `conda workspace` command-line documentation.
:::

:::{grid-item-card} {octicon}`code` API reference
:link: reference/api
:link-type: doc

Python API for models, parsers, resolver, context, and environments.
:::

::::

```{toctree}
:hidden:

quickstart
tutorials/index
features
configuration
reference/cli
reference/api
motivation
changelog
```
