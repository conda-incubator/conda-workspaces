# Manifests

Manifest parsers and the detection/registry system.

Each parser handles both workspace configuration and task definitions
for its file format.  The `manifests/` package is conda-workspaces'
internal substrate; the package-root modules `env_spec.py`,
`lockfile.py` and `export.py` sit on top and expose the public
plugin API.

```{eval-rst}
.. automodule:: conda_workspaces.manifests
   :members:
   :undoc-members:

.. automodule:: conda_workspaces.manifests.base
   :members:
   :undoc-members:

.. automodule:: conda_workspaces.manifests.toml
   :members:
   :undoc-members:

.. automodule:: conda_workspaces.manifests.pixi_toml
   :members:
   :undoc-members:

.. automodule:: conda_workspaces.manifests.pyproject_toml
   :members:
   :undoc-members:

.. automodule:: conda_workspaces.manifests.normalize
   :members:
   :undoc-members:
```
