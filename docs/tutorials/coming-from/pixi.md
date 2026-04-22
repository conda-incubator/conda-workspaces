# Coming from pixi

![pixi compatibility demo](../../../demos/pixi-compat.gif)

If you already use pixi for workspace management and tasks,
conda-workspaces can read the same manifest files. This guide explains
how the two tools relate and how to use them side by side.

## Key differences

| Aspect | pixi | conda-workspaces |
|---|---|---|
| Solver | rattler (bundled) | conda / libmamba |
| Environment location | `.pixi/envs/` | `.conda/envs/` |
| Lock file | `pixi.lock` | `conda.lock` (same schema family as `pixi.lock`, distinct filename and on-disk version byte) |
| Task runner | Built-in | Built-in (`conda task`) |
| Template engine | MiniJinja (Rust) | Jinja2 (Python) |
| Shell | `deno_task_shell` | Native platform shell |
| Package builds | pixi-build (rattler-build) | conda-build (separate) |
| CLI | `pixi` | `conda workspace` / `conda task` |

## Using both tools

Since pixi stores environments in `.pixi/envs/` and conda-workspaces
uses `.conda/envs/`, both tools can coexist on the same project:

```bash
# pixi users
pixi install
pixi run test

# conda users
conda workspace install
conda task run test
```

Both read the same `pixi.toml` (or `pyproject.toml`) manifest —
workspace definitions and task definitions alike.

## Command mapping

| pixi | conda-workspaces |
|---|---|
| `pixi init` | `conda workspace init` |
| `pixi install` | `conda workspace install` |
| `pixi install --locked` | `conda workspace install --locked` (validates lockfile freshness) |
| `pixi install --frozen` | `conda workspace install --frozen` (installs from lockfile as-is) |
| `pixi add python` | `conda workspace add python` (installs by default, like pixi) |
| `pixi add --feature test pytest` | `conda workspace add -e test pytest` or `conda workspace add --feature test pytest` |
| `pixi add --pypi requests` | `conda workspace add --pypi requests` |
| `pixi add --no-install foo` | `conda workspace add --no-install foo` |
| `pixi remove numpy` | `conda workspace remove numpy` (uninstalls by default, like pixi) |
| `pixi run <task>` | `conda task run <task>` |
| `pixi run CMD` | `conda workspace run -- CMD` or `conda workspace shell -- CMD` |
| `pixi list` | `conda workspace list` (packages in default env) |
| `pixi list` (envs) | `conda workspace envs` |
| `pixi list` (specific env) | `conda workspace list -e <env>` |
| `pixi info` | `conda workspace info` (workspace overview) |
| `pixi info` (per-env) | `conda workspace info -e <env>` |
| `pixi lock` | `conda workspace lock` (pure solve, no install required) |
| `pixi shell` | `conda workspace shell` or `conda workspace shell -e <env>` |
| `pixi clean` | `conda workspace clean` |
| `pixi task list` | `conda task list` |

## Template compatibility

pixi uses MiniJinja (Rust) and conda-workspaces uses Jinja2 (Python).
The template syntax is identical for all practical purposes:

- `{{ pixi.platform }}` works in conda-workspaces when reading from
  `pixi.toml`
- `{{ conda.platform }}` is available everywhere
- Jinja2 supports a few extra filters not present in MiniJinja, but you
  are unlikely to hit any incompatibilities going the other direction

## Shell differences

pixi uses `deno_task_shell`, a cross-platform shell that understands
commands like `rm` on Windows. conda-workspaces uses the native platform
shell (`sh` on Unix, `cmd` on Windows).

For cross-platform compatibility, use platform overrides:

```toml
[tasks]
clean = "rm -rf build/"

[target.win-64.tasks]
clean = "rd /s /q build"
```

Or use Jinja2 conditionals:

```toml
[tasks]
clean = "{% if conda.is_win %}rd /s /q build{% else %}rm -rf build/{% endif %}"
```

## Using conda.toml instead

If your team uses only conda, you can use `conda.toml` instead of
`pixi.toml`. The format is structurally identical:

```bash
conda workspace init --format conda
```

This creates a `conda.toml` file with the same workspace/feature/
environment/task structure. The only difference is:

- `conda.toml` uses `[workspace]` exclusively (no `[project]` fallback)
- `conda.toml` is searched first, before `pixi.toml` and `pyproject.toml`

## Embedded in pyproject.toml

You can also embed workspace and task configuration in `pyproject.toml`:

```toml
# Preferred: under [tool.conda.*]
[tool.conda.workspace]
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[tool.conda.dependencies]
python = ">=3.10"

[tool.conda.tasks]
test = "pytest tests/ -v"

# Also supported: [tool.pixi.*]
# (same format pixi uses)
```

## Converting to conda.toml

If you want to convert your entire `pixi.toml` manifest (workspace
configuration, dependencies, and tasks) to `conda.toml`, use the
import command:

```bash
conda workspace import pixi.toml
```

This reads your `pixi.toml` and writes a fully equivalent `conda.toml`.
Use `--dry-run` to preview the output, or `-o custom.toml` to choose a
different output path.

To export only tasks, use the task export command instead:

```bash
conda task export --file pixi.toml -o conda.toml
```

## Cross-platform solving

`conda workspace lock` writes a multi-platform `conda.lock` just like
`pixi lock`. It iterates over every platform listed under
`[workspace]` / `[project]` and solves each one in isolation, pointing
conda's `context._subdir` at the target so `__linux` / `__osx` /
`__win` virtual packages line up with the target platform.

One difference to keep in mind: pixi (via rattler) ships conservative
baseline versions for virtual packages that the host machine can't
detect — e.g. solving `linux-64` from macOS still picks up a baseline
`__glibc` so `glibc`-gated packages resolve. conda-workspaces relies
on conda's own virtual package plugins, which only emit entries the
host can detect. Fill in the blanks with either `CONDA_OVERRIDE_*`
environment variables or an explicit `[system-requirements]` table in
the manifest:

```toml
[system-requirements]
glibc = "2.28"
cuda = "12.0"
```

Both settings are also honoured by `pixi`, so this is compatible
either way.

## What's not supported

Some pixi-only concepts don't apply to conda-workspaces:

- `[package]` / pixi-build — use conda-build instead
- `[host-dependencies]` / `[build-dependencies]` — part of pixi-build
- `deno_task_shell` — conda-workspaces uses native platform shells
- `solve-group` — accepted for compatibility but has no effect (conda's
  solver operates on a single environment at a time)
- pixi's built-in conservative virtual package defaults for
  cross-compiled targets — pin minimums via `[system-requirements]`
  or `CONDA_OVERRIDE_*` instead (tracked in [DESIGN.md](https://github.com/conda-incubator/conda-workspaces/blob/main/DESIGN.md) under "Future Work")

See [DESIGN.md](https://github.com/conda-incubator/conda-workspaces/blob/main/DESIGN.md)
for the full compatibility mapping.
