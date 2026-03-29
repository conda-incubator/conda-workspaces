# Quick start

## Installation

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

## Your first workspace

Create a manifest in your project root:

::::{tab-set}

:::{tab-item} conda.toml

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
default = { solve-group = "default" }
test = { features = ["test"], solve-group = "default" }
```

:::

:::{tab-item} pixi.toml

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
default = { solve-group = "default" }
test = { features = ["test"], solve-group = "default" }
```

:::

:::{tab-item} pyproject.toml

```toml
[tool.conda.workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[tool.conda.dependencies]
python = ">=3.10"
numpy = ">=1.24"

[tool.conda.feature.test.dependencies]
pytest = ">=8.0"
pytest-cov = ">=4.0"

[tool.conda.environments]
default = { solve-group = "default" }
test = { features = ["test"], solve-group = "default" }
```

:::

::::

Or use the init command to scaffold one:

```bash
cw init
# or: cw init --format conda
# or: cw init --format pyproject
```

## Install environments

```bash
cw install
```

This creates project-local conda environments under `.conda/envs/` for
each environment defined in your manifest. A `conda.lock` file is
generated automatically after solving.

You can point to a specific manifest with `--file` / `-f`:

```bash
cw install -f path/to/conda.toml
```

To recreate environments from scratch, use `--force-reinstall`:

```bash
cw install --force-reinstall
```

## Lock

The `cw lock` command runs the solver and records the solution in
`conda.lock` without installing any environments:

```bash
cw lock
```

## Reproducible installs

Use `--locked` to install from the lockfile. This validates that the
lockfile is still fresh relative to the manifest — if the manifest has
changed, the install fails:

```bash
cw install --locked
```

Use `--frozen` to install from the lockfile as-is, without checking
freshness:

```bash
cw install --frozen
```

## Run commands

```bash
cw run -e test -- pytest -v
```

## Spawn a shell

To drop into an interactive shell with an environment activated, use
`cw shell`. This relies on [conda-spawn](https://conda-incubator.github.io/conda-spawn/)
to start a new shell process — exit with `exit` or Ctrl+D to return.

```bash
cw shell -e test
```

You can also pass a command to run inside the spawned shell:

```bash
cw shell -e test -- python -c "import numpy; print(numpy.__version__)"
```

## Add dependencies

```bash
cw add numpy
```

Add to a specific feature:

```bash
cw add --feature test pytest
```

Add a PyPI dependency:

```bash
cw add --pypi requests
```

## List packages

```bash
cw list
```

This lists packages in the default environment. To list packages in a
specific environment:

```bash
cw list -e test
```

To list defined environments instead:

```bash
cw list --envs
```

## Workspace overview

```bash
cw info
```

To see details for a specific environment:

```bash
cw info -e test
```

## Next steps

- Read about [features](features.md) to learn how environments compose
- See the [configuration](configuration.md) reference for all manifest options
- Check out the [tutorials](tutorials/index.md) for more in-depth guides
