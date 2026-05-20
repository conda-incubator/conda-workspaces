# PyPI dependencies

This guide walks through adding PyPI packages to a conda-workspaces
project, including versioned dependencies, editable local packages,
and what to install for everything to work.

## Manifest compatibility with pixi

The `[pypi-dependencies]` table uses the same syntax as
[pixi](https://pixi.sh), so manifests are portable between the two
tools. The underlying implementation is different: pixi resolves PyPI
packages with its own built-in solver (uv), while conda-workspaces
delegates to conda-pypi and conda-rattler-solver through conda's
plugin system.

## Prerequisites

PyPI dependency support requires two conda packages that are not
installed by default:

```bash
conda install conda-pypi conda-rattler-solver
```

**conda-pypi** translates PyPI package names to their conda equivalents
and handles wheel extraction and editable installs.

**conda-rattler-solver** is the solver backend that can resolve conda
and PyPI packages together in a single pass. Since conda-pypi 0.9.0 it
is no longer installed automatically, so you need to install it
explicitly.

If either package is missing, `conda workspace install` prints a
warning and skips PyPI dependencies.

:::{seealso}
These tools build on the conda plugin architecture defined in
[CEP 2](https://github.com/conda/ceps/blob/main/cep-0002.md) (plugin
architecture proposal) and
[CEP 4](https://github.com/conda/ceps/blob/main/cep-0004.md) (plugin
mechanism implementation). The solver backend plugin hook that
conda-rattler-solver uses was introduced by
[CEP 3](https://github.com/conda/ceps/blob/main/cep-0003.md)
(pluggable solver backends, originally for conda-libmamba-solver).
:::

## Adding versioned PyPI dependencies

Declare PyPI dependencies alongside your conda dependencies in the
manifest:

```toml
[dependencies]
python = ">=3.10"
numpy = ">=1.24"

[pypi-dependencies]
httpx = ">=0.27"
pydantic = ">=2.0,<3"
```

PyPI package names are translated to their conda equivalents via the
[grayskull mapping](https://github.com/conda/grayskull) and merged
into the same solver call as conda dependencies. The solver resolves
everything together, so conda and PyPI packages cannot conflict.

Install as usual:

```bash
conda workspace install
```

### Extras

PyPI extras are supported with square-bracket syntax:

```toml
[pypi-dependencies]
httpx = { version = ">=0.27", extras = ["http2"] }
```

### Per-feature PyPI dependencies

Just like conda dependencies, PyPI dependencies can be scoped to a
feature:

```toml
[feature.test.pypi-dependencies]
pytest-httpx = ">=0.30"

[environments]
default = []
test = { features = ["test"] }
```

## Editable local packages

For local Python packages under active development, use a path
dependency with `editable = true`:

```toml
[pypi-dependencies]
my-project = { path = ".", editable = true }
```

This installs the package in editable (development) mode so that
changes to the source code take effect immediately without
reinstalling. conda-pypi builds the package into a `.conda` archive
using PEP 517 and installs it into the environment prefix.

Path dependencies can also point to subdirectories in a monorepo:

```toml
[pypi-dependencies]
core = { path = "packages/core", editable = true }
api = { path = "packages/api", editable = true }
```

Editable and path dependencies are handled separately from versioned
PyPI dependencies. They are built and installed after the main solver
completes, so they do not participate in dependency resolution. Make
sure any transitive dependencies your local package needs are declared
as conda or versioned PyPI dependencies in the manifest.

## Git and URL dependencies

You can also install packages directly from git repositories or URLs:

```toml
[pypi-dependencies]
my-fork = { git = "https://github.com/user/project.git", branch = "main" }
some-wheel = { url = "https://example.com/pkg-1.0-py3-none-any.whl" }
```

Like editable installs, these are built post-solve by conda-pypi and
do not participate in the solver pass.

## Troubleshooting

### "conda-pypi is not installed"

Install it:

```bash
conda install conda-pypi
```

### "conda-rattler-solver is not installed"

Since conda-pypi 0.9.0, the solver backend is a separate package:

```bash
conda install conda-rattler-solver
```

### Solve failures with PyPI packages

If the solver cannot find a PyPI package, check that:

1. The package name is spelled correctly (PyPI names are
   case-insensitive but conda names use lowercase and hyphens).
2. The version constraint is satisfiable.
3. Your channels include a PyPI-aware index. For development, see the
   [conda-pypi documentation](https://github.com/conda/conda-pypi)
   for channel setup.

## Next steps

- [Features: PyPI dependencies](../features.md#pypi-dependencies) for
  the full reference on supported fields
- [Configuration](../configuration.md) for all manifest options
- [Your first project](first-project.md) for a complete walkthrough
