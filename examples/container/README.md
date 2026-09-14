# Locked workspace container

This example installs a local Python application and a locked environment with conda, generates an activation entrypoint, and copies the environment into a fresh Linux image. The manifest names the workspace `workspace-container-example`, so its application files stay at `/workspaces/workspace-container-example`. The environment stays at `/workspaces/workspace-container-example/.conda/envs/runtime` during installation and execution.

The default command prints its Python prefix, a manifest environment variable, a value set by the activation hook, and any application arguments. The final image contains neither the bootstrap conda installation nor its package caches. Container startup only activates the environment and executes the command.

## Build and run

From this directory, with Docker and Buildx installed:

```bash
docker buildx build --platform linux/amd64 --load -t workspace-container-example .
docker run --rm --network none workspace-container-example
docker run --rm --network none workspace-container-example workspace-hello "another argument"
```

Use `--platform linux/arm64` for a native Linux ARM64 build. The committed `conda.lock` covers both architectures. Cross-architecture builds require a builder that supports the target architecture.

The default output includes:

```json
{"prefix": "/workspaces/workspace-container-example/.conda/envs/runtime", "message": "Hello from the locked workspace", "hook": "activated at /workspaces/workspace-container-example/.conda/envs/runtime", "arguments": []}
```

Overriding the command retains activation:

```bash
docker run --rm --network none workspace-container-example python -m workspace_container_example "from Python"
```

The workspace image command assembles this workflow from the manifest and archive rules:

```bash
conda workspace image -e runtime --platform linux-64 \
  -t workspace-container-example --load -- workspace-hello
```

Use `--platform linux-aarch64` for Linux ARM64. The image command creates an OCI-capable Buildx builder for each invocation unless `--builder` selects an existing one.

## Extend the image

Use the built image as the base of a downstream Dockerfile:

```dockerfile
FROM workspace-container-example
COPY README.md /workspaces/workspace-container-example/BUILD-NOTES.md
RUN python --version
RUN workspace-entrypoint workspace-hello "during build"
CMD ["workspace-hello", "from a derived image"]
```

`RUN python` finds the locked environment through `PATH`. Build steps need the explicit `workspace-entrypoint` wrapper to apply the manifest variables and activation hook. This wrapper lives at `/workspaces/workspace-container-example/.conda/bin/workspace-entrypoint`, and its directory is also on `PATH`. The runtime `CMD` uses the inherited activating entrypoint. A derived image can choose another `WORKDIR` while the environment remains at its original path.

Mount runtime data below `/workspaces/workspace-container-example/data`. A mount over `/workspaces/workspace-container-example` hides the installed environment along with the project files. For multistage `COPY --from`, copy that workspace directory to the same absolute path to retain the environment, project files, and activation wrapper. Image configuration such as `PATH`, `ENTRYPOINT`, `CMD`, and `WORKDIR` must be set separately in the final stage.

The image command requires `--base-image` to have no existing `/workspaces/workspace-container-example` path. Other workspace directories may exist under `/workspaces`, each retaining its own manifest, environment, and activation wrapper. Each invocation builds one workspace with one default runtime command. Task and dependency graphs remain separate. See the [image guide](../../docs/how-to/image.md) for the complete workflow.

## Update the environment

After changing the environment requirements, regenerate the lockfile before building:

```bash
conda workspace lock
```

The builder checks lockfile freshness and uses `LockfileInstallPlan` so an outdated or incomplete lockfile fails the build. `setuptools` and `python-build` are included in the locked environment because they build the local application. The application has a static version and does not need Git metadata during its build.

`build_environment.py` uses conda-workspaces' Python API to install at the final prefix, then reads conda's activation JSON and creates a shell script that preserves the prefix variables and activation hooks without retaining the bootstrap executable paths. Its final `exec` makes the application receive container signals directly.

The example uses Ubuntu 24.04 for both build and runtime. A different base must provide compatible Linux libraries and Bash. CUDA drivers, CPU capabilities, and the Linux kernel also depend on the machine running the container.
