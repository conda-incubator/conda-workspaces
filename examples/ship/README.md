# Locked Python launcher

This example builds a native launcher for a locked Python environment. The launcher installs that environment into a managed prefix on first use and passes its arguments to Python. No project source files or manifest tasks are copied into the artifact.

The integration requires an unreleased conda-ship development build with the `--manifest`, `--source-lock`, and `--source-environment` build options from [conda-ship#147](https://github.com/conda-incubator/conda-ship/pull/147). conda-ship 0.10.0 does not support these options. Install a compatible development build in the Python environment running conda-workspaces.

## Build and run

From this directory on Linux x86-64:

```bash
conda workspace ship -e runtime --platform linux-64 -o dist
CONDA_SHIP_PREFIX="$PWD/.runtime" ./dist/workspace-python --version
```

Use `--platform osx-arm64` on Apple Silicon. On Windows x86-64:

```powershell
conda workspace ship -e runtime --platform win-64 -o dist
$env:CONDA_SHIP_PREFIX = "$PWD/.runtime"
.\dist\workspace-python.exe --version
```

The build needs a conda-ship runtime template for the selected platform. Changing `--platform` does not cross-compile that template.

Building reads the existing `conda.lock` without solving, modifying the manifest or lockfile, or installing a workspace environment. The online launcher downloads and installs its locked packages when first run. Subsequent invocations reuse `.runtime`.

For a launcher with embedded package archives:

```bash
conda workspace ship -e runtime --platform linux-64 -o dist-embedded \
  --artifact-layout embedded
CONDA_SHIP_PREFIX="$PWD/.runtime-embedded" ./dist-embedded/workspace-python --version
```

An embedded build downloads the packages, then the launcher extracts and installs them on first use. `--artifact-layout external` writes a separate package bundle instead. `--delegate-executable` overrides the manifest's `python` delegate.

## Preview or update

```bash
conda workspace ship -e runtime --platform linux-64 -o dist --dry-run --json
```

The preview performs no downloads or artifact writes. After changing environment requirements, regenerate the lockfile explicitly before building:

```bash
conda workspace lock
```

See the [shipping guide](../../docs/how-to/ship.md) for artifact layouts and limitations.
