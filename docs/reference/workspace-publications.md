(workspace-publications)=

# Published workspace references

:::{note}
This is the proposed publication convention for
[#211](https://github.com/conda-incubator/conda-workspaces/issues/211).
Named lookup, the settings, and the CLI options specified here await
implementation in [#212](https://github.com/conda-incubator/conda-workspaces/issues/212).
Direct archive URLs already work with `conda workspace quickstart`.
:::

The proposed everyday command selects a published starting snapshot:

```sh
conda workspace quickstart example-org/analysis
```

The configured source selects its default revision. An explicit revision
selects a particular publication:

```sh
conda workspace quickstart example-org/analysis:2026.11.06.1
```

Resolution produces an existing workspace archive and any declared receipt.
Quickstart restores it into a new directory, installs the selected environment
from its lockfile, and opens a shell. `--target`, `-e`, `--no-shell`, and JSON
output retain their [archive quickstart behavior](cli.md).
The archive filename determines the default destination, including when the
requested publication name differs from that filename.

The saved origin identifies the download. Local edits do not change that
record. `conda workspace install`, `update`, and `lock` continue to operate on
the restored workspace without consulting its publication source. Repeating
quickstart requires a new destination.

An immutable publication revision does not freeze the restored conda prefixes.
Conda's frozen-prefix marker from
[CEP 22](https://github.com/conda/ceps/blob/main/cep-0022.md) and workspace
lockfile options are separate mechanisms. Named bootstrap creates no
`conda-meta/frozen` marker.

## Publication approach

Version 1 uses one JSON publication document per workspace, served through
ordinary HTTP or HTTPS. Its revision entries point to existing archive and
receipt files. HTTPS is the default in examples and recommended for hosts.
HTTP and redirects retain the agreed
[remote archive transport policy](archive-receipts.md).

| Approach | What it supplies | Work still needed | Decision |
| --- | --- | --- | --- |
| Static HTTPS files | Ordinary file hosting, existing conda downloads, one lookup document | A small reference and descriptor format | Use for version 1 |
| OCI distribution with ORAS | Registry manifests, blobs, tags, digests, and platform descriptors | Registry client integration, workspace media types, default selection, and receipt association | Keep as a possible later transport |
| Service-specific APIs | Existing discovery and service build identities | An adapter for each service | Allow optional discovery outside generic retrieval |

[ORAS pull](https://oras.land/docs/commands/oras_pull/) supports tags and
digests, and the [OCI distribution protocol](https://github.com/opencontainers/distribution-spec/blob/v1.1.1/spec.md#pull)
is vendor neutral. Static files fit the existing download path without adding
a registry client. The design borrows OCI's separation of reference names
from [content digests](https://github.com/opencontainers/image-spec/blob/v1.1.1/descriptor.md).
The [open conda OCI proposal](https://github.com/conda/ceps/pull/115) covers
packages and channel metadata. Workspace media types, receipt associations,
and reference mapping would need a separate specification before native OCI
retrieval could replace this HTTP convention.

[Conda-store's download CLI](https://github.com/conda-incubator/conda-store/blob/main/conda-store/conda_store/_internal/cli.py)
resolves `namespace/name` to `current_build_id`, or accepts an explicit build
ID belonging to that environment. This supports a source-defined default
instead of assuming a universal `latest` tag. Conda-store's downloadable
conda-pack archives are not automatically workspace archives.

[Nebi's OCI bundle import](https://nebi.nebari.dev/registry-setup/) can restore
pixi manifests, locks, and assets without a Nebi server. Its
[CLI separates registry import from server synchronization](https://nebi.nebari.dev/cli-reference/).
That is useful workflow precedent. Neither Nebi bundles nor pixi-pack files
become conda-workspaces archives by adopting their reference syntax.

## Reference syntax and input classification

A published reference is `owner/name` or `owner/name:selector`. There is
exactly one slash and, when present, exactly one colon after `name`.

| Component | Version 1 grammar |
| --- | --- |
| Owner and name | 1 to 64 lowercase ASCII letters, digits, dots, underscores, or hyphens, starting and ending with a letter or digit |
| Revision or tag selector | 1 to 128 ASCII letters, digits, dots, underscores, plus signs, or hyphens, starting with a letter or digit |
| Source alias | 1 to 64 lowercase ASCII letters, digits, underscores, or hyphens, starting with a letter |

Names and selectors are compared exactly. Clients do not case-fold,
percent-decode, perform Unicode normalization, or interpret selectors as
version ranges, dates, branches, or hashes. Empty components, whitespace,
percent escapes, backslashes, and additional path components are invalid in
a named reference. Slashes in URL inputs retain their URL meaning.

The future parser applies the following rules in order, before any network
request, source lookup, or check for a local file:

1. Explicit input selectors win. `--published` requires one valid named
   reference. `--archive` keeps its current meaning of one remote archive URL.
   `--package` treats all positionals as package specifications. These three
   flags are mutually exclusive. They select interpretation, not a fallback.
2. `--copy` and `--clone` select a local manifest source. Their positionals
   remain package specifications. They conflict with `--published`,
   `--archive`, and `--source`.
3. Preserve conda's explicitly qualified package syntax, including
   `channel::package` and bracketed selectors such as
   `numpy[url=https://packages.example.org/numpy-2.0-0.conda]`. Validate these
   with `MatchSpec`. Do not identify them merely by finding a URL substring.
4. Keep #210's URL classification. Registered package URLs remain package
   inputs, including `.conda` and `.tar.bz2`. Recognized workspace archive
   URLs select restoration. `--archive` disambiguates a `.tar.bz2` archive.
   An unsupported URL produces an error.
5. Recognize explicit local path syntax: a POSIX absolute path, a Windows
   drive or UNC path, or a value beginning with `./`, `../`, `~/`, or their
   Windows separator equivalents. A supported local package filename remains
   a package input. Other paths require `--copy PATH` or `--clone PATH`.
   Positional local workspace archives are not added by this design.
6. A relative filename ending in a registered conda package suffix remains
   a local package input, even if it has the form `owner/name`.
7. A token matching the named-reference grammar selects a published workspace.
   It must be the only positional. Every remaining token goes through the
   existing package-specification path.

| Input | Interpretation |
| --- | --- |
| `example-org/analysis` | Named publication, even if a local directory has that name |
| `example-org/analysis:stable` | Named publication with an explicit selector |
| `conda-forge::numpy` | Conda package specification |
| `https://conda.anaconda.org/conda-forge::numpy` | URL-qualified package specification |
| `https://files.example.org/analysis.tar.gz` | Workspace archive URL |
| `https://files.example.org/pkg-1.0-0.tar.bz2` | Package URL unless `--archive` is supplied |
| `downloads/pkg-1.0-0.conda` | Local package path |
| `--published team/package.conda` | Publication whose name collides with a package suffix |
| `--package team/package` | Package-specification interpretation, subject to conda validation |
| `--copy team/analysis numpy` | Copy that local manifest and add `numpy` |
| `./team/analysis` | Local-path error pointing to `--copy` |

Malformed inputs never trigger a second interpretation after a failed
request. Multiple publications, or a publication mixed with package specs,
fail before retrieval. Named bootstrap retains archive quickstart's option
conflicts, including `--file`, `--format`, package-editing options, and
`--frozen`. `--locked` remains accepted and redundant.

(workspace-source-configuration)=

## Source configuration

Register two settings with conda's
[`conda_settings` hook](https://docs.conda.io/projects/conda/en/stable/dev-guide/plugins/settings.html):

| Setting under `plugins` | Type | Default | Meaning |
| --- | --- | --- | --- |
| `workspace_sources` | Map from source alias to base URL | Empty map | Optional names for publication roots |
| `workspace_default_source` | String | Empty string | Alias or base URL used when `--source` is absent |

`workspace_sources` uses
`MapParameter(PrimitiveParameter("", element_type=str))` and
`workspace_default_source` uses `PrimitiveParameter("", element_type=str)`.
Read effective settings through `context.plugins`. Conda owns configuration
file precedence, map merging, environment overrides, and parameter type
validation. The resolver validates alias and URL syntax when selecting a
source. Source resolution must work before a `WorkspaceContext` exists.

There is no built-in host. Do not select the only configured alias implicitly.
When no usable default exists, report one setup action, with the placeholder
identified as the user's publication root:

```sh
conda config --set plugins.workspace_default_source https://workspaces.example.org/
```

That setting is sufficient for subsequent `quickstart owner/name` commands.
Aliases are optional:

```yaml
plugins:
  workspace_sources:
    lab: https://research.example.org/workspaces/
    community: https://community.example.net/catalog/
  workspace_default_source: lab
```

The equivalent alias setup uses existing conda configuration commands:

```sh
conda config --set plugins.workspace_sources.lab https://research.example.org/workspaces/
conda config --set plugins.workspace_default_source lab
```

For named inputs, `--source ALIAS_OR_URL` overrides the configured default:

```sh
conda workspace quickstart example-org/analysis --source community
conda workspace quickstart example-org/analysis --source https://other.example.org/catalog/
```

An explicit archive URL selects its own location and does not read source
configuration. Combining it, package inputs, or local copying with `--source`
is an error. Unknown aliases and invalid selected URLs fail without trying
another source. Direct archive retrieval does not resolve or validate unused
aliases or source URLs. Normal conda configuration parsing and type errors
still apply.

A base URL is an absolute HTTP or HTTPS URL with a hostname and optional path.
It cannot contain user information, a query, a fragment, or embedded
Anaconda token components. Append a trailing slash when absent. Reject
literal or percent-encoded dot path components, separators within encoded
components, and control characters. A source alias always maps directly to
a URL, never to another alias.

Package `channel_alias`, `channels`, `default_channels`, `custom_channels`,
and channel priority keep their existing package-resolution meaning. A
workspace source is never inferred from them. Conda's
[channel alias setting](https://docs.conda.io/projects/conda/en/stable/user-guide/configuration/settings.html#channel-alias-set-a-channel-alias)
is precedent for configurable name expansion, not an endpoint for these
publications.

## Static publication convention, version 1

A normalized source base `B` publishes the reference `owner/name` at:

```text
B + v1/owner/name/index.json
```

For `B = https://research.example.org/workspaces/` and
`example-org/analysis`, the only metadata request is:

```text
https://research.example.org/workspaces/v1/example-org/analysis/index.json
```

This document contains the default, revisions, and tags in one response.
The client validates it and resolves the requested selector once from that
response. It does not fetch a separate tag document, list a directory,
negotiate a provider API, or search another host.

The following descriptor is illustrative. A publisher must replace the
example digests and byte counts with those of its actual files:

```json
{
  "version": 1,
  "name": "example-org/analysis",
  "default": "stable",
  "tags": {
    "stable": "2026.11.06.1",
    "latest": "2026.11.06.1"
  },
  "revisions": {
    "2026.11.06.1": {
      "archive": {
        "type": "conda-workspaces-archive-v1",
        "url": "https://files.example.org/analysis-2026.11.06.1.tar.zst",
        "sha256": "1111111111111111111111111111111111111111111111111111111111111111",
        "size": 4096
      },
      "receipt": {
        "url": "https://files.example.org/analysis-2026.11.06.1.tar.zst.receipt.json",
        "sha256": "2222222222222222222222222222222222222222222222222222222222222222",
        "size": 2048
      },
      "platforms": ["linux-64", "osx-arm64", "win-64"]
    }
  }
}
```

### Document fields and validation

| Field | Requirement |
| --- | --- |
| `version` | Required integer `1` |
| `name` | Required exact `owner/name` that was requested |
| `revisions` | Required nonempty object mapping immutable revision names to entries |
| `tags` | Optional object mapping tag names directly to existing revision names |
| `default` | Optional revision or tag name, which must exist when supplied |
| Entry `archive` | Required archive descriptor |
| Entry `receipt` | Optional descriptor for an existing archive receipt |
| Entry `platforms` | Optional nonempty array of distinct conda platform subdir strings |

Revision names and tag names use the selector grammar. Their sets must be
disjoint. Tags cannot refer to other tags. Each archive descriptor requires
`type`, `url`, `sha256`, and `size`. Each receipt descriptor requires `url`,
`sha256`, and `size`. Version 1 rejects unknown fields, duplicate JSON object
keys, null values, wrong types, non-finite numbers, and unsupported versions.
JSON booleans are not integers for `version` or `size`.

Host catalog fields such as descriptions, README content, licenses, owner
profiles, and search results belong in a separate response or page. They
cannot be added to this strict version 1 document. This rule does not apply
to extensible conda package records inside locks or receipts.

Publication documents are UTF-8 JSON objects of at most 1 MiB. Limit nesting
to eight containers and the combined number
of revision and tag entries to 4,096. These limits are independent of
`--allow-large-archive`. Fetch metadata through the shared bounded download
path and reject HTML error pages as malformed metadata. Servers should send
`Content-Type: application/json`, but generic static-host content types are
accepted when the body validates.

Descriptor URLs must be absolute HTTP or HTTPS URLs with a hostname, without
user information or fragments. Query strings, including expiring signed URLs,
are permitted. A descriptor cannot define request headers, credentials,
configuration changes, or verification exemptions. URLs on another host use
that host's own conda authentication settings when fetched directly.
Redirects use the existing archive download policy, which strips credential
headers when the origin changes. Do not assume a redirect selects a new
destination-specific authentication handler.

The archive type is exactly `conda-workspaces-archive-v1`. This identifies the
existing workspace archive layout and supported manifest formats. It does
not introduce another archive format. The decoded URL basename must be a
safe single filename ending in `.tar.zst`, `.tar.zstd`, `.tar.gz`, `.tgz`, or
`.tar.bz2`. Named resolution has already selected an archive, so `.tar.bz2`
needs no `--archive` flag here. Preserve that basename during download for
receipt subject matching. Redirects cannot rename it or choose the target.

`sha256` is 64 lowercase hexadecimal characters. `size` is a positive integer
count of file bytes, excluding HTTP framing. The existing bounded downloader
requests identity HTTP content encoding and rejects other content encodings.
Hosts must serve metadata, archives, and receipts accordingly. Archive
compression such as `.tar.zst` remains part of the file bytes. Check size and
digest before archive decompression or receipt JSON parsing. Do not hash a
reserialized receipt or extracted files in place of the downloaded objects.
Existing archive and receipt limits still apply, even if a descriptor claims
a larger size. A mismatch is fatal.

`platforms` advertises the union of installation target subdirs available
across the published environments. Derive it from the manifest and lockfile
environment targets, not package subdirs. Exclude `noarch`, as required for
environment targets by [CEP 24](https://github.com/conda/ceps/blob/main/cep-0024.md).
Compare it with the same effective target platform used by the existing
archive installer, including conda configuration overrides. If that target
is absent, reject the selection before downloading the archive. If the field
is absent, defer the check to the archive.

This is an early filter, not a claim that the physical machine can run every
package. The manifest, lockfile, and `LockfileInstallPlan.validate_virtual_packages`
remain authoritative for the selected environment and its OS, CPU, and GPU
requirements. No platform-specific artifact routing, solver fallback, or
choice among several archives is introduced.

### Defaults, tags, and immutable identities

An omitted selector uses `default`. If `default` is absent, fail with guidance
to pass `owner/name:revision`. Never guess `latest`, choose the highest version,
or use the first revision in the document. An explicit selector chooses its
revision entry or follows one tag to its revision. Unknown selectors fail.

A publisher must never reassign a revision name to different archive bytes
or change its receipt association. Tags and `default` may change. `latest`
has no special client behavior and exists only when the source declares it.
To retain a reproducible selection, keep the canonical revision name, source
base, archive digest, and any declared receipt digest. The digest identifies
the bytes even if the URL is replaced or expires. A publisher may refresh an
expiring URL while preserving that revision's content digests and filenames.
It must preserve the advertised environment targets as well. Adding a receipt
to a receipt-free revision, replacing a receipt, or changing the archive
requires a new revision.

For example, `analysis:stable` might resolve to revision `2026.11.06.1` today
and `2026.11.20.1` later. An invocation freezes one entry and verifies the
downloaded archive and declared receipt against its digests. It must not
resolve `stable` again during installation. `analysis:2026.11.06.1` selects
the immutable publication by name. Keeping the digest also lets another tool
detect a publisher that violates the immutable-revision rule.

The digest checks bind retrieval to the lookup response. They do not mark a
receipt-free operation as receipt-verified, authenticate the publisher, or
add a standalone checksum CLI option. Direct archive URLs retain their
existing optional-receipt policy.

Version 1 clients do not persist publication documents or use stale metadata
after a failed request. Request revalidation with `Cache-Control: no-cache`.
Publishers should do the same for `index.json`. Independent fresh quickstart
invocations may see different tag targets. Offline named lookup fails before
network access. Ordinary package caching is unaffected.

(publication-receipt-selection)=

## Receipt selection and verification

Apply the policy from
[#208](https://github.com/conda-incubator/conda-workspaces/issues/208)
to the resolved archive:

| Source declaration | Explicit receipt option | Required checks |
| --- | --- | --- |
| Absent | Absent | Normal archive and package checks, plus descriptor size and digest |
| Present | Absent | Automatically retrieve and fully verify the declared receipt |
| Absent | `--receipt` | Derive and require the conventional sibling |
| Absent | `--receipt PATH` or `--receipt URL` | Require the specified receipt |
| Present | Any explicit receipt selection | Require both the declared and explicitly selected receipt |

Bare `--receipt` always means the conventional sibling of the resolved
archive URL. It does not mean "accept whichever receipt the source declares".
As for direct URLs, a query or fragment that prevents reliable sibling
derivation requires an explicit receipt location. If a declared and explicit
remote location are exactly the same URL, retrieve it once. Otherwise, obtain
both. Do not deduplicate on redacted URLs, which can hide different credentials
or query parameters.

Every selected receipt's whole-archive digest must pass before archive
inspection or extraction. Then each receipt must pass the existing manifest,
lockfile, and package-inventory checks against the single extraction staging
tree. For installation, every receipt must bind the root manifest and
`conda.lock` actually selected by the installer. Verifying other files in the
same tree is insufficient. Receipts need not be byte-identical.
The source-declared receipt must also match its descriptor size and digest.
`--require-sha256` applies to every selected receipt's package records. A
source declaration satisfies its requirement for a selected receipt even
without a `--receipt` flag. With no receipt selected, the flag is an error.

For every bundled package staged for installation, every selected receipt
must satisfy the existing bundled-package identity and cache-metadata checks.
Missing or conflicting package metadata fails verification. Do not select
one receipt's inventory to bypass another receipt's checks.

All required checks finish before publishing the target, staging bundled
packages for installation, or starting installation or a shell. Failure of
any selected receipt aborts the operation. There is no flag to drop a source
declaration. Without a declared or requested receipt, do not request a sibling,
do not stage bundled packages, and report `verified: false`.

The existing receipt verifier owns receipt format and inventory validation.
Supporting several selected receipts must reuse those checks and the same
staged files. It must not create a second extraction or receipt parser.
Unsigned receipts and source descriptors establish consistency with the
selected source. They do not independently authenticate a publisher.

[CEP 27](https://github.com/conda/ceps/blob/main/cep-0027.md) defines conda
package publish attestations, and
[CEP 50](https://github.com/conda/ceps/blob/main/cep-0050.md) defines their
distribution. Neither defines the workspace receipt predicate or which
workspace publishers a client should trust. Future workspace signatures or
appendable attestations need an explicit format and verification policy.
They must not silently change a revision's immutable receipt association.

## Resolution result and saved origin

Resolution is independent of workspace manifest parsing. It returns the
requested reference, selected source, requested selector, canonical revision,
archive descriptor, optional platforms, and selected receipt locations.
Only the shared archive path interprets the downloaded archive and manifests.

After all verification succeeds, write a versioned origin record to
`.conda/workspace-origin.json` in the extraction staging tree before publishing
the target or starting installation. If writing fails, fail restoration and
remove staging as usual. This ensures an installation failure still leaves
the restored workspace with its original download identity.

| Origin field | Stored value |
| --- | --- |
| `version` | Integer `1` |
| `requested_reference` | Original valid `owner/name[:selector]` |
| `source_alias` | Selected alias, or null when a base URL was selected directly |
| `source_url` | Selected normalized source base, redacted |
| `publication_url` | Requested lookup URL, redacted |
| `requested_selector` | Explicit selector, or null for the source default |
| `selected_selector` | Selector chosen from the request or `default` |
| `resolved_revision` | Canonical immutable revision name |
| `archive` | Object with `type`, `url`, `basename`, `sha256`, and `size` |
| `receipts` | Array of objects with `selected_by`, `url`, `sha256`, and `size` |
| `receipt_verified` | True only when at least one receipt was selected and all selected receipts verified |

In `archive`, `type` is `conda-workspaces-archive-v1`, `url` is the redacted
archive URL, and `basename` is the filename used for receipt matching. Both
`sha256` and `size` record the verified file bytes using the descriptor types.

Each receipt's `selected_by` is a nonempty array containing `"source"`,
`"user"`, or both in that order, without duplicates. Its `url` is a redacted
remote URL or null for a local receipt. Its `sha256` and `size` record the
verified receipt file bytes using the descriptor types. A deduplicated
download selected by both mechanisms has one entry with
`"selected_by": ["source", "user"]`. With no selected receipts, `receipts`
is an empty array and `receipt_verified` is false.

Do not store local receipt paths, raw publication documents, HTTP headers,
credentials, URL queries, or fragments. Redacted URLs are descriptive and may
not be replayable. Digests and the canonical revision retain the content
identity. The file is a record of the original download, never an instruction
to fetch or update anything.

Reserve this origin path for client-generated state. Named bootstrap must
reject archive-supplied entries at that path and unsafe ancestors before
writing. The implementation must exclude it when creating subsequent
workspace archives, so a publisher cannot supply a counterfeit download
record. Later workspace operations must neither trust it as authorization
nor alter it to describe local edits.

Quickstart's JSON result gains an `origin` object with the same fields. Keep
existing `source` as the redacted archive URL and `artifact` as its basename.
For compatibility, `receipt` identifies the declared receipt when present,
otherwise the user-selected receipt. The complete set is in `origin.receipts`.
`verified` is true only when all selected receipts verified. Human output
reports the selected source, resolved revision, archive identity, and target.

`--dry-run` performs lookup, downloads, descriptor checks, receipt verification,
and the existing locked-environment preview. It returns the prospective origin
in JSON, but writes no target or persistent origin record. On an installation
failure after restoration, retain the origin and use the existing recovery
commands. Recovery never resolves a publication again.

## Independent host examples

A minimal workspace host consists of static object storage and a publishing
job. The job creates an archive and optional receipt, uploads the final files,
and atomically updates the workspace index. An institute can serve this tree
from any static HTTPS host:

```text
https://research.example.org/workspaces/
  v1/example-org/analysis/index.json
  artifacts/analysis-2026.11.06.1.tar.zst
  artifacts/analysis-2026.11.06.1.tar.zst.receipt.json
```

The publication's absolute descriptor URLs point to the two `artifacts/`
files. Configure that root once, then bootstrap:

```sh
conda config --set plugins.workspace_default_source https://research.example.org/workspaces/
conda workspace quickstart example-org/analysis
```

An independent community can host only the lookup document under its own
root and use existing release storage for the archive:

```text
https://community.example.net/catalog/v1/example-org/analysis/index.json
https://releases.example.net/example-org/analysis/2026.11.06.1/analysis.tar.gz
```

Its descriptor points to that archive URL. If no receipt was published, omit
the `receipt` field. The client then installs with normal package checks and
leaves bundled packages unused:

```sh
conda workspace quickstart example-org/analysis:2026.11.06.1 \
  --source https://community.example.net/catalog/ --target analysis-community --no-shell
```

Publish archive and optional receipt files before replacing `index.json`.
Compute each descriptor's size and SHA-256 from the final uploaded file bytes.
Publish metadata atomically so clients receive either complete generation.
Retain the artifacts referenced by published revisions, with immutable URLs
where possible. Concurrent publishers must detect conflicting index updates
so one publication cannot erase another. The size and entry limits apply to
the whole index. A host approaching either limit must stop adding entries
or introduce a separately specified protocol version, rather than silently
removing pinned revisions to make room.
Moving `default` or a tag changes future downloads only. Hosting requires
ordinary GET requests and does not require a package channel, Anaconda client,
upload API, directory listing, or server-side archive generation.

A larger host can add an authenticated upload API, owner permissions, and a
searchable catalog while serving the same read protocol. Its workspace page
can show available environments and target platforms, revision history,
archive digests, receipt status, and a pinned quickstart command with an
explicit source. Catalog metadata stays outside `index.json`. Receipt status
must remain distinct from any independently verified publisher identity.

For immutable object storage, use paths such as
`objects/sha256/<digest>/<original-basename>`. Keeping the original basename
preserves receipt subject matching. Each revision fixes the archive, receipt
association, and advertised environment targets. A publish operation checks
those values before accepting an existing revision name. Concurrent index
writes can use an ETag or storage-generation condition, or a serialized
publishing job. Private metadata and artifact endpoints use the client's
existing conda authentication settings for their respective URLs.

An OCI registry may supply the host's storage if the host exposes ordinary
HTTP archive downloads. That backend choice does not require an OCI client.
Optional build workers belong to the publishing service and run only on an
explicit build request. Looking up a workspace or inspecting its archive
does not run its manifest tasks. The client installs the selected locked
environment through conda.

Hosting an archive does not make the host a package channel. Keep lockfile
package locations and effective package metadata intact, including values
patched in repodata. [CEP 15](https://github.com/conda/ceps/blob/main/cep-0015.md)
already allows package metadata and artifacts on different hosts. Do not
rewrite package URLs to the workspace source or reconstruct package records
solely from embedded `info/index.json`.

## Authentication, failures, and implementation scope

Keep named lookup in the existing workspace subcommand. Register source
configuration through `conda_settings`, use `MatchSpec` for package inputs,
and leave manifest and lock conversion with the existing environment
specifier/exporter plugins and `Environment` model. The lookup document
resolves an archive and does not become another environment format.
`WorkspaceArchive` and the lockfile install plan own restoration and conda
transactions. No new conda core publication hook is needed.

Use conda sessions and the existing archive downloader for metadata, archives,
and remote receipts. Preserve its bounded streaming, retry behavior, redirect
credential handling, offline checks, proxy configuration, TLS verification,
client certificates, and connection/read timeouts. Do not introduce a parallel
HTTP stack or a separate insecure-mode flag.

Keep credentials in existing authentication facilities. Conda's
[authentication handlers](https://docs.conda.io/projects/conda/en/stable/dev-guide/plugins/auth_handlers.html)
can be selected for matching URLs through `channel_settings`. That existing
authentication mapping can apply to a publication host without turning it into
a package channel. Host-specific handlers, request-header plugins, and client
certificates keep their existing scope. Downloaded metadata and manifests
cannot change trusted source configuration or relax verification.

| Failure | Required response |
| --- | --- |
| No configured default | Show the one default-source setup command |
| Unknown alias or invalid selected base URL | Identify the selected setting and stop |
| Publication lookup returns 404 or 410 | Report that `owner/name` was not found at the selected source |
| Lookup returns 401 or 403 | Report access failure without interpreting it as an unknown name |
| No default in a valid document | Require an explicit revision or tag |
| Unknown revision or tag | Identify the unavailable selector without choosing another |
| Wrong name, schema, type, limits, or platforms | Report the invalid or incompatible publication before restoration |
| Transport failure or missing archive/receipt | Report the failed retrieval and stop |
| Descriptor or receipt verification failure | Reject the download and preserve extraction cleanup guarantees |
| Occupied target | Refuse replacement, including an empty directory or symlink |
| Installation or shell failure after restoration | Preserve the target and origin with #210's recovery commands |

Every error uses the same credential redaction and structured JSON error path
as archive quickstart. Do not include response bodies or raw credentials in
diagnostics. A failed lookup never retries another source or input type.

Optional service-specific discovery may return an explicit supported archive
URL and receipt, or a complete resolution result with the same validation and
verification requirements. Such discovery must be deliberately selected.
Generic named lookup does not discover, import, or require those adapters.
An OCI or conda-store artifact must actually use the supported workspace
archive format before it can enter this restoration path.

Implementation in #212 must exercise the classification table, configuration
precedence, defaults and tag movement, immutable digest checks, declared and
explicit receipts together, absent-receipt behavior, source redaction, preview,
occupied targets, and recovery. It must show that an install uses one resolved
entry and that later local package updates make no publication requests.

Publishing commands, publication upgrades, synchronization, merging local
changes, remote manifest-only import, service search, and organization
management remain outside this convention.
