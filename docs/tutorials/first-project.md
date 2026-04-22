# Your first project

This tutorial walks through setting up a Python project with separate
environments for development, testing, and documentation — plus tasks
to automate your workflow.

## Prerequisites

- conda (>= 24.7) with the conda-workspaces plugin installed
- A project directory to work in

## Create the workspace manifest

Start by creating a `conda.toml` in your project root:

```bash
mkdir my-project && cd my-project
conda workspace init --format conda --name my-project
```

This creates a `conda.toml` with sensible defaults:

```toml
[workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
```

## Add dependencies

Each `conda workspace add` updates the manifest, resolves the
affected environments, installs into their prefixes, and refreshes
`conda.lock` — no separate install step is needed.

Add your base dependencies:

```bash
conda workspace add "python>=3.10"
conda workspace add "numpy>=1.24" "scipy>=1.11"
```

Add test dependencies to a test feature:

```bash
conda workspace add -e test "pytest>=8.0" "pytest-cov>=4.0"
```

Add documentation dependencies:

```bash
conda workspace add -e docs "sphinx>=7.0" "myst-parser>=3.0"
```

Your `conda.toml` now looks like:

```toml
[workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"
numpy = ">=1.24"
scipy = ">=1.11"

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
```

And three conda environments now exist under `.conda/envs/`:

```
.conda/envs/
├── default/    # python, numpy, scipy
├── test/       # + pytest, pytest-cov
└── docs/       # + sphinx, myst-parser
```

Prefer to stage a batch of edits before solving? Pass
`--no-lockfile-update` to each `add` / `remove`, then run
`conda workspace install` once to solve, install, and regenerate
`conda.lock` for every environment:

```bash
conda workspace add --no-lockfile-update "python>=3.10"
conda workspace add --no-lockfile-update "numpy>=1.24" "scipy>=1.11"
conda workspace install
```

This is also how you reinstall everything on a fresh checkout (e.g.
after cloning the repo on a new machine).

## Define tasks

Add tasks to your `conda.toml`:

```toml
[tasks]
test = { cmd = "pytest tests/ -v", description = "Run the test suite" }
lint = { cmd = "ruff check src/", description = "Lint the source code" }
build-docs = { cmd = "sphinx-build docs docs/_build/html", description = "Build documentation" }

[tasks.check]
depends-on = ["lint", "test"]
description = "Run all checks"
```

## Run tasks

List available tasks:

```bash
conda task list
```

Run a single task in a workspace environment:

```bash
conda task run -e test test
```

Run the full check suite:

```bash
conda task run -e test check
```

Build documentation:

```bash
conda task run -e docs build-docs
```

## Run commands in an environment

Run a one-shot command in a workspace environment:

```bash
conda workspace run -e test -- python -c "import numpy; print(numpy.__version__)"
```

Or drop into an interactive shell:

```bash
conda workspace shell -e test
```

## Check environment status

```bash
conda workspace envs
conda workspace info -e test
```

## Next steps

- Learn about [features](../features.md) and how environments and tasks compose
- See the [configuration](../configuration.md) reference for all options
- Set up [CI pipelines](ci-pipeline.md) with conda-workspaces
