# Local environments and CI

Workspace environments are project-local, standard conda prefixes. This
keeps project environments separate from global conda environments while
still allowing normal conda activation and execution workflows.

## Project-local environments

All environments are installed under `.conda/envs/` in your project
directory:

```text
my-project/
├── conda.toml
├── conda.lock
├── .conda/
│   └── envs/
│       ├── default/
│       ├── test/
│       └── docs/
└── src/
```

Environments are standard conda prefixes. Use
`conda workspace run -e <name> -- CMD` to run a command in an
environment, `conda workspace shell -e <name>` for an interactive shell,
or `conda activate .conda/envs/<name>` directly.

## Optimizing disk usage with hardlinks

conda hardlinks packages from its global cache into environment
prefixes, which saves significant disk space. In CI and Docker the
global cache is often on a different filesystem or volume from the
project directory, causing conda to silently fall back to copying
packages. That roughly doubles disk usage per environment.

Set the `CONDA_PKGS_DIRS` environment variable to a project-local path
before installing so that the cache and environments share a filesystem:

```bash
export CONDA_PKGS_DIRS="$PWD/.conda/pkgs"
conda workspace install
```

::::{tab-set}

:::{tab-item} GitHub Actions

```yaml
- name: Install workspace
  run: |
    export CONDA_PKGS_DIRS="$PWD/.conda/pkgs"
    conda workspace install
```

:::

:::{tab-item} GitLab CI

```yaml
install:
  script:
    - export CONDA_PKGS_DIRS="$PWD/.conda/pkgs"
    - conda workspace install
```

:::

::::

On developer workstations the global cache is usually on the same
filesystem and hardlinks work without any extra configuration.
