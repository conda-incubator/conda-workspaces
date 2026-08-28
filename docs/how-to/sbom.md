# Generate a workspace SBOM

Install conda-sboms into conda's base environment so conda can discover the
plugin:

```console
conda install -n base "conda-forge::conda-sboms>=0.3.0"
```

## Generate from a lockfile

Create or refresh `conda.lock`, then export the default environment for the
current platform:

```console
conda workspace lock
conda workspace sbom --file exports/default.cdx.json
```

To export a different environment or platform:

```console
conda workspace sbom \
  --environment test \
  --platform linux-64 \
  --file exports/test-linux-64.cdx.json
```

The command reads `conda.lock` as-is. It does not solve, update the lockfile, or
fetch package archives. For a named platform such as `linux-64-cuda`, pass
either its declared name or its backing subdir. Use the declared name when
multiple variants share one subdir.

## Generate from an installed prefix

Use `--from-prefix` to inventory an installed workspace environment:

```console
conda workspace sbom \
  --environment test \
  --from-prefix \
  --file exports/test-installed.cdx.json
```

Prefix mode supports the host platform only. If `--platform` names another
platform, the command stops with an error.

## Add product and author metadata

Only add product and author details you can verify:

```console
conda workspace sbom \
  --file exports/acme-runtime.cdx.json \
  --product-name "Acme Runtime" \
  --product-version "2026.08" \
  --product-manufacturer "Acme GmbH" \
  --product-manufacturer-url "https://acme.example" \
  --author-name "Alice Example" \
  --author-email "alice@acme.example" \
  --author-organization "Acme Product Security" \
  --author-organization-url "https://acme.example/security"
```

Pass `--product-name` and `--product-version` together.
`--product-manufacturer` requires both, and `--product-manufacturer-url` also
requires `--product-manufacturer`. `--author-email` requires `--author-name`,
while `--author-organization-url` requires `--author-organization`.

Without metadata flags, conda-sboms uses its configured plugin values. Once any
metadata flag is present, only values given on this command are used. Omitted
fields stay unset.

## Make the output reproducible

Pass `--reproducible` to omit `metadata.timestamp` and record
`cdx:reproducible=true`:

```console
conda workspace sbom --reproducible \
  --file exports/default.cdx.json
```

The flag takes precedence over `SOURCE_DATE_EPOCH`. If the SBOM needs a stable
timestamp instead, set `SOURCE_DATE_EPOCH`:

```console
SOURCE_DATE_EPOCH=1787184000 conda workspace sbom \
  --file exports/default.cdx.json
```

Without `--reproducible`, conda-sboms rejects an invalid, negative, or
unsupported `SOURCE_DATE_EPOCH` before writing output. Matching output byte for
byte also depends on unchanged inputs and the same conda-sboms and serializer
versions.

See [](../features/export.md) for how lockfile and prefix input differ and which
packages each mode includes. See [](../reference/cli.md) for every command
option.
