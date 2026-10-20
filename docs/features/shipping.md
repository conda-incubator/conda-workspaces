# Native launchers

`conda workspace ship` builds a native launcher from a locked workspace
environment through [conda-ship](https://github.com/conda-incubator/conda-ship).
The launcher installs the locked packages into its managed prefix on first
use, then runs an executable from that environment. Later invocations reuse
the prefix.

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
The executable must be supplied by the locked packages.

## Package bundles

Online launchers download their locked packages on first use. For offline
deployment, external and embedded layouts download packages during the build.
External layouts write a separate bundle to distribute with the launcher.
Embedded layouts carry the bundle inside the executable.

![Build embedded and external bundles and run both launchers offline](../../demos/ship-bundle.gif)

See [Ship a workspace environment](../how-to/ship.md) for manifest settings,
platform selection, bundle options, and the distinction between launchers,
container images, and workspace archives.
