# Locked workspace container

This example installs a local Python application and a locked environment with conda, generates an activation entrypoint, and copies the environment into a fresh Linux image. The environment stays at `/opt/workspace/.conda/envs/runtime` during installation and execution. The application files stay at `/opt/workspace`.

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
{"prefix": "/opt/workspace/.conda/envs/runtime", "message": "Hello from the locked workspace", "hook": "activated at /opt/workspace/.conda/envs/runtime", "arguments": []}
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

## Update the environment

After changing the environment requirements, regenerate the lockfile before building:

```bash
conda workspace lock
```

The builder checks lockfile freshness and uses `LockfileInstallPlan` so an outdated or incomplete lockfile fails the build. `setuptools` and `python-build` are included in the locked environment because they build the local application. The application has a static version and does not need Git metadata during its build.

`build_environment.py` uses conda-workspaces' Python API to install at the final prefix, then reads conda's activation JSON and creates a shell script that preserves the prefix variables and activation hooks without retaining the bootstrap executable paths. Its final `exec` makes the application receive container signals directly.

The example uses Ubuntu 24.04 for both build and runtime. A different base must provide compatible Linux libraries and Bash. CUDA drivers, CPU capabilities, and the Linux kernel also depend on the machine running the container.
