# Releasing the Band Python workload

The next SDK release containing the v3 migration publishes **one workload
image per registry**, built directly from `docker/band_python_kit/spec.yaml`.
Do not rebuild an older SDK release with newer packaging assets. Existing
immutable v2 tags remain available with their original contents.

## Artifact and tag policy

| Registry | Workload reference |
|---|---|
| Docker Hub | `docker.io/bandhq/band-python-kit:<tag>` |
| GHCR | `ghcr.io/band-ai/band-python-kit:<tag>` |

Each is a multi-platform OCI image for linux/amd64 and linux/arm64 with v3
workload descriptor metadata and provenance/SBOM attestations. New releases
do not publish separate `-image` or `/image` aliases and do not assemble v2
ORAS kit artifacts. Customer creation uses the workload as its positional
argument, followed by the workspace; Band's public provisioning `--kit`
option remains available.

| Tag | Meaning |
|---|---|
| `X.Y.Z` | Immutable SDK release artifact |
| `X.Y.Z-rN` | Immutable rebuild of that same v3 SDK release |
| `latest`, `<major>` | Floating accepted release/rebuild |

Git release tags remain `band-sdk-vX.Y.Z`; workload versions are bare `X.Y.Z`.
Do not overwrite an existing immutable tag, including an incompatible v2 tag.
Authentication and transport failures fail closed, rather than being classified
as absent references. Release, rebuild and manual entrypoints share the FIFO
publish queue and stale-release guards.

## First v3 rollout

The initial release calls the publisher with `move-floating: false`. Its
immutable workloads can publish while production floating tags remain on the
previous accepted kit. Scheduled rebuilds require both a v3 release source and
an already-promoted v3 floating workload; they must report ineligible while
the first-release hold is active. The publisher rejects a non-v3 source before
registry writes. No packaging-ref override may bypass this rule.

After explicit authorization, use isolated candidate references to rehearse
both registries. Record the frozen source ref, version, digests, builder and
validation results. A local OCI export is useful artifact evidence but does
not replace real push/pull acceptance.

Before promoting the first release, require:

- Pinned TCK validation and inspection of each platform's descriptor, UID 1000
  user, launcher command and attestations on both registry destinations.
- Real pull/consume from both registries and partial mirror recovery with the
  same complete root digest, platform children and attestation/referrer graph.
- The written published echo quickstart from its matching release archive;
  completed Band turns, sustained owned PTY and stop/restart with another reply.
- Actual proxy CA trust, positive controls for real-key absence, allowed Band/
  PyPI traffic, denied unrelated traffic and confirmed cleanup.
- The optional Anthropic recipe, local/published self-registration and meeting
  demo, including interrupted-run recovery. Credential, entitlement, provider,
  quota, registry and cleanup failures are blocked acceptance, not green skips.

When these gates pass, an **explicitly authorized** dispatch of
`kit-publish-manual.yml` with the same frozen release ref/version and
`move-floating: true` validates the existing immutable workloads before moving
floating tags. Restore routine release auto-promotion only after the first v3
rollout passes. Implementing the workflows does not authorize publishing a
production release or promoting tags.

## Publishing and recovery

`kit-publish.yml` builds the canonical descriptor through its digest-pinned
`docker/sandbox-kit:3` frontend. Ordinary publication uses the lean default
runtime; the optional `cli` target is for demo/validation builds. Policy and
instructions come from the same descriptor for both variants.

The publisher validates completed immutable destinations before reusing them.
For an absent mirror, pinned ORAS 1.3.0 copies the complete existing index by
digest into the missing tag, recursively including separate referrers. Do
not filter to one platform or use cross-registry `imagetools create` as graph
transfer. Verify root digest, children and attestations after recovery. Reuse
valid immutable contents; deleting or replacing immutable tags requires a
separate deliberate recovery decision.

Floating promotion happens only after both immutable destinations and their
attestations validate and the version is still the current release. A queued
older rebuild can publish its immutable `-rN` while leaving floating tags
untouched. Same-registry retagging must preserve the complete image index and
metadata.

## Quarantine and rebuild cadence

Before builds, `scripts/check-lock-age.py` checks every committed lock artifact's
PEP 700 upload time against the default seven-day quarantine. Unknown dates
fail closed; repository-defined first-party exemptions remain intentional.
This may block a kit publish because of a new dev dependency even after PyPI
publication succeeds. No runtime dependency resolution or `--exclude-newer`
workaround replaces the committed-lock gate.

Prefer waiting, then rerunning the failed release job. A manual quarantine
age override must be recorded and justified; leave `move-floating: false`
during first-release acceptance. The override does not authorize skipping
registry or supported-host acceptance.

The Integrations team owns workflow triage. Weekly `kit-image-rebuild.yml`
checks the eligible v3 workload for updated pinned base digests or fixable
HIGH/CRITICAL vulnerabilities. It preserves the released SDK source, produces
the next immutable `-rN` and proposes base pin updates on main. Merge pin
updates promptly to avoid repeatedly detecting the same delta. A release
without a published immutable workload, a v2 source or an unpromoted first v3
workload is ineligible even when forced. A no-change scan publishes nothing.

## Supported host and local validation

The validated command baseline is **sbx 0.47.0**. Recheck current released
primary docs/source and rerun artifact plus supported-host checks before a CLI
or frontend upgrade. v3 workload validation uses the pinned upstream TCK,
not the v1/v2-only `sbx kit pack/push/pull` path.

From a v3 source checkout, build a single-platform diagnostic image or a
multi-platform OCI export with attestations:

```bash
docker buildx build -f docker/band_python_kit/spec.yaml \
  --target runtime --load -t band-python-kit:local .
docker buildx build -f docker/band_python_kit/spec.yaml \
  --platform linux/amd64,linux/arm64 --provenance=mode=max --sbom=true \
  --output type=oci,dest=band-python-kit.oci.tar .
mkdir band-python-kit-layout
tar -xf band-python-kit.oci.tar -C band-python-kit-layout
# Use the checksum-verified kit-tck v3.0.0-m.8 from the test toolkit:
kit-tck validate --layout band-python-kit-layout '<tag-or-digest>'
```

Select the actual root tag/digest recorded in the export's `index.json`, rather
than inventing one. Loaded Docker images and multi-platform OCI exports are
separate verification boundaries; a local load need not retain attestations.
Use the customer quickstart with a unique sandbox and a local workload for the
supported-host launch boundary. No background/headless lifetime guarantee is
part of this release.

## Registry setup and candidate rehearsal

Docker Hub needs public `bandhq/band-python-kit`, `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN`. For an organization access token the username is `bandhq`.
GHCR needs Actions package creation permissions, repository linkage and public
visibility for `band-python-kit`; making a GHCR package public is one-way.
Caller workflows retain the release environment deployment gate.

Use `kit-publish-manual.yml` against a frozen v3 ref and a unique throwaway
candidate version with `move-floating: false`. Verify both destinations with
`kit-tck validate <reference>`, inspect and consume each on a supported host,
and exercise partial mirror completion. Clean up only the owned candidate
references after recording the results. Preserve production immutable tags.
The shared publisher is the manual recovery path too; avoid a second ad-hoc
publisher that bypasses immutable-state, source, quarantine or acceptance gates.

## Catalog status

Neither `band-ai/add-band` nor `docker/sbx-kits-contrib` currently contains a
Band kit entry. The existing add-band bump job remains dormant when its entry
is absent. Creating a catalog entry or claiming a bootstrap wrapper is outside
this migration.
