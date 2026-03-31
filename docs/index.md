# conda-workspaces

Project-scoped multi-environment workspaces and task runner for conda,
with pixi manifest compatibility.

Define environments, features, dependencies, and tasks in a single manifest.
conda-workspaces reads `conda.toml`, `pixi.toml`, or `pyproject.toml` and
delegates solving and installation to conda — no extra solver, no new
package manager, just workspaces and tasks on top of the tools you already use.

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
conda workspace install                    # solve + install + generate conda.lock
conda workspace run -e test -- pytest -v   # run a command in an environment
conda workspace shell -e test              # spawn a shell with test env activated
conda workspace install --locked           # reproducible install from conda.lock
conda workspace list                       # list packages in default env
conda workspace envs                       # list defined environments
conda workspace info                       # workspace overview
```

## Define tasks

![task quickstart demo](../demos/task-quickstart.gif)

Add tasks to the same manifest:

```toml
[tasks]
test = { cmd = "pytest tests/ -v", depends-on = ["build"] }
build = "python -m build"
lint = "ruff check ."

[tasks.check]
depends-on = ["test", "lint"]
```

Then run them:

```bash
conda task run check        # resolves dependencies, runs build → lint → test
conda task list             # shows all available tasks
conda task run test         # builds first, then tests
```

Tasks run in your current conda environment by default, or target a
workspace environment with `-e myenv`.

## Why conda-workspaces?

[pixi](https://pixi.sh) introduced an excellent project model for
managing multi-environment workspaces and tasks, but it brings its own
solver and installation machinery. conda-workspaces reuses that same
manifest format while delegating all solving and installation to conda's
existing infrastructure.

This means:

- Workspaces and tasks read from `conda.toml`, `pixi.toml`, or
  `pyproject.toml` — one manifest, multiple tools
- Environments are solved by conda / libmamba and installed as regular
  conda prefixes
- Lock files (`conda.lock`) capture exact package URLs for reproducible
  installs without re-solving
- Task dependencies, caching, Jinja2 templates, and platform overrides
  all work out of the box
- Ships as a conda plugin (`conda workspace`, `conda task`) and
  standalone `conda workspace` / `conda task` CLIs (also available as `cw` / `ct`)

Read more in [](motivation.md).

---

::::{grid} 2
:gutter: 3

:::{grid-item-card} {octicon}`rocket` Getting started
:link: quickstart
:link-type: doc

Set up your first workspace and tasks in under a minute.
:::

:::{grid-item-card} {octicon}`mortar-board` Tutorials
:link: tutorials/index
:link-type: doc

Your first project, migrating from conda / pixi / anaconda-project / conda-project, CI setup.
:::

:::{grid-item-card} {octicon}`list-unordered` Features
:link: features
:link-type: doc

Environments, features, platform overrides, PyPI dependencies,
task dependencies, caching, templates, and more.
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

Complete `conda workspace` and `conda task` command-line documentation.
:::

:::{grid-item-card} {octicon}`code` API reference
:link: reference/api
:link-type: doc

Python API for models, parsers, resolver, context, environments,
and task execution.
:::

::::

```{toctree}
:hidden:
:caption: Tutorials

quickstart
tutorials/index
```

```{toctree}
:hidden:
:caption: Reference

reference/cli
reference/api
configuration
```

```{toctree}
:hidden:
:caption: Explanation

features
motivation
```

```{toctree}
:hidden:
:caption: Project

changelog
```
