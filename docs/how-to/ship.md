# Ship a workspace environment

`conda workspace ship` turns one locked environment into a native launcher
through [conda-ship](https://github.com/conda-incubator/conda-ship). The launcher
installs the locked packages into its managed prefix on first use, then runs
an executable from that environment. Later invocations reuse the prefix.

```{important}
This integration requires a development build of conda-ship with the
`--manifest`, `--source-lock`, and `--source-environment` build options.
These options have not been released. conda-ship 0.10.0 does not support them.
The input-selection support is proposed in
[conda-ship#147](https://github.com/conda-incubator/conda-ship/pull/147).
Install a compatible development build in the Python environment that runs
conda-workspaces.
```

## Prepare the manifest and lockfile

The selected environment must provide the executable you want to distribute.
For a Python launcher, add Python and the runtime settings to `conda.toml`:

```toml
[workspace]
name = "workspace-python"
channels = ["conda-forge"]
platforms = ["linux-64", "osx-arm64", "win-64"]

[dependencies]
python = "3.13.*"

[environments]
runtime = []

[tool.conda-ship]
runtime-name = "workspace-python"
runtime-version = "0.1.0"
delegate-executable = "python"
```

Generate `conda.lock` before building:

```bash
conda workspace lock
```

The ship command requires a current, complete `conda.lock`. It does not solve
dependencies, install a local workspace environment, or update the manifest
or lockfile. It passes the selected manifest and existing lockfile to
conda-ship, including when a Pixi-compatible manifest is used with a
conda-workspaces `conda.lock`.

## Build and run the launcher

Build for the native platform of the installed conda-ship runtime template.
For Linux x86-64:

```bash
conda workspace ship -e runtime --platform linux-64 -o dist
CONDA_SHIP_PREFIX="$PWD/.runtime" ./dist/workspace-python --version
```

The second command installs the locked Python environment into `.runtime` on
first use, then runs `python --version`. `CONDA_SHIP_PREFIX` selects the
launcher's managed prefix, independently of workspace environment prefixes.
It is optional when using conda-ship's configured installation location.

Use `--platform osx-arm64` on Apple Silicon. On Windows x86-64, select
`win-64` and run the generated executable from PowerShell:

```powershell
conda workspace ship -e runtime --platform win-64 -o dist
$env:CONDA_SHIP_PREFIX = "$PWD/.runtime"
.\dist\workspace-python.exe --version
```

`--platform` selects locked package records. It does not cross-compile a
launcher. conda-ship rejects a platform that does not match its runtime
template. Named workspace platform variants are not supported by this
integration. Use a declared conda subdir such as `linux-64`.

Set `--delegate-executable` to override the manifest's delegate. Arguments
passed to the resulting launcher go directly to that executable. The ship
command does not copy project source files or convert manifest tasks into
launcher commands. Applications must already be present in the locked packages
or distributed separately. Local path, Git, and URL PyPI dependencies are
unsupported. conda-ship also rejects package types it cannot install.

Workspace activation scripts and manifest activation variables are not copied
into the launcher. Runtime behavior is controlled by conda-ship.

The [shipping example](https://github.com/conda-incubator/conda-workspaces/tree/main/examples/ship)
contains this Python launcher configuration.

## Choose where package archives live

`--artifact-layout` overrides `[tool.conda-ship].artifact-layout`. Without either
setting, conda-ship uses `online`.

| Layout | Build behavior | First invocation |
| --- | --- | --- |
| `online` | Writes a launcher without package archives | Downloads and installs locked packages |
| `external` | Downloads packages and writes a separate bundle | Installs packages from the supplied bundle |
| `embedded` | Downloads packages and embeds the bundle in the launcher | Extracts the bundle and installs its packages |

For a launcher that carries its package archives:

```bash
conda workspace ship -e runtime --platform linux-64 -o dist-embedded \
  --artifact-layout embedded
CONDA_SHIP_PREFIX="$PWD/.runtime-embedded" ./dist-embedded/workspace-python --version
```

Bundle builds download packages but do not install a workspace environment.
Every layout installs a managed prefix when the launcher first runs. For
external bundles, distribute the bundle alongside the launcher and configure
its location as described in the
[conda-ship runtime reference](https://github.com/conda-incubator/conda-ship/blob/main/docs/reference/runtime-cli.md).

## Preview a build

```bash
conda workspace ship -e runtime --platform linux-64 -o dist --dry-run --json
```

`--dry-run` validates the workspace selection and previews the build without
downloading packages or writing artifacts. `--json` produces a structured
result on stdout, with build diagnostics on stderr.

Use the global `--file` option when several manifests exist:

```bash
conda workspace --file pyproject.toml ship -e runtime \
  --platform linux-64 -o dist
```

The selected manifest's `[tool.conda-ship]` settings control runtime naming,
version, delegate, and other conda-ship build policy. `-e` selects the source
environment for this build.

## Choose a delivery format

| Command | Result | When packages are installed |
| --- | --- | --- |
| `workspace ship` | Native launcher for a locked environment | On the launcher's first invocation |
| `workspace image` | Linux container image with environment and project files | During the image build |
| `workspace archive` | Workspace snapshot, optionally with package archives | During a separate installation after extraction |

Use [images](image.md) when the application needs copied project files and a
container runtime. Use [archives](../features/archives.md) to transfer a
workspace for further work or installation.
