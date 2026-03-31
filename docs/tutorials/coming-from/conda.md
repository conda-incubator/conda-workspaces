# Coming from conda

If you manage environments with `conda create`, `conda activate`, and
`environment.yml` files, this guide shows how conda-workspaces brings
project-scoped environments, lockfiles, and tasks to the workflow you
already know.

## What stays the same

conda-workspaces is a conda plugin — it uses conda's solver, channels,
and package infrastructure under the hood. Your `.condarc` settings,
channel configuration, and package cache all carry over. Environments
are real conda prefixes you can inspect with `conda list`.

## What changes

| Traditional conda | conda-workspaces |
|---|---|
| Environments live in a global location | Environments live in `.conda/envs/` inside your project |
| One environment per `environment.yml` | Multiple environments from a single manifest |
| No lockfile (or separate conda-lock) | `conda.lock` generated automatically |
| No built-in task runner | `conda task run` with dependencies, caching, templates |
| `conda activate myenv` | `conda workspace shell -e myenv` |
| Share `environment.yml` and hope it resolves | Share `conda.lock` for exact reproducibility |

## Command mapping

| conda | conda-workspaces |
|---|---|
| `conda create -n myenv python=3.10` | `conda workspace init` + `conda workspace add python=3.10` |
| `conda activate myenv` | `conda workspace shell` (or `conda workspace shell -e myenv`) |
| `conda deactivate` | `exit` |
| `conda install numpy` | `conda workspace add numpy` |
| `conda remove numpy` | `conda workspace remove numpy` |
| `conda list` | `conda workspace list` |
| `conda run -n myenv CMD` | `conda workspace run -- CMD` |
| `conda env export > environment.yml` | `conda workspace lock` (generates `conda.lock`) |

## Migrating an environment.yml

The fastest way to migrate is the built-in import command:

```bash
conda workspace import environment.yml
```

This reads your `environment.yml` and writes a `conda.toml` with the
equivalent workspace configuration. Use `--dry-run` to preview the
output without writing a file, or `-o custom.toml` to choose a
different output path.

For reference, given an existing `environment.yml`:

```yaml
name: my-project
channels:
  - conda-forge
dependencies:
  - python>=3.10
  - numpy>=1.24
  - pandas>=2.0
  - pip:
    - requests>=2.31
```

The equivalent `conda.toml` is:

```toml
[workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"
numpy = ">=1.24"
pandas = ">=2.0"

[pypi-dependencies]
requests = ">=2.31"
```

Then install:

```bash
conda workspace install
```

## Lockfiles: reproducibility built in

Traditional conda has no built-in lockfile. You might use `conda env
export` to capture a snapshot, but the output is platform-specific and
re-solving from it can produce different results over time.

conda-workspaces generates a `conda.lock` file automatically when you
run `conda workspace install` or `conda workspace lock`. The lockfile
records exact package URLs and checksums for every environment and
platform in your workspace:

```bash
conda workspace lock                   # solve only, write conda.lock
conda workspace install                # solve, lock, and install
conda workspace install --locked       # install from lockfile (validates freshness)
conda workspace install --frozen       # install from lockfile (skip freshness check)
```

`--locked` ensures the lockfile matches your manifest — if you've
changed dependencies since the lockfile was generated, the install
fails and tells you to re-lock. `--frozen` skips that check and
installs exactly what's in the lockfile, which is useful in CI where
you want zero solver overhead.

Commit `conda.lock` to version control and every contributor, CI
runner, and deployment target gets identical environments.

## Works with conda env create

conda-workspaces registers environment spec plugins so that standard
conda commands understand workspace manifests and lockfiles directly:

```bash
conda env create --file conda.toml -n myenv    # solve and create from manifest
conda env create --file conda.lock -n myenv    # install exact lockfile contents
```

This means you can share a `conda.toml` or `conda.lock` with someone
who has conda-workspaces installed and they can create an environment
with the familiar `conda env create` command — no new workflow to
learn.

The companion [conda-lockfiles](https://github.com/conda/conda-lockfiles)
plugin (installed as a dependency) adds the same `conda env create`
support for `pixi.lock` and `conda-lock.yml` files. Together the two
plugins cover all common lockfile formats:

| Plugin | Files | Format |
|---|---|---|
| conda-workspaces | `conda.toml` | workspace manifest |
| conda-workspaces | `conda.lock` | rattler-lock v6 |
| conda-lockfiles | `pixi.lock` | rattler-lock v6 |
| conda-lockfiles | `conda-lock.yml` | conda-lock v1 |

## Multiple environments from one manifest

With traditional conda you create separate environments and manage
them individually. conda-workspaces lets you define composable
features in a single file:

```toml
[workspace]
name = "my-project"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
python = ">=3.10"
numpy = ">=1.24"

[feature.test.dependencies]
pytest = ">=8.0"

[feature.docs.dependencies]
sphinx = ">=7.0"

[environments]
default = []
test = { features = ["test"] }
docs = { features = ["docs"] }
```

One `conda workspace install` creates all three environments, each
with the right subset of dependencies. Features compose — the `test`
environment includes everything in `default` plus pytest.

## Adding tasks

Traditional conda has no task runner, so most projects use Makefiles,
shell scripts, or tox. conda-workspaces has tasks built in:

```toml
[tasks]
test = { cmd = "pytest tests/ -v", depends-on = ["lint"] }
lint = "ruff check src/"

[tasks.check]
depends-on = ["test", "lint"]
description = "Run all checks"
```

```bash
conda task run -e test check    # resolves dependency order automatically
conda task list                 # shows all available tasks
```

Tasks support dependency graphs, input/output caching, Jinja2
templates, and per-platform overrides — see [features](../../features.md)
for details.

## Project-local environments

Traditional conda stores environments globally (typically under
`~/miniconda3/envs/`). conda-workspaces stores them inside your
project:

```
my-project/
├── conda.toml
├── conda.lock
└── .conda/envs/
    ├── default/
    ├── test/
    └── docs/
```

This keeps your project self-contained. Different projects can have
different versions of the same packages without conflicting.

## Next steps

- [Your first project](../first-project.md) — full walkthrough with
  environments and tasks
- [Configuration](../../configuration.md) — all manifest fields and
  file formats
- [CI pipeline](../ci-pipeline.md) — using conda-workspaces in GitHub
  Actions
