# Features

conda-workspaces adds project workspaces and task execution to conda
without replacing conda's solver, package cache, or environment prefixes.
This overview explains the main feature areas and points to the detailed
explanation pages for each one.

## Choose a feature area

<!-- Preserve the former overview's URL fragments. -->

::::{grid} 2
:gutter: 3

:::{grid-item-card} {octicon}`terminal` Tasks
:link: features/tasks
:link-type: doc

<span id="tasks"></span>
<span id="task-commands"></span>
<span id="task-dependencies"></span>
<span id="task-aliases"></span>
<span id="hidden-tasks"></span>
<span id="task-arguments"></span>
<span id="template-variables"></span>
<span id="task-environment-variables"></span>
<span id="clean-environment"></span>
<span id="task-caching"></span>
<span id="platform-specific-tasks"></span>
<span id="task-environment-targeting"></span>
<span id="user-level-tasks"></span>

Repeatable project commands with dependencies, arguments, templates,
environment targeting, clean environments, and caching.
:::

:::{grid-item-card} {octicon}`package` Environments and features
:link: features/environments
:link-type: doc

<span id="environments"></span>
<span id="workspace-dependency-inheritance"></span>
<span id="dependency-mutation-locations"></span>
<span id="channels"></span>
<span id="platform-targeting"></span>
<span id="known-vs-declared-platforms"></span>
<span id="pypi-dependencies"></span>
<span id="no-default-feature"></span>
<span id="activation"></span>
<span id="system-requirements"></span>
<span id="channel-priority"></span>

Project-local conda prefixes composed from reusable features, shared
dependencies, channels, platform overrides, PyPI dependencies, and
activation settings.
:::

:::{grid-item-card} {octicon}`lock` Locking and reproducible installs
:link: features/locking
:link-type: doc

<span id="lock"></span>
<span id="automatic-lockfile-management"></span>
<span id="lock-freshness-indicator"></span>
<span id="ci-friendly-defaults"></span>
<span id="ci-split-locking-with-merge"></span>

Multi-environment, multi-platform `conda.lock` files, freshness checks,
locked installs, CI defaults, and split matrix locking.
:::

:::{grid-item-card} {octicon}`arrow-switch` Export and format interoperability
:link: features/export
:link-type: doc

<span id="export"></span>
<span id="manifest-format-exporters"></span>

Workspace exports through conda's exporter plugin system, including
`environment.yml`, `environment.json`, lockfiles, and manifest dialects.
:::

:::{grid-item-card} {octicon}`archive` Archives and portable workspaces
:link: features/archives
:link-type: doc

<span id="archives"></span>

Portable workspace archives with source files, manifests, optional
lockfiles and package bundles, receipts, and verified extraction.
:::

:::{grid-item-card} {octicon}`verified` Workspace attestations
:link: features/attestations
:link-type: doc

Sigstore attestations for the exact workspace manifest and canonical lockfile,
with signer checks for locked installs.
:::

:::{grid-item-card} {octicon}`server` Local environments and CI
:link: features/ci
:link-type: doc

<span id="project-local-environments"></span>
<span id="ci-and-docker"></span>
<span id="optimizing-disk-usage-with-hardlinks"></span>

Project-local prefix layout and package-cache placement for efficient CI
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
features/attestations
features/ci
```
