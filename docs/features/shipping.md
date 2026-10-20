# Native launchers

`conda workspace ship` builds a native command-line launcher for an executable
supplied by a locked workspace environment through
[conda-ship](https://github.com/conda-incubator/conda-ship). The recipient can run
it without installing conda or the builder. On first use, the launcher installs
the locked packages into its managed prefix, then starts the executable.
Later invocations reuse that prefix.

![Preview, build, and run a locked Python launcher](../../demos/ship.gif)

With the manifest from the [shipping guide](../how-to/ship.md), build and run
the launcher on Linux x86-64:

```bash
conda workspace ship -e runtime --platform linux-64 -o dist
CONDA_SHIP_PREFIX="$PWD/.runtime" ./dist/workspace-python --version
```

The build requires conda-ship 0.11.1 or later and a current `conda.lock`.
It preserves the manifest and lockfile and does not install a local workspace
environment. The selected platform must match conda-ship's runtime template.
Project source files, manifest tasks, and workspace activation settings are
not copied into the launcher.

## Package bundles

Online launchers download their locked packages on first use. For offline
deployment, external and embedded layouts download packages during the build.
External layouts write a separate bundle that the recipient extracts and
configures through the runtime's bundle variable. Embedded layouts carry the
bundle inside the executable and locate it automatically.

![Build embedded and external bundles and run both launchers offline](../../demos/ship-bundle.gif)

Replacing a launcher does not automatically refresh an existing managed
prefix. The [shipping guide](../how-to/ship.md) covers installation locations,
prefix naming for changed releases, recipient instructions, and release
responsibilities. It also compares launchers with container images and
workspace archives.
