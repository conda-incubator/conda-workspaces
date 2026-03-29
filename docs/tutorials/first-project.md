# Your first workspace

This tutorial walks through setting up a Python project with separate
environments for development, testing, and documentation.

## Prerequisites

- conda (>= 24.7) with the conda-workspaces plugin installed
- A project directory to work in

## Create the workspace manifest

Start by creating a `conda.toml` in your project root:

```bash
mkdir my-project && cd my-project
cw init --format conda --name my-project
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

Add your base dependencies:

```bash
cw add "python>=3.10"
cw add "numpy>=1.24" "scipy>=1.11"
```

Add test dependencies to a test feature:

```bash
cw add -e test "pytest>=8.0" "pytest-cov>=4.0"
```

Add documentation dependencies:

```bash
cw add -e docs "sphinx>=7.0" "myst-parser>=3.0"
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

## Install environments

```bash
cw install
```

This creates three conda environments under `.conda/envs/`:

```
.conda/envs/
├── default/    # python, numpy, scipy
├── test/       # + pytest, pytest-cov
└── docs/       # + sphinx, myst-parser
```

## Run commands

Run your tests:

```bash
cw run -e test pytest -v
```

Build documentation:

```bash
cw run -e docs sphinx-build docs docs/_build
```

## Check environment status

```bash
cw list --envs
cw info -e test
```

## Next steps

- Learn about [features](../features.md) and how environments compose
- See the [configuration](../configuration.md) reference for all options
- Set up [CI pipelines](ci-pipeline.md) with conda-workspaces
