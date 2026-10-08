# Features

conda-workspaces adds workspaces and task execution to conda
without replacing conda's solver, package cache, or environment prefixes.
This overview explains the main feature areas and points to the detailed
explanation pages for each one.

## Choose a feature area

::::{grid} 2
:gutter: 3

:::{grid-item-card} {octicon}`terminal` Tasks
:link: features/tasks
:link-type: doc

Named shell commands with dependencies, arguments, templates,
environment targeting, clean environments, and caching.
:::

:::{grid-item-card} {octicon}`package` Environments and features
:link: features/environments
:link-type: doc

Workspace environments composed from reusable features, shared
dependencies, channels, platform overrides, PyPI dependencies, and
activation settings.
:::

:::{grid-item-card} {octicon}`lock` Locking and reproducible installs
:link: features/locking
:link-type: doc

Multi-environment, multi-platform `conda.lock` files, freshness checks,
locked installs, CI defaults, and split matrix locking.
:::

:::{grid-item-card} {octicon}`arrow-switch` Export and format interoperability
:link: features/export
:link-type: doc

Workspace exports through conda's exporter plugin system, including
`environment.yml`, `environment.json`, lockfiles, and manifest dialects.
:::

:::{grid-item-card} {octicon}`archive` Archives and portable workspaces
:link: features/archives
:link-type: doc

Portable workspace archives with source files, manifests, optional lockfiles,
optional package bundles, receipts, and verified extraction.
:::

:::{grid-item-card} {octicon}`server` Local environments and CI
:link: features/ci
:link-type: doc

Workspace environment layout and package-cache placement for efficient CI
and Docker installs.
:::

::::

## Reading by task

For step-by-step learning, start with the [quickstart](quickstart.md) or
[tutorials](tutorials/index.md). For task-oriented integration work, use
the [how-to guides](how-to/index.md). For exact manifest fields and
command options, use the [configuration guide](configuration.md), the
[`conda.toml` specification](reference/conda-toml-spec.md), and the
[CLI reference](reference/cli.md).

```{toctree}
:hidden:
:maxdepth: 1

features/tasks
features/environments
features/locking
features/export
features/archives
features/ci
```
