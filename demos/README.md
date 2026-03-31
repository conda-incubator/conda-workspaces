# Demo recordings

Animated terminal demos recorded with [VHS](https://github.com/charmbracelet/vhs).

## Demos

### Workspaces

| Demo | Description |
|---|---|
| `quickstart` | Init a workspace, add deps, install, list envs, run a command |
| `lockfile` | Install, lock, clean, reinstall from lockfile |
| `multi-env` | Multiple environments from one manifest |
| `pixi-compat` | Use an existing pixi.toml with conda-workspaces |
| `import` | Import environment.yml and anaconda-project.yml into conda.toml |
| `shell` | Interactive shell and one-shot commands |

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

## Regenerating demos

From the project root:

```bash
# Regenerate all demos
pixi run demos

# Regenerate a single demo
pixi run demos quickstart
```

## File structure

- `_settings.tape` — shared VHS theme, font, and dimensions (sourced by all tapes)
- `fixtures/` — TOML manifests used by demos that start from an existing project
- `*.tape` — individual demo scripts
- `*.gif` — generated animated GIFs (used in docs and README)
- `*.mp4` — generated MP4 videos (higher quality)
