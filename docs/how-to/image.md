# Build a workspace image

`conda workspace image` builds one locked Linux environment and the workspace's
project files into a runnable container image. It delegates image construction
to Docker Buildx and installs packages through conda inside the Linux builder.

Install Docker with Buildx and ensure its daemon is running. Image tooling is
optional for other workspace commands and for `image --dry-run`.

## Prepare the environment

Declare a runtime environment and Linux platform in the manifest, then generate
`conda.lock`. The image command requires a current, complete lockfile and never
updates the source manifest or lockfile.

Local Python applications must use non-editable, workspace-relative path
dependencies. Include their runtime requirements, `python-build`, and their
Python build backend, such as `setuptools`, in the locked environment. The
builder rejects missing build or runtime requirements instead of solving them.
Local builds run with environment activation and networking disabled.

The [container example](https://github.com/conda-incubator/conda-workspaces/tree/main/examples/container)
includes a local application, activation settings, a lockfile for both supported
Linux architectures, and a standalone Dockerfile.

## Build and run

```bash
conda workspace image -e runtime --platform linux-64 \
  -t myapp:latest --load -- python -m myapp

docker run --rm myapp:latest
```

Use `linux-aarch64` for ARM64. A named workspace platform that maps to either
supported subdir is also accepted. Building for another architecture requires
a builder capable of running that architecture, either natively or with
emulation.

The command after `--` becomes the image's default command. Docker command
arguments override it while preserving activation:

```bash
docker run --rm myapp:latest python -c 'import sys; print(sys.prefix)'
```

The final image contains the environment at `/opt/workspace/.conda/envs/<name>` and selected project
files at `/opt/workspace`, which is also its working directory. Its entrypoint applies
conda environment variables and sources activation hooks before using `exec`
to start the application. Startup performs no installation or solving. Conda
and temporary build tools are excluded unless the selected environment or base
image itself includes them. Build-generated source caches are excluded.

Mount runtime data into a subdirectory, for example
`--mount type=bind,src=/path/to/data,dst=/opt/workspace/data`. Mounting over
`/opt/workspace` would hide both the project files and the installed environment.

Choose a compatible glibc-based Linux base with `/bin/bash`:

```bash
conda workspace image -e runtime --platform linux-64 \
  --base-image ubuntu:24.04 -t myapp:latest --load -- python -m myapp
```

The default is `debian:bookworm-slim`. The build checks native virtual packages
against locked package and manifest requirements. The runtime host must also
meet kernel, CPU, and driver requirements. Pin base images by digest when a
stable base is required. The bootstrap image is versioned, while its temporary
conda build tools are installed separately from the locked application.

## Export or publish

Choose exactly one destination. `--load` loads an image into the local Docker
image store. `--push` publishes tagged images through the builder's registry
support and existing authentication:

```bash
conda workspace image -e runtime --platform linux-64 \
  -t registry.example.org/myapp:latest --push -- python -m myapp
```

`-o/--output` writes a standard OCI image layout tar archive:

```bash
conda workspace image -e runtime --platform linux-64 \
  -t myapp:latest -o myapp.oci.tar -- python -m myapp
```

The archive contains `oci-layout`, `index.json`, and content-addressed blobs.
Use an OCI-aware tool to copy or import it, for example:

```bash
skopeo copy oci-archive:myapp.oci.tar docker-daemon:myapp:latest
```

Use `--load` when loading directly into Docker, whose archive import support
varies by image store. Existing output files are never overwritten. A failed
build does not publish a partial archive.

By default the command creates a temporary `docker-container` Buildx builder
and removes it afterward. For repeated builds, reuse a builder and its cache:

```bash
docker buildx create --name workspace-images --driver docker-container
conda workspace image -e runtime --platform linux-64 \
  --builder workspace-images -t myapp:latest --load -- python -m myapp
```

An explicitly selected builder remains available after the command. OCI archive
exports require a supporting driver such as `docker-container`. See Docker's
[OCI exporter documentation](https://docs.docker.com/build/exporters/oci-docker/).

## Inspect the inputs

```bash
conda workspace image -e runtime --platform linux-64 \
  -t myapp:latest --load --dry-run --json -- python -m myapp
```

The preview includes the recipe, builder helper, selected files, environment,
platform, base, tags, and destination. Successful builds return these artifact
identifiers in JSON: `environment`, `workspace`, `prefix`, `platform`,
`oci_platform`, `tags`, `output`,
`load`, `push`, `digest`, and `image_id`. Build logs go to stderr.

Project files follow the existing [archive selection rules](../features/archives.md).
Git workspaces include tracked files, plus the manifest and lockfile when
allowed by the archive filters. Required activation scripts and local package
sources must be included. All eligible files beneath a local package directory
are required because arbitrary Python build backends can read those files.

Git and URL Python dependencies, editable installs, absolute or external local
source paths, and local conda channels are rejected. Container configuration
schemas, multi-architecture image indexes, and task-oriented entrypoints are
outside this command's initial scope.
