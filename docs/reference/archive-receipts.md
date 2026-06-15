# Archive receipt reference

This page describes the external receipt format written by
`conda workspace archive --receipt` and verified by
`conda workspace unarchive --receipt`.

Archive receipts are sidecar JSON documents. They use the
[in-toto Statement v1][in-toto-statement] envelope and a
conda-workspaces predicate schema that binds a workspace archive to the
manifest, lockfile, and package inventory it was created from.

[in-toto-statement]: https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md

## Status

| Field | Value |
|---|---|
| Format | Workspace archive receipt |
| Format version | `1` |
| JSON schema | `https://conda-incubator.github.io/conda-workspaces/workspace-archive-receipt-1.schema.json` |
| Source schema | [`schema/workspace-archive-receipt-1.schema.json`](https://github.com/conda-incubator/conda-workspaces/blob/main/schema/workspace-archive-receipt-1.schema.json) |
| Statement type | `https://in-toto.io/Statement/v1` |
| Predicate type | `https://conda-incubator.github.io/conda-workspaces/workspace-archive-receipt-1.schema.json` |
| Producer | `conda workspace archive --receipt [PATH]` |
| Consumer | `conda workspace unarchive ARCHIVE --receipt [PATH]` |

## File naming

When `--receipt` is passed without a path, conda-workspaces writes or
reads a sibling file named after the archive:

```text
my-project.tar.zst
my-project.tar.zst.receipt.json
```

Pass an explicit path to store the receipt elsewhere:

```bash
conda workspace archive --receipt attestations/my-project.json -o my-project.tar.zst
conda workspace unarchive my-project.tar.zst --receipt attestations/my-project.json
```

The receipt path must be separate from the archive path.

## Statement structure

A receipt is a JSON object with the in-toto Statement fields
`_type`, `subject`, `predicateType`, and `predicate`.

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {
      "name": "my-project.tar.zst",
      "digest": {
        "sha256": "<archive sha256>"
      }
    },
    {
      "name": "conda.toml",
      "digest": {
        "sha256": "<manifest sha256>"
      }
    },
    {
      "name": "conda.lock",
      "digest": {
        "sha256": "<lockfile sha256>"
      }
    }
  ],
  "predicateType": "https://conda-incubator.github.io/conda-workspaces/workspace-archive-receipt-1.schema.json",
  "predicate": {
    "archive": {
      "formatVersion": 1,
      "options": {}
    },
    "workspace": {
      "manifest": "conda.toml",
      "lockfile": "conda.lock"
    },
    "environments": []
  }
}
```

Receipts are written as stable, sorted, UTF-8 JSON. The loader rejects
duplicate JSON object keys because duplicate keys can make an integrity
document ambiguous.

## Subjects

The `subject` array records SHA-256 digests for:

- the archive file, using the archive basename as the subject name
- the workspace manifest path as stored inside the archive
- the `conda.lock` path as stored inside the archive

Receipt creation requires the selected archive filters to include the
workspace manifest and `conda.lock`. If `[workspace.archive].include`,
`[workspace.archive].exclude`, or `--exclude` would omit either file,
the archive command fails before writing the archive or receipt.

## Predicate

The predicate contains three sections.

| Section | Required fields | Description |
|---|---|---|
| `archive` | `formatVersion` | Receipt format version. `options` records archive options such as `bundle`, `lock`, `include`, `exclude`, and `compressionLevel` when available. |
| `workspace` | `manifest`, `lockfile` | POSIX archive-relative paths to the manifest and lockfile that must verify after extraction. |
| `environments` | `name`, `packages` | Lockfile-derived package inventory for each workspace environment. |

Environment records may include `prefix`. Prefixes inside the workspace
are stored as archive-relative POSIX paths such as
`.conda/envs/default`; external runtime prefixes remain absolute, using
POSIX or Windows syntax as appropriate.

Package records are normalized from `conda.lock`. Records may include:

| Field | Description |
|---|---|
| `name` | Package name |
| `version` | Package version |
| `build` | Package build string |
| `build_number` | Package build number |
| `subdir` | Conda platform subdir |
| `channel` | Package channel URL |
| `url` | Package artifact URL |
| `fn` | Package artifact filename |
| `sha256` | Package artifact SHA-256 digest |
| `md5` | Package artifact MD5 digest |

Package URLs and channel URLs are redacted before they are written to a
receipt. Embedded credentials, Anaconda tokens, query strings, and URL
fragments are removed.

## Verification

`conda workspace unarchive ARCHIVE --receipt [PATH]` verifies in this
order:

1. Load the receipt JSON and reject duplicate object keys.
2. Validate the in-toto Statement type, predicate type, and receipt
   format version.
3. Verify the archive file's SHA-256 digest before extraction.
4. Extract to a temporary staging directory under the target parent,
   using the same archive path traversal protections as regular
   extraction.
5. Verify the extracted manifest and lockfile SHA-256 digests.
6. Recompute the package inventory from the extracted `conda.lock` and
   compare it with the receipt.
7. Move the staged directory into the requested target.

Verified extraction refuses to use an existing symlink target, existing
file target, or non-empty directory target. This prevents an attacker
from satisfying receipt paths with pre-existing files outside the
staged extraction.

Pass `--require-sha256` with `--receipt` to require every compared
package record to include SHA-256. Without it, the receipt still
compares all package identity and digest fields that are present.

## Trust model

Archive receipts are integrity documents, not signatures. They detect
whether the archive, extracted manifest, extracted lockfile, or lockfile
package inventory differs from the receipt, but they do not prove who
created the receipt.

For provenance-sensitive workflows, pair receipts with
`conda workspace lock --sign` and verify the resulting
`conda.lock.sigstore.json` with `conda workspace install --locked --verify`
or `conda workspace unarchive --verify`. For air-gapped workflows, pair
`--receipt` with `--bundle` so the archive carries the package artifacts
and the receipt carries the lockfile inventory that should describe them.
`unarchive` primes a conda package cache from bundled packages only after
receipt verification or attestation verification plus package hash checks
against the attested lockfile.

See [Workspace attestation reference](workspace-attestations.md) for the
Sigstore bundle format and verification contract.
