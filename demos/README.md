# Demo recordings

Animated terminal demos recorded with [VHS](https://github.com/charmbracelet/vhs).

## Demos

### Workspaces

| Demo | Description |
|---|---|
| `quickstart` | Init a workspace, add deps, declare an environment, list it, run a command |
| `workspace-quickstart` | Single-command bootstrap with `conda workspace quickstart` |
| `dependency-management` | Target feature, environment, and platform dependencies, then update a selected package |
| `lockfile` | Install, lock, clean, reinstall from lockfile |
| `export` | Export environment.yml, conda.toml, pyproject.toml, and conda-lock-v1 formats |
| `ci-split` | Split locking across a CI matrix and merge fragments with `--merge` |
| `multi-platform` | Cross-platform locking and the `--platform` flag |
| `multi-env` | Multiple environments from one manifest |
| `pixi-compat` | Use an existing pixi.toml with conda-workspaces |
| `import` | Convert source manifests and add environment.yml as a named environment |
| `shell` | Open an interactive shell in a workspace environment |
| `archives` | Create and extract workspace archives |
| `archives-receipt` | Create a workspace archive receipt and verify extraction |
| `archives-bundle` | Bundle packages for offline/air-gapped deployment |
| `archives-install` | Extract and install in one step with --install |
| `image` | Build a locked Linux image, run it offline, and retain activation when overriding its command |
| `ship` | Preview and build a native Python launcher, run it, and check that source files are unchanged |
| `ship-bundle` | Build embedded and external package bundles and run both launchers in offline mode |
| `auto-lockfile` | Automatic lockfile creation and staleness detection |

### Tasks

| Demo | Description |
|---|---|
| `task-quickstart` | Define tasks, list, and run them |
| `depends-on` | Task dependencies and --skip-deps |
| `caching` | Input/output caching for incremental builds |
| `templates` | Jinja2 templates and task arguments |
| `platform-overrides` | Platform-specific task overrides |
| `task-pixi-compat` | Read pixi.toml tasks and export to conda.toml |

### Ecosystem

| Demo | Description |
|---|---|
| `ecosystem` | Workspace environments + tasks in one manifest |

## Prerequisites

- [VHS](https://github.com/charmbracelet/vhs) (`brew install vhs` or `go install github.com/charmbracelet/vhs@latest`)
- [ttyd](https://github.com/tsl0922/ttyd) (installed automatically by VHS on first run)
- [bat](https://github.com/sharkdp/bat) (`conda install conda-forge::bat`)
- A working `pixi` installation with the dev environment configured

The `image` demo also requires a running Linux Docker daemon with Buildx and network access to fetch its base images and locked packages. It uses the Docker host's native architecture (`linux-64` or `linux-aarch64`) and the committed lockfile from `examples/container`. The recording omits the build wait and leaves the resulting `workspace-image-demo` image available locally.

The `ship` demos require a conda-ship build with the input-selection options from [conda-ship#147](https://github.com/conda-incubator/conda-ship/pull/147), installed in the dev environment. They use the committed lockfile from `examples/ship` on Linux x86-64 or Apple Silicon. `ship-bundle` also needs `tar` with Zstandard support. The recordings omit build and installation waits. Each uses a new temporary directory and fresh runtime prefixes, and leaves its artifacts and logs there for inspection. VHS also writes screen transcripts under `docs/_build`.

To record with a local conda-ship checkout, build its `cs` and `cs-template` release binaries, then set these variables before running VHS:

```bash
export PYTHONPATH="/path/to/conda-ship/python${PYTHONPATH:+:$PYTHONPATH}"
export CONDA_SHIP_EXECUTABLE="/path/to/conda-ship/target/release/cs"
```

## Regenerating demos

From the repository root:

```bash
# Regenerate all demos
pixi run demos

# Regenerate a single demo
pixi run demos quickstart
```

## File structure

- `_settings.tape`: shared VHS theme, font, and dimensions (sourced by all tapes)
- `_ship_setup.tape`: shared locked workspace setup for the shipping demos
- `fixtures/`: manifest files copied into demos
- `*.tape`: individual demo scripts
- `*.gif`: generated animated GIFs (used in docs and README)
