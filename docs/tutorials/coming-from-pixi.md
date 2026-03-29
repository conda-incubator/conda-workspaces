# Coming from pixi

![pixi compatibility demo](../../demos/pixi-compat.gif)

If you already use pixi for workspace management, conda-workspaces can
read the same manifest files. This guide explains how the two tools
relate and how to use them side by side.

## Key differences

| Aspect | pixi | conda-workspaces |
|---|---|---|
| Solver | rattler (bundled) | conda / libmamba |
| Environment location | `.pixi/envs/` | `.conda/envs/` |
| Lock file | `pixi.lock` | `conda.lock` |
| Task runner | Built-in | Use [conda-tasks](https://github.com/conda-incubator/conda-tasks) |
| Package builds | pixi-build (rattler-build) | conda-build (separate) |
| CLI | `pixi` | `cw` / `conda workspace` |

## Using both tools

Since pixi stores environments in `.pixi/envs/` and conda-workspaces
uses `.conda/envs/`, both tools can coexist on the same project:

```bash
# pixi users
pixi install
pixi run test

# conda users
cw install
cw run -e test pytest
```

Both read the same `pixi.toml` manifest.

## Command mapping

| pixi | conda-workspaces |
|---|---|
| `pixi init` | `cw init` |
| `pixi install` | `cw install` |
| `pixi install --locked` | `cw install --locked` (validates lockfile freshness) |
| `pixi install --frozen` | `cw install --frozen` (installs from lockfile as-is) |
| `pixi add python` | `cw add python` |
| `pixi add --feature test pytest` | `cw add -e test pytest` or `cw add --feature test pytest` |
| `pixi add --pypi requests` | `cw add --pypi requests` |
| `pixi remove numpy` | `cw remove numpy` |
| `pixi run test` | `cw run test` (with [conda-tasks](https://github.com/conda-incubator/conda-tasks) installed) |
| `pixi run CMD` | `cw run CMD` (arbitrary command in project env) |
| `pixi list` | `cw list` (packages in default env) |
| `pixi list` (envs) | `cw list --envs` |
| `pixi list` (specific env) | `cw list -e <env>` |
| `pixi info` | `cw info` (workspace overview) |
| `pixi info` (per-env) | `cw info -e <env>` |
| `pixi lock` | `cw lock` (pure solve, no install required) |
| `pixi shell` | `cw shell` or `cw shell -e <env>` |
| `pixi exec CMD` | `cw run CMD` (runs in project env, not a temporary one) |
| `pixi clean` | `cw clean` |

## Using conda.toml instead

If your team uses only conda, you can use `conda.toml` instead of
`pixi.toml`. The format is structurally identical:

```bash
cw init --format conda
```

This creates a `conda.toml` file with the same workspace/feature/
environment structure. The only difference is:

- `conda.toml` uses `[workspace]` exclusively (no `[project]` fallback)
- `conda.toml` is searched first, before `pixi.toml` and `pyproject.toml`

## Embedded in pyproject.toml

You can also embed workspace configuration in `pyproject.toml`:

```toml
# Preferred: under [tool.conda.*]
[tool.conda.workspace]
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[tool.conda.dependencies]
python = ">=3.10"

# Also supported: [tool.pixi.*]
# (same format pixi uses)
```

## What's not supported

Some pixi-only concepts don't apply to conda-workspaces:

- `[package]` / pixi-build — use conda-build instead
- `[host-dependencies]` / `[build-dependencies]` — part of pixi-build
- `deno_task_shell` — tasks are handled by conda-tasks, not conda-workspaces

See [DESIGN.md](https://github.com/conda-incubator/conda-workspaces/blob/main/DESIGN.md)
for the full compatibility mapping.
