# Releasing the Band Python kit

This is the release and patch process for the `band-python-kit` image and OCI
kit artifact published to GHCR. It covers what gets published, the tag policy,
who owns the cadence, the supply-chain quarantine gate and how to recover from
it, the supported `sbx` version, the manual-publish fallback, and how the
downstream catalogs stay current.

## Artifact map

Every band-sdk release publishes two artifacts to GHCR:

| Artifact | Reference | What it is | Pushed by |
|---|---|---|---|
| Sandbox image | `ghcr.io/band-ai/band-python-kit/image:<tag>` | The container image the sandbox VM pulls (`sandbox.image`). Multi-arch (amd64+arm64), provenance + SBOM attested. | `docker/build-push-action` in `kit-publish.yml` |
| Kit (OCI artifact) | `ghcr.io/band-ai/band-python-kit:<tag>` | The `spec.yaml` packaged as an OCI v2 kit artifact, consumed via `sbx create --kit <ref>`. Its `sandbox.image` pins the image above **by digest**. | ORAS assembly in `kit-publish.yml` (see the `sbx`-version note below) |

The two are separate GHCR packages. The kit artifact is behavioral only (network
policy + startup command); it ships no `files/`, so its OCI manifest carries a
single empty tar+gzip layer.

Both tags always resolve to the same underlying release: the image bundles the
SDK built from repo source at exactly that tag, and the kit tag pins the image
digest it was released with. One version number tells the whole story.

## Tag policy

Kit and image tags equal the release-please band-sdk version (composed from the
release job's `major`/`minor`/`patch` outputs — the artifact tags are bare
`X.Y.Z`, while the underlying **git tag** is component-prefixed as
`band-sdk-vX.Y.Z`).

| Tag | Semantics |
|---|---|
| `X.Y.Z` | Immutable. Published once per band-sdk release. |
| `X.Y.Z-rN` | Immutable CVE rebuild of `X.Y.Z` (same SDK, fresh base digest). `N` increments per rebuild. |
| `latest`, `<major>` (e.g. `1`) | Floating. Move on every release and every rebuild. |

Customers who pin `X.Y.Z` get a frozen artifact. Customers who ride
`latest`/`<major>` pick up CVE rebuilds when they re-create their sandbox. The
kit artifact mirrors image tags one-to-one.

Immutability and ordering are **enforced by the pipeline**, not just policy:
the publish refuses to overwrite an existing version tag (image and kit), and
registry authentication/transport failures fail closed instead of masquerading
as an absent tag. Floating tags only move after the immutable image, GitHub
provenance attestation, and immutable kit all succeed, followed by a re-check
that the version being published is still the current release. A rebuild that
waited in the publish queue behind a newer release publishes its immutable
`-rN` tag but leaves `latest`/`<major>` alone. Release, rebuild, and manual
entrypoints share one FIFO publish queue (`queue: max`), so a later pending run
cannot evict an earlier one.

## Cadence and ownership

The **Integrations team** owns the cadence. The workflows run unattended; a
human owns triage of their failures and of the rebuild scan reports.

| When | What runs | Result |
|---|---|---|
| On every band-sdk release | `release.yml` → `publish-kit` (calls `kit-publish.yml`) | Fresh image + kit publish at `X.Y.Z`, floating tags moved. |
| Weekly, Mondays 05:00 UTC | `kit-image-rebuild.yml` (schedule) | Rebuilds the latest released version **only if** a pinned base has a newer digest than `main`'s Dockerfile pins, or the currently published image (`X.Y.Z-rN` when rebuilt, else `X.Y.Z`) has a fixable HIGH/CRITICAL; publishes the next `X.Y.Z-rN`, moves floating tags, opens a digest-pin bump PR **against `main`**; skips entirely (even when forced) if the version was never published — the initial `X.Y.Z` belongs to the release workflow. A no-change week publishes nothing. **Merge the bump PR promptly**: `main`'s pins are the comparison ledger, so while the PR sits unmerged the next scheduled run re-detects the same digest delta and rebuilds redundantly. |
| Ad hoc (critical CVE) | `kit-image-rebuild.yml` via `workflow_dispatch` (`force: true`, `reason:`) | Same as weekly but unconditional; the reason is recorded in the run log. |
| Manual publish/rehearsal | `kit-publish-manual.yml` via `workflow_dispatch` | Calls the same pipeline under the same publish lock. Rehearsals leave `move-floating` false; emergency recovery can opt in after review. |

