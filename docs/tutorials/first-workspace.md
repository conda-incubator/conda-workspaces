(your-first-project)=

# Your first workspace

This tutorial creates a workspace for a Python project, with separate
environments for development, testing, and documentation, plus tasks
to run tests and build the docs.

The project is the code, data, and other files you work on. The workspace
defines the environments and tasks used to work with those files. The
directory containing the workspace manifest is the workspace root.

## Prerequisites

- conda (>= 26.3) with the conda-workspaces plugin installed
- A directory in which to create the workspace

## Create the workspace manifest

Create a directory and initialize its workspace manifest, `conda.toml`:

```bash
mkdir my-project && cd my-project
conda workspace init --format conda --name my-project \
  -c conda-forge --override-channels \
  --platform linux-64 --platform osx-arm64 --platform win-64
```

This creates a `conda.toml` with the name, channel, and platforms you
specified:

```toml
[workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
```

## Add dependencies

Each `conda workspace add` updates the manifest, installs into the
affected prefixes, and refreshes a complete `conda.lock` in one step.

Add your base dependencies:

```bash
conda workspace add "python>=3.10"
conda workspace add "numpy>=1.24" "scipy>=1.11"
```

Add test dependencies to a shared test feature:

```bash
conda workspace add --feature test "pytest>=8.0" "pytest-cov>=4.0" "ruff>=0.9"
```

Add documentation dependencies to a shared docs feature:

```bash
conda workspace add --feature docs "sphinx>=7.0" "myst-parser>=3.0"
```

Your `conda.toml` now looks like:

```toml
[workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = ">=3.10"
numpy = ">=1.24"
scipy = ">=1.11"

[feature.test.dependencies]
pytest = ">=8.0"
pytest-cov = ">=4.0"
ruff = ">=0.9"

[feature.docs.dependencies]
sphinx = ">=7.0"
myst-parser = ">=3.0"

[environments]
default = []
test = { features = ["test"] }
docs = { features = ["docs"] }
```

Three conda environments now exist under `.conda/envs/`:

```
.conda/envs/
├── default/    # python, numpy, scipy
├── test/       # + pytest, pytest-cov
└── docs/       # + sphinx, myst-parser
```

To stage a batch of edits before solving, pass `--no-lockfile-update`
to each `add` / `remove`, then run
`conda workspace install` once to solve, install, and regenerate
`conda.lock` for every environment:

```bash
conda workspace add --no-lockfile-update "python>=3.10"
conda workspace add --no-lockfile-update "numpy>=1.24" "scipy>=1.11"
conda workspace install
```

Use the same install command on a fresh checkout, such as after
cloning the repo on a new machine.

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
