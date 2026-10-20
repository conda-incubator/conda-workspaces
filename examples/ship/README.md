# Locked Python launcher

This example builds a native command-line launcher for a locked Python environment. The launcher installs that environment into a managed prefix on first use and passes its arguments to Python. No project source files or manifest tasks are copied into the artifact.

Install conda-ship 0.11.1 or later in the Python environment that runs conda:

```bash
conda install -n base "conda-forge::conda-ship>=0.11.1"
```

Replace `base` if conda runs from another environment. The conda-forge builder package requires Python 3.11 or later there.

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

## Give the launcher to a recipient

Publish the complete output directory, including the launcher and its checksum, lock, package list, SBOM, and build metadata files. Include the separate bundle for an external build. Recipients need a compatible operating system and architecture, any system libraries required by the locked packages, and a writable, persistent prefix. They do not need conda, conda-workspaces, or the conda-ship builder installed.

Online launchers download packages on first use. Without `CONDA_SHIP_PREFIX`, this example installs into `~/.conda/workspace-python`. Embedded launchers carry the package archives. For an external build on Linux x86-64:

```bash
conda workspace ship -e runtime --platform linux-64 -o dist-external \
  --artifact-layout external
```

The recipient extracts the bundle using `tar` with Zstandard support, then runs:

```bash
mkdir packages
tar -xf dist-external/workspace-python.bundle.tar.zst -C packages
WORKSPACE_PYTHON_BUNDLE="$PWD/packages" WORKSPACE_PYTHON_OFFLINE=1 \
  CONDA_SHIP_PREFIX="$PWD/.runtime-external" \
  ./dist-external/workspace-python --version
```

`WORKSPACE_PYTHON_BUNDLE` names the extracted directory. `WORKSPACE_PYTHON_OFFLINE=1` selects conda-ship's offline bootstrap mode. In conda-ship 0.11.1, offline mode can still download missing packages. Supply the complete bundle and test with network access disabled when preparing an offline deployment. These names derive from this example's `runtime-name`.

## Preview or update

```bash
conda workspace ship -e runtime --platform linux-64 -o dist --dry-run --json
```

The preview performs no downloads or artifact writes. Detailed build plans go to stderr. JSON on stdout contains `success`, `environment`, `platform`, `output`, and `dry_run`. A preview does not check template/platform compatibility or prove that the built launcher will run.

After changing environment requirements, regenerate the lockfile explicitly before building:

```bash
conda workspace lock
```

An existing ready prefix can be reused even when a rebuilt launcher's version or locked packages have changed. Use a fresh `CONDA_SHIP_PREFIX` to test a changed build. The [shipping guide](../../docs/how-to/ship.md#distribute-and-maintain-the-launcher) includes an opt-in `install-name` recipe that derives a new prefix from the version and runtime lock, plus release, signing, and cleanup guidance. Older prefixes remain on disk.

The guide also includes a [packaged application example](../../docs/how-to/ship.md#ship-a-packaged-application) using Ruff.