**How customers learn a new tag exists:** the floating tags move (documented
semantics above), rebuild tags appear on the GHCR package pages, the catalog
pins refresh per the upkeep policy below, and the rebuild workflow writes a run
summary (base digest deltas, scan results, what was published and why).

## Supply-chain quarantine gate

Before the image build, the publish pipeline runs
`scripts/check-lock-age.py`, which reads the PEP 700 `upload-time` uv records
in `uv.lock` for **every** locked artifact and fails if any was published
less than **7 days** ago (`quarantine-max-age-days` input, default 7). A
freshly-poisoned dependency therefore cannot enter a published image until it
has aged past a week. The check is deterministic — no resolution, no index
access, the committed lock is the single input — and it fails in seconds,
before any build minutes are spent. An artifact with no recorded upload-time
also fails the gate (an undatable source must not slip through silently).

**Why not `uv sync --exclude-newer` inside the build:** proven live during
the pre-merge smoke runs — passing a cutoff makes uv treat the committed
lockfile itself as outdated ("Resolving despite existing lockfile due to
addition of global exclude newer"), so `--locked` fails on **every** gated
build regardless of package age, on both uv 0.9.13 and 0.11.19
(astral-sh/uv#18775 documents the underlying option-tracking). The lock's own
recorded upload-times give the same policy exactly, without fighting uv.
Local developer builds are untouched — the gate lives in the workflow, not
the Dockerfile.

### What a gate failure means

The check covers the **whole** lockfile, so the gate can trip on a fresh
**dev-only** dependency bump (pytest, ruff, …) that never enters the
image — not only on a poisoned runtime dependency. Benign trips are therefore
expected. A trip leaves a **split release**: the PyPI publish (`publish-band`, an
independent job) has already succeeded, but no kit artifacts were produced.

### Recovery

- **Wait it out (default).** Once the young package has aged past 7 days, use
  GitHub's **"Re-run failed jobs"** on the release run. The cutoff is recomputed
  as now − 7 days at re-run time, so the same release heals without a new version
  number — no retag, no re-release. Always **re-run failed jobs only**: a full
  re-run of a partially-successful publish stops at the immutable-tag guard for
  whatever was already pushed (by design — that guard is what makes the tag
  policy real). If an artifact is truly half-published (e.g. pushed but its
  attestation failed), deleting that partial tag is a deliberate manual step
  before re-running.
- **Can't wait (justified override).** Dispatch `kit-publish-manual` with the
  release tag/version, `move-floating: true`, and a lowered
  `quarantine-max-age-days` input. The value used is recorded in the run's
  inputs, never silent; state the reason in the release PR or channel. Use this
  only for a real time-critical release; the default is to wait.

## Supported `sbx` version

Validated against **`sbx` v0.34.0** at ship time (OCI v2 kit artifacts,
`kit.allowedSources` default). The kit surface of `sbx` is experimental and has
moved between releases, so on **every** CLI upgrade, revalidate:

- `sbx kit validate` on the repo `spec.yaml`,
- a `kit push` → `kit pull` roundtrip,
- an OCI-ref consume (`sbx create --kit <oci-ref> band-python-kit <ws>`).

The CI kit push is currently assembled with **ORAS** rather than `sbx kit push`,
because `sbx`'s availability on ubuntu runners is unconfirmed. The ORAS manifest
is fully specified (`artifactType: application/vnd.docker.sandbox.kit.v2`, config
media type `application/vnd.docker.sandbox.kit.v2.spec+yaml`, one empty
`application/vnd.oci.image.layer.v1.tar+gzip` layer). The GHCR smoke artifact was
pulled and inspected with `sbx` v0.34.0, then consumed by
`sbx create --kit <ghcr-ref>`; the sandbox started, imported `band`, and was
removed successfully. This closes the first-release roundtrip prerequisite.
If `sbx` becomes available on the runner, swapping the ORAS step for
`sbx kit push` remains an option. Note that `sbx kit push` also emits a sibling
GC-anchor tag (`_kit_<tag>`) that ORAS does not; the proven OCI-ref consumption
path does not depend on it.

## Manual publish runbook (fallback)

If both CI paths are unavailable, a maintainer can publish from a
Docker-Sandbox-capable laptop:

1. Check out the release tag: `git checkout band-sdk-vX.Y.Z`.
2. Run the quarantine gate, then build and push the multi-arch image:
   ```bash
   python3 scripts/check-lock-age.py --lock uv.lock --max-age-days 7
   docker buildx build \
     -f docker/band_python_kit/Dockerfile \
     --platform linux/amd64,linux/arm64 \
     -t ghcr.io/band-ai/band-python-kit/image:X.Y.Z \
     --push .
   ```
3. Capture the pushed digest and stamp the distribution spec:
   ```bash
   mkdir -p staging
   python scripts/stamp-kit-spec.py \
     --spec docker/band_python_kit/spec.yaml \
     --image-ref ghcr.io/band-ai/band-python-kit/image:X.Y.Z \
     --digest sha256:<digest> \
     --output staging/spec.yaml
   ```
4. Validate and push the immutable kit: `sbx kit validate staging` then
   `sbx kit push staging ghcr.io/band-ai/band-python-kit:X.Y.Z`.
5. Only after both immutable artifacts and provenance verification succeed,
   move the kit's floating tags and retag the immutable image manifest. Keep
   this ordering: the kit pins the immutable image digest, so an interrupted
   final retag never points a kit at a missing image.

## One-time GHCR setup

New GHCR packages default to **private** regardless of repo visibility, and once
a package is made public it **cannot** be made private again. band-ai had zero
container packages before this kit, so this release creates the convention.

Sequencing (org enablement must precede the first release merge —
`workflow_dispatch` only works once the workflow is on the default branch, so
there is no pre-merge rehearsal):

1. Org admin allows Actions / member package creation.
2. GitHub App installed on `band-ai/add-band` (`contents: write` +
   `pull-requests: write`) for the automated bump PR.
3. Merge this workflow to the default branch.
4. Rehearsal: dispatch `kit-publish-manual` (Actions → kit-publish-manual →
   Run workflow) with a real git ref, a throwaway version like `0.0.0-rc1`, and
   `move-floating` left at its dispatch default of **false**
   so `latest`/`<major>` are untouched. Verify repo linkage, attestations, and
   both tag sets on the GHCR package pages. From a Docker-Sandbox machine,
   consume the rehearsal kit —
   `sbx create --kit ghcr.io/band-ai/band-python-kit:0.0.0-rc1 band-python-kit
   <workspace>` — revalidating the already-proven ORAS end-to-end path against
   the currently supported `sbx`. Then delete the rehearsal tags. Note:
   re-dispatching the same
   rehearsal version stops at the immutable-tag guard — bump the rc number or
   delete the previous rehearsal tags first.
5. First real release publishes for real.
6. Flip both packages (`band-python-kit`, `band-python-kit/image`) to **public**
   (one-way) once the layout is confirmed.

## Catalog upkeep

Two downstream catalogs point at these artifacts:

- **`band-ai/add-band`** — the bootstrap's pinned release tag is bumped by
  `release.yml`'s automated cross-repo PR (`bump-add-band`) on **every** release.
  Merging that PR stays a human step (add-band's own CI validates it).
- **`docker/sbx-kits-contrib`** — the contrib `spec.yaml` pins the image **by
  digest**. Refresh it with a contrib PR on **minor/major** releases and on CVE
  rebuilds that fix a HIGH/CRITICAL in the pinned image. Patch releases are
  accepted as stale, to keep the load on Docker's review queue low. The contrib
  copy tracks the repo copy — handle drift with a PR there, never by diverging
  the source spec.
