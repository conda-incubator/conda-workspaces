# Motivation

## Why conda-workspaces?

Conda is a powerful package and environment manager, but it has lacked
two things that modern projects need: project-scoped multi-environment
workspace management, and a built-in task runner. Projects typically
manage environments manually with `environment.yml` files and rely on
`Makefile`, `tox`, or ad-hoc shell scripts for common workflows.

[pixi](https://pixi.sh) introduced an excellent project model that
integrates multi-environment management and a task runner with project
configuration. conda-workspaces brings both capabilities to conda as a
plugin, so existing conda users can define workspaces and tasks without
switching tools.

## Workspaces and tasks, not a new package manager

conda-workspaces adds workspace-level orchestration and task running on
top of conda's existing environment management. It does not replace
conda's solver, channels, or installation machinery.

In pixi, a single `pixi.toml` manages workspace definitions, task
definitions, and installation. Running `pixi install` uses rattler
(a Rust-based solver) to resolve and install packages into
`.pixi/envs/`. conda-workspaces separates concerns:

| Responsibility | In pixi | In conda + conda-workspaces |
|---|---|---|
| Define workspace & envs | `pixi.toml` or `pyproject.toml` | `conda.toml`, `pixi.toml`, or `pyproject.toml` |
| Define tasks | `pixi.toml` or `pyproject.toml` `[tasks]` | Same manifest file |
| Solve dependencies | rattler (bundled) | conda / libmamba (existing) |
| Install packages | rattler (into `.pixi/envs/`) | conda (into `.conda/envs/`) |
| Manage environments | `pixi install` | `conda workspace install` (delegates to conda) |
| Run tasks | `pixi run <task>` | `conda task run <task>` |
| Run commands in env | `pixi run CMD` | `conda workspace run -- CMD` or `conda workspace shell -e ENV -- CMD` |
| Activate | `pixi shell` | `conda workspace shell` / `conda activate .conda/envs/<name>` |

This approach has advantages:

- Uses conda's mature solver and package cache
- Environments are standard conda prefixes (compatible with all conda tooling)
- No additional resolver or package installation system to maintain
- Works with existing conda channels, mirrors, and authentication
- Incremental adoption without changing your existing conda workflow
- Tasks work standalone without a workspace definition

## Tasks without workspaces

Tasks can be used independently of workspaces. A `conda.toml` with only
a `[tasks]` table is perfectly valid — no `[workspace]` or
`[dependencies]` required. Tasks run in whatever conda environment is
currently active.

This makes it easy to start with tasks and add workspace features later:

```toml
# A valid conda.toml — tasks only, no workspace
[tasks]
test = "pytest tests/ -v"
lint = "ruff check ."
check = { depends-on = ["test", "lint"] }
```

## Compatibility with pixi

conda-workspaces reads the same manifest format as pixi. A project with a
`pixi.toml` or `pyproject.toml` can use both tools:

- pixi users run `pixi install` and `pixi run` as usual
- conda users run `conda workspace install` and `conda task run` using conda's solver

This coexistence is possible because each tool stores environments in a
different directory (`.pixi/envs/` vs `.conda/envs/`), so they don't
interfere with each other.

See [DESIGN.md](https://github.com/conda-incubator/conda-workspaces/blob/main/DESIGN.md)
for the full compatibility mapping.

## Comparison to other tools

| Tool | Scope | Tasks | Multi-env | Lock files | Solver |
|---|---|---|---|---|---|
| conda-workspaces | Workspaces + tasks | Yes | Yes | Yes (`conda.lock`) | conda / libmamba |
| pixi | Full project mgmt | Yes | Yes | Yes | rattler (bundled) |
| conda-project | Project management | Commands only | Yes | Yes | conda (via conda-lock) |
| anaconda-project | Project management | Commands only | Yes | Yes | conda |
| conda-devenv | Env templating | No | Via includes | Yes | conda / mamba |
| conda-lock | Lock files only | No | N/A | Yes | conda |
| tox / nox | Test matrix | Yes | Virtualenvs | No | pip |

### Prior art

conda-workspaces builds on ideas from several earlier tools.

[anaconda-project](https://github.com/anaconda/anaconda-project) was the
first tool to add project-scoped conda environments, command runners, and
secrets management via an `anaconda-project.yml` manifest. It supports
multiple named environment specs and lock files. However, development has
slowed and the manifest format is not shared with any other tool.

[conda-project](https://github.com/conda-incubator/conda-project) is the
spiritual successor to anaconda-project. It uses `conda-project.yml` for
multi-environment management and generates lock files via conda-lock.
conda-project remains in alpha and uses its own manifest format.

[conda-devenv](https://github.com/ESSS/conda-devenv) takes a different
approach: it extends `environment.yml` with Jinja2 templating, file
includes, and environment variable definitions. It is well suited for
composing a single environment from multiple projects, but does not
support multiple independent environments per project.

conda-workspaces differs from all of the above in two ways:

1. It uses pixi's TOML-based manifest format rather than inventing a new
   one. Projects can share a single `pixi.toml` or `pyproject.toml`
   between pixi and conda-workspaces.
2. It integrates as a conda plugin (`conda workspace`, `conda task`)
   rather than shipping a standalone CLI. Environments are standard conda
   prefixes that work with `conda activate` and all existing conda
   tooling.

### Task runner prior art

The task runner in conda-workspaces draws from a broad set of prior
work.

[pixi](https://pixi.sh) was the first tool to ship a full-featured task
runner tightly integrated with conda package management: task
dependencies, platform overrides, input/output caching, template
variables, and task arguments. Its task system is the direct inspiration
for the task features in conda-workspaces. The key difference is that
pixi uses MiniJinja (Rust) and `deno_task_shell` for cross-platform
execution, while conda-workspaces uses Jinja2 (Python) and the native
platform shell.

General-purpose Python task runners like [tox](https://tox.wiki),
[nox](https://nox.thea.codes), [invoke](https://www.pyinvoke.org), and
[hatch](https://hatch.pypa.io) each provide ways to define and run
project tasks. tox and nox focus on test-matrix automation with
virtualenvs; invoke is a general-purpose Make replacement; hatch offers
scripts and environment matrices for Python projects. None of them
integrate directly with conda environments or conda's plugin system.

## Acknowledgements

The workspace and task system in conda-workspaces is directly inspired by
the work of the [prefix.dev](https://prefix.dev) team on
[pixi](https://github.com/prefix-dev/pixi). Their design of workspace
manifests, features, environments, platform targeting, task dependencies,
caching, and template variables provided the blueprint for this plugin.
We are grateful for their contribution to the conda ecosystem.

The [anaconda-project](https://github.com/anaconda/anaconda-project)
and [conda-project](https://github.com/conda-incubator/conda-project)
teams explored project-scoped environments and command runners long
before conda-workspaces existed. Their work informed how project-level
automation fits into the conda ecosystem.
