# Manifests

Manifest parsers and the detection/registry system.

Each parser handles both workspace configuration and task definitions
for its file format.  The `manifests/` package is conda-workspaces'
internal substrate; the package-root modules `env_spec.py`,
`lockfile.py` and `export.py` sit on top and expose the public
plugin API.

`parse_text(path, content)` parses supplied text without discovering another
workspace. Malformed channel lists and dependency values raise
`WorkspaceParseError`. Conda dependency declarations must be strings or tables
of supported MatchSpec fields. Source-package fields such as `path` and `git`
are rejected. PyPI source declarations remain available in the parsed model.

Declare channels in `[workspace]` or `[feature.<name>]`. Environment and target
channel overrides are unsupported and raise an error.

Services accepting uploaded manifests can call
`parse_text(path, content, reject_url_credentials=True)` to reject embedded
authentication using the parser's credential checks. The default is `False`,
preserving existing local read behavior. Parser diagnostics redact URL
credentials regardless of this option.

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
