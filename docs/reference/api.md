# API reference

```{toctree}
:hidden:

api/models
api/manifests
api/resolver
api/context
api/environments
api/execution
api/archive
api/attestations
```

::::{grid} 2
:gutter: 3

:::{grid-item-card} Models
:link: api/models
:link-type: doc

Workspace configuration, task definitions, features, environments,
dependencies, and channels.
:::

:::{grid-item-card} Manifests
:link: api/manifests
:link-type: doc

File format parsers, detection logic, and the parser registry.
:::

:::{grid-item-card} Resolver
:link: api/resolver
:link-type: doc

Feature-to-environment resolution.
:::

:::{grid-item-card} Context
:link: api/context
:link-type: doc

Lazy workspace context, template variables, and platform introspection.
:::

:::{grid-item-card} Environments
:link: api/environments
:link-type: doc

Environment creation, removal, and inspection via conda's APIs.
:::

:::{grid-item-card} Execution
:link: api/execution
:link-type: doc

Task DAG resolution, shell backends, caching, and template rendering.
:::

:::{grid-item-card} Archives
:link: api/archive
:link-type: doc

Workspace archive creation, receipt verification, extraction, and install.
:::

:::{grid-item-card} Attestations
:link: api/attestations
:link-type: doc

Workspace attestation construction, signing, and verification.
:::

::::
