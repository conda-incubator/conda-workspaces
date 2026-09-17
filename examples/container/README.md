# Locked workspace container

This example installs a locked environment with conda, copies a Python module, and generates an activation entrypoint in a fresh Linux image. The manifest names the workspace `workspace-container-example`, so its application files stay at `/workspaces/workspace-container-example`. The environment stays at `/workspaces/workspace-container-example/.conda/envs/runtime` during installation and execution.

Workspace names and environment paths support spaces but cannot contain dollar signs or apostrophes, which conda activation and Dockerfile parsing interpret.

The default command prints its Python prefix, a manifest environment variable, a value set by the activation hook, and any application arguments. The final image contains neither the bootstrap conda installation nor its package caches. Container startup only activates the environment and executes the command.

## Build and run

From this directory, with Docker, Buildx, and conda-workspaces installed:

```bash
conda workspace image -e runtime --platform linux-64 \
  -t workspace-container-example --load -- python -m workspace_container_example
docker run --rm --network none workspace-container-example
docker run --rm --network none workspace-container-example python -m workspace_container_example "another argument"
```

The application runs directly from the copied source file. It does not need a Python package build or an unreleased conda-pypi version.

Use `--platform linux-aarch64` for Linux ARM64. The committed `conda.lock` covers both architectures. Cross-architecture builds require a builder that supports the target architecture. The image command creates a temporary Buildx builder unless `--builder` selects an existing one.

The default output includes:

```json
{"prefix": "/workspaces/workspace-container-example/.conda/envs/runtime", "message": "Hello from the locked workspace", "hook": "activated at /workspaces/workspace-container-example/.conda/envs/runtime", "arguments": []}
```

Overriding the command retains activation:

```bash
docker run --rm --network none workspace-container-example python -m workspace_container_example "from Python"
```

## Extend the image

Use the built image as the base of a downstream Dockerfile:

```dockerfile
FROM workspace-container-example
COPY README.md /workspaces/workspace-container-example/BUILD-NOTES.md
RUN python --version
RUN workspace-entrypoint python -m workspace_container_example "during build"
CMD ["python", "-m", "workspace_container_example", "from a derived image"]
```

`RUN python` finds the locked environment through `PATH`. Build steps need the explicit `workspace-entrypoint` wrapper to apply the manifest variables and activation hook. This wrapper lives at `/workspaces/workspace-container-example/.conda/bin/workspace-entrypoint`, and its directory is also on `PATH`. The runtime `CMD` uses the inherited activating entrypoint. The copied module runs from the workspace directory. If a derived image changes `WORKDIR`, use the module's absolute path in its Python command.

Mount runtime data below `/workspaces/workspace-container-example/data`. A mount over `/workspaces/workspace-container-example` hides the installed environment along with the project files. For multistage `COPY --from`, copy that workspace directory to the same absolute path to retain the environment, project files, and activation wrapper. Image configuration such as `PATH`, `ENTRYPOINT`, `CMD`, and `WORKDIR` must be set separately in the final stage.

The image command requires `--base-image` to have no existing `/workspaces/workspace-container-example` path. Other workspace directories may exist under `/workspaces`, each retaining its own manifest, environment, and activation wrapper. Each invocation builds one workspace with one default runtime command. Task and dependency graphs remain separate. See the [image guide](../../docs/how-to/image.md) for the complete workflow.

## Update the environment

After changing the environment requirements, regenerate the lockfile before building:

```bash
conda workspace lock
```

The generated recipe first runs `conda workspace install --locked --download-only` to validate and fetch the locked packages. It then runs `conda workspace install --locked` in a Docker `RUN --network=none` step. Both commands select the runtime environment and target platform. An outdated or incomplete lockfile fails the build.

The locked environment provides Python. No packaging tools are needed for the copied module. The image command generates the activation wrapper through `conda_workspaces.image_entrypoint`.

The builder copies the invoking conda-workspaces Python sources and installs a released conda-pypi package with native Linux dependencies in its bootstrap environment. By default, the image command uses `debian:bookworm-slim` for build and runtime. A different base must provide compatible Linux libraries and Bash. CUDA drivers, CPU capabilities, and the Linux kernel also depend on the machine running the container.
