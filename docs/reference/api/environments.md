# Environments

Environment creation, removal, and inspection via conda's APIs.

```{eval-rst}
.. automodule:: conda_workspaces.envs
   :members:
   :undoc-members:
```

## Read and select a workspace lock

`CondaLockLoader` inspects saved environments and reconstructs exact conda records without package-cache access when `metadata_only=True` is requested. Use `select()` to extract source entries, including their metadata, instead of composing a new lock from reconstructed records.

```python
from pathlib import Path

from conda.common.serialize.yaml import dumps
from conda_workspaces.lockfile import CondaLockLoader

loader = CondaLockLoader(Path("conda.lock"))
print(loader.available_environments)
print(loader.platforms_for("test"))
selected = loader.select({"test": ["linux-64"]})
Path("selected.lock").write_text(dumps(selected), encoding="utf-8")
```

Selection preserves root, environment and package metadata and removes unused records. Selected external references, missing packages, inconsistent identities or hashes and invalid channels fail explicitly. The loader redacts URL credentials as on other read paths. Callers requiring unchanged authenticated URLs must reject credential-bearing input before construction.

`package_platform_for(target, name)` returns the concrete conda subdir inferred from a saved target name and its package records. It returns `None` for a logical target with only noarch packages or no packages when the backing subdir is unknown. Source selection remains possible for that target, while an export requiring a concrete platform needs additional information. Pass a known subdir to `env_for(..., package_platform=subdir, metadata_only=True)` to keep the logical target separate from the package platform.

```{eval-rst}
.. autoclass:: conda_workspaces.lockfile.CondaLockLoader
   :members: available_environments, platforms_for, package_platform_for, select, env_for
```

## Check a saved lock against its manifest

`check_lockfile_satisfiability(config, lockfile_data, current_platform)` checks workspace declarations and the package requirements for one target. Call it for every declared logical target to check all saved solutions. Its `LockfileStatus.reason` explains the first mismatch.

Pass `environment="test"` to check package requirements only for that environment on the requested target. All workspace environment names, ordered channels and declared target names remain checked. This lets callers scope virtual package configuration independently for each environment and target without copying or removing declarations. An unknown environment raises `EnvironmentNotFoundError`.

The checker uses conda's virtual package plugins and the active override configuration. Callers requiring host-independent results must configure target virtual packages before each call. Success describes manifest satisfiability, without checking for newer packages or verifying package archives.

```{eval-rst}
.. autofunction:: conda_workspaces.lockfile.load_lockfile_data

.. autofunction:: conda_workspaces.lockfile.check_lockfile_satisfiability
```
