# INT-979 — Kit Release Engineering: Publish band-python-kit + Catalog in add-band & sbx-kits-contrib

**Ticket:** [INT-979](https://linear.app/thenvoi/issue/INT-979).
**Blocked by:** INT-978 only (PR [#445](https://github.com/band-ai/band-sdk-python/pull/445) —
must merge first, since this ticket publishes the kit it creates). INT-977
(PR [#443](https://github.com/band-ai/band-sdk-python/pull/443)) is merged. Version examples
below use band-sdk **1.2.0** (the version on `main` at time of writing) — confirm the current
version before wiring any tag logic; don't hardcode 1.2.0.

> **NO manual rebase.** The INT-979 branch/PR ([#446](https://github.com/band-ai/band-sdk-python/pull/446))
> stays **stacked on the INT-978 branch** by design, for as long as #445 is open — this is
> deliberate, confirmed with the assignee, not a stale TODO. Both #445 and #446 target
> **`dev`**, not `main` (verified: `gh pr view 445 --json baseRefName` → `dev`) — `main` is
> where `release.yml` fires from (push-triggered release-please), reached only via a
> separate, periodic dev→main sync, not this PR chain. When #445 merges to `dev`, GitHub
> retargets #446's base to `dev` automatically; nothing here manually rebases, force-pushes,
> or re-bases the stack. If a stale 1.1.0-era reference is spotted after #445 lands, fix it
> as a normal small edit — not by rebasing the branch.

## Objective

Make the Band Python kit adoptable in one command from a clean machine:
reproducible, supply-chain-quarantined releases of the kit image **and** the
kit OCI artifact to GHCR, driven by the repo's existing release automation;
catalog entries in `band-ai/add-band` and `docker/sbx-kits-contrib` that point
at the published artifacts (never vendor the build); and a written
release/patch process (who rebuilds, how often, how customers learn).

## Linear requirements

1. Semver tags; pinned base-image digests; reproducible build from a lockfile
   — in the `band-sdk-python` repo.
2. Release-age dependency quarantine in the image build (uv
   `--exclude-newer`-style).
3. Stated CVE-rebuild cadence for the published image (who rebuilds, how
   often, how customers learn a new tag exists).
4. Publish to GHCR (ticket text: `ghcr.io/thenvoi/band-python-kit`; resolved
   to `ghcr.io/band-ai/...` — see "Confirmed decisions").
5. Catalog in `add-band` (on-ramp entry pointing at the published image) and
   open the `docker/sbx-kits-contrib` PR for community discovery.
6. Quickstart README: prerequisites (`brew install docker/tap/sbx`,
   `sbx login`), the one command, workspace-file config, staging/prod switch,
   honest v0.1 custody statement (keys as env vars — laptop-equivalent;
   injection custody comes with INT-980/981).

**Ticket acceptance:** `sbx run <agent> --kit ghcr.io/…/band-python-kit:<tag>`
works from a clean machine; both catalog entries point at the published image;
the release/patch process is written down in `band-sdk-python`.

**Acceptance-wording adaptation (deliberate, evidence-backed):** INT-978
established on the pinned `sbx` v0.34.0 that this kit launches headlessly via
`commands.startup` on `sbx create` and defines **no** `sandbox.entrypoint`
(interactive attach is not a supported flow — `entrypoint.run` executes only
on attach, as root, on a PTY). The clean-machine acceptance is therefore
realized as `sbx create --name <sandbox> --kit
ghcr.io/band-ai/band-python-kit:<tag> band-python-kit <workspace>` — the same
adaptation PR #445 already records. `--kit` accepts OCI references on
v0.34.0 (verified: `sbx create --help` / `sbx run --help` both document
"Kit reference (directory, ZIP, or OCI)").

## Confirmed decisions

The cross-cutting decisions below are settled — treat them as fixed unless
new evidence surfaces, not open questions.

### GHCR namespace: `ghcr.io/band-ai` (not `thenvoi`)

The ticket's `ghcr.io/thenvoi/...` predates the thenvoi→band rename. Both
GitHub orgs exist: `thenvoi` holds the platform's
**private** container packages (`thenvoi-platform`); `band-ai` — the org that
owns this public SDK repo — has **zero** container packages. GHCR namespaces
are bound to the GitHub org, and a workflow's `GITHUB_TOKEN` can only push
packages in its own org; publishing to `ghcr.io/thenvoi/...` from band-ai CI
would require provisioning and rotating a cross-org PAT/app. Decision:
publish under `ghcr.io/band-ai/`, treating the ticket string as stale.

### Two published artifacts, one release

`sbx` distributes the **kit** (spec.yaml packaged as an OCI artifact,
consumed via `--kit <ref>`) separately from the **sandbox image** (a normal
container image the sandbox VM pulls from `sandbox.image:`). Both publish to
GHCR on every release:

| Artifact | Reference | Pushed by |
|---|---|---|
| Kit (OCI artifact) | `ghcr.io/band-ai/band-python-kit:<tag>` | `sbx kit push` (Docker credential store auth — `docker login ghcr.io` first) |
| Sandbox image | `ghcr.io/band-ai/band-python-kit/image:<tag>` | `docker/build-push-action` |

The nested `band-python-kit/image` name keeps the image visibly subordinate
to the kit (GHCR supports multi-segment names). The published kit's
`sandbox.image` points at the image **by digest** (see "Publish pipeline").
Note: on v0.34.0 `sbx kit push` also pushes a sibling GC-anchor tag
(`_kit_<tag>`) into the kit repository so the referenced image layers are not
garbage-collected — expected, not a bug (documented in sbx-kits-contrib's
`skills/kit-author/topics/distribution.md`).

### Versioning: tracks the band-sdk release

Kit and image tags equal the release-please band-sdk version (1.2.0 on
`main` at time of writing). The image bundles the SDK built
from the repo source at exactly that tag, so one version tells the whole
story; publishing rides the existing `release.yml` (`release_created` gate),
no second release train.

Tag policy:

- `X.Y.Z` — immutable, published once per band-sdk release.
- `X.Y.Z-rN` — immutable CVE-rebuild of `X.Y.Z` (same SDK, fresh base
  digest); N increments per rebuild.
- `latest` and major (`1`) — floating; move on every release and rebuild.

Customers who pin `X.Y.Z` get a frozen artifact (standard pin semantics);
customers who ride `latest`/`1` pick up CVE rebuilds on sandbox re-create.
The kit artifact mirrors image tags one-to-one; each kit tag pins the image
digest it was released with.

**Git-tag format:** release-please emits
component-prefixed tags — `band-sdk-vX.Y.Z`, **not** `vX.Y.Z`
(`gh release list` → `band-sdk-v1.2.0`). Every git-ref use in this plan must
use the full form: the publish jobs' checkout
(`ref: needs.release.outputs.tag_name`), the rebuild workflow's tag
checkout, and the add-band bootstrap's pinned tarball URL. The *artifact*
tags stay bare `X.Y.Z` — compose them from the release job's existing
`major`/`minor`/`patch` outputs rather than string-stripping `tag_name`.

### Release-age dependency quarantine: uv `--exclude-newer` at build time

Empirically grounded (probed on uv 0.11.19 with a scratch project):

- `uv sync --locked --exclude-newer <date>` **re-resolves to validate the
  lock against the cutoff and fails** if any locked package was published
  after it ("requests==2.34.2 was published after the exclude newer time").
  Nothing is installed on failure.
- `uv sync --frozen --exclude-newer <date>` **ignores the cutoff** (no
  resolution happens) — `--frozen` must not be used for the gated build.
- The cutoff stays a **CI-only flag**, never `[tool.uv] exclude-newer` in
  `pyproject.toml`: uv records the option into `uv.lock`, which breaks
  `uv lock --check` for every dev who doesn't pass the same flag (open
  upstream friction: astral-sh/uv#18775).

Design: the Dockerfile's builder stage gains `ARG UV_EXCLUDE_NEWER=""` and
appends `--exclude-newer "$UV_EXCLUDE_NEWER"` to its `uv sync --locked`
invocations when non-empty. The release workflow computes the cutoff as
**now − 7 days** (RFC 3339) and passes it as a build arg. Effect: a release
build fails loudly if the committed `uv.lock` contains any package version
younger than 7 days — a freshly-poisoned release cannot enter the published
image until it has aged (or the lock is explicitly pinned back). Local
developer builds pass no cutoff and behave exactly as today.

7 days sits inside the community-recommended 3-day–2-week cooldown band;
PyPI's own incident report for the March 2026 LiteLLM/Telnyx supply-chain
attacks recommends exactly this pattern (dependency cooldown + hash-checked
lockfiles). An emergency override (documented in RELEASING.md) is a
`workflow_dispatch` input that shortens the window with a required
justification string — visible in the run log, never silent.

Implementation notes: the gate re-resolves against the index, so the
builder needs PyPI metadata access (it already has it — this is where
`uv sync` runs today), relying on PEP 700 `upload-time` (PyPI serves it; the
repo's own `uv.lock` records `upload-time` for every artifact, and the lock
has zero git/URL sources that would lack it). And the
image pins **uv 0.9.13** while the probes ran on 0.11.19 — verifying the
identical `--locked` + `--exclude-newer` behavior on the pinned uv (or
bumping the pin) is an explicit implementation step.

**Gate breadth + recovery:** `uv sync --locked` validates
the *entire universal lockfile*, so the cutoff almost certainly also trips
on a fresh **dev-only** dependency bump (pytest, ruff, …) that never enters
the image — confirm with a quick probe (open item #12) and word RELEASING.md
accordingly. Benign trips are therefore likelier than the poisoned-dep
framing suggests, and a trip leaves a **split release**: PyPI published (an
independent job), no kit artifacts. Recovery is cheap and must be written
into RELEASING.md: GitHub's "Re-run failed jobs" recomputes the now−7d
cutoff at re-run time, so waiting out the age and re-running heals the
release without a new version; the justified-override dispatch covers the
can't-wait case.

### CVE-rebuild cadence: weekly + on-release

- **On every band-sdk release**: fresh image + kit publish (the release job).
- **Weekly** (scheduled workflow, Monday): rebuild the latest released
  version from its tag when the base needs it; publish `X.Y.Z-rN` and move
  the floating tags.
- **Ad hoc**: the same workflow via `workflow_dispatch` for a critical CVE.

Who: the Integrations team owns the cadence (the workflow runs unattended;
a human owns triage of its failures and of scan reports). How customers
learn: floating tags move (documented semantics), rebuild tags appear on the
GHCR package pages, catalog pins refresh per the upkeep policy (see the
RELEASING.md section), and RELEASING.md states the policy; the rebuild
workflow writes a summary of what changed (base digest, scan delta) on
every run.

### Kit spec `schemaVersion`: bump "1" → "2"

`sbx kit push` selects the OCI artifact format from the spec's
`schemaVersion`: `"1"` → legacy ZIP; `"2"` → tar+gzip layer with the spec in
the manifest config blob and standard OCI annotations, plus v0.34.0's
streaming/caching support. The published kit should be the v2 form.
Verified on the pinned v0.34.0: a `schemaVersion: "2"` spec passes
`sbx kit validate` unchanged. No documented spec-field differences exist
between 1 and 2 (the version selects packaging); the repo spec bumps to
`"2"` and the drift test updates with it. End-to-end verification (local
`--kit <dir>` create, push→pull roundtrip, OCI consume) is an implementation
step — the docs site does not document schemaVersion 2 at all, so the CLI is
the source of truth.

### Catalog scope: full entries; add-band merged, contrib PR-opened

Both catalogs get complete, working entries (not stubs). The two differ on
definition-of-done because band-ai owns one and Docker owns the other:

- **`band-ai/add-band` — merged.** band-ai owns the repo, so an open PR is not
  a finished on-ramp; acceptance ("the catalog points at the published
  artifacts") is only truly met once it lands on `main`. Merging is in scope.
- **`docker/sbx-kits-contrib` — PR-opened = done.** The merge timeline belongs
  to Docker's reviewers; **opening** a complete, locally-validated PR satisfies
  the ticket.

## Publish pipeline design

### Dockerfile changes (small, contained)

1. `ARG UV_EXCLUDE_NEWER=""` wired into both `uv sync --locked` branches
   (quarantine gate above).
2. Static OCI labels in the runtime stage:
   `org.opencontainers.image.source=https://github.com/band-ai/band-sdk-python`,
   `…image.licenses=MIT`, `…image.title`, `…image.description`. GHCR uses
   the `source` label to link CLI-pushed packages to the repo; the
   Actions-pushed image links automatically via `GITHUB_TOKEN`, but the
   labels make manual pushes and the kit artifact behave too. Dynamic labels
   (`version`, `revision`, `created`) come from `docker/metadata-action` at
   build time — not hardcoded.

### `release.yml`: two new jobs after `release`

Both gated on `needs.release.outputs.release_created == 'true'`, following
the existing `publish-band` shape, and both join `environment: release` like
that sibling (the environment has **no** protection rules currently, so this
costs nothing and stays uniform if protections are added later). Both check out the release ref explicitly
(`ref: needs.release.outputs.tag_name` — format `band-sdk-vX.Y.Z`) instead
of assuming `github.sha` is the tagged commit. Kit-publish failures must not
block `publish-band` (PyPI) — the jobs stay independent and `summary`
reports all of them. New third-party actions are SHA-pinned
(GitHub's guidance for publish workflows; the repo's existing tag-pinned
actions are left as they are — repo-wide re-pinning is out of scope). The
build→attest→stamp→push sequence is factored as a **reusable workflow**
(`workflow_call`, `.github/workflows/kit-publish.yml`) shared with the
weekly rebuild — two inline copies of the same pipeline would drift.

**Job `publish-kit-image`** (permissions: `contents: read`,
`packages: write`, `attestations: write`, `id-token: write`):

1. `docker/setup-qemu-action` + `docker/setup-buildx-action` (current
   majors: v4/v4).
2. `docker/login-action` (v4) to `ghcr.io` with `GITHUB_TOKEN`.
3. `docker/metadata-action` (v6) on `ghcr.io/band-ai/band-python-kit/image`
   with tags: the release semver, the major, and `latest` — all `type=raw`,
   composed from the release job's `major`/`minor`/`patch` outputs (the
   workflow trigger is a push to `main`, not a tag event, so tags cannot be
   derived from the ref).
4. Compute the quarantine cutoff (`date -u -d '7 days ago'
   +%Y-%m-%dT%H:%M:%SZ`).
5. `docker/build-push-action` (v7 — current major, not v6):
   `platforms: linux/amd64,linux/arm64`, `push: true`,
   `provenance: mode=max`, `sbom: true`,
   `build-args: UV_EXCLUDE_NEWER=<cutoff>`, file
   `docker/band_python_kit/Dockerfile`, context repo root.
6. `actions/attest-build-provenance` (v4) on the pushed digest with
   `push-to-registry: true`.
7. Expose the pushed image digest as a job output for the kit job.

One job with QEMU for the arm64 half is the v0.1 choice: the build is
dominated by wheel installs (no compilation — INT-977 verified the lock
resolves to wheels on `linux/arm64`), and a single `build-push-action` call
produces the multi-arch manifest atomically. If build time becomes a
problem, the documented upgrade path is the Docker-docs split pattern:
native per-arch jobs (`ubuntu-latest` + `ubuntu-24.04-arm` — free and GA for
public repos since Aug 2025) merged with `buildx imagetools`. Attestations
require pushing directly to the registry (they are dropped with `load:
true`) — the job never loads the image locally.

**Job `publish-kit-artifact`** (needs `publish-kit-image`; permissions:
`contents: read`, `packages: write`):

1. Install the `sbx` CLI on the runner (see verification item #1 — registry
   operations should not need the sandbox VM/nested virtualization, but the
   Linux-runner path is unproven).
2. `docker login ghcr.io` (again `GITHUB_TOKEN` — `sbx kit push` uses only
   the Docker credential store; `sbx secret set --registry` is a pull-side
   mechanism).
3. Stamp the distribution spec: copy `docker/band_python_kit/spec.yaml` to a
   staging dir with `sandbox.image` rewritten to
   `ghcr.io/band-ai/band-python-kit/image:<version>@<digest>` (digest from
   the image job's output). The repo copy keeps `band-python-kit:local` —
   the local-development flow (`docker build` + `sbx template load`) stays
   intact, and the kit-spec drift tests don't pin `sandbox.image`, so
   nothing fights. Stamping is a small tested Python helper (see
   deliverables), not inline sed: contrib's distribution doc claims
   `sbx kit push` digest-pins the image itself, but that behavior is
   version-sensitive and undocumented for v0.34.0 — stamping explicitly is
   deterministic either way.
4. `sbx kit validate` the staged dir (fail-loud gate).
5. `sbx kit push <staged-dir> ghcr.io/band-ai/band-python-kit:<version>`,
   then push the floating tags (repeat push, or re-tag with `crane`/`oras`
   if repeat pushes prove non-idempotent — implementation detail).

**Fallback if `sbx` cannot run on ubuntu runners** (format confirmed live
against sbx-kits-contrib's `spec/OCI-v2.md`): an ORAS-assembled push is a
fully-specified plan B. Exact shape:

| Component | Exact value |
|---|---|
| `artifactType` | `application/vnd.docker.sandbox.kit.v2` |
| config blob mediaType | `application/vnd.docker.sandbox.kit.v2.spec+yaml` (verbatim `spec.yaml` bytes) |
| layer mediaType | `application/vnd.oci.image.layer.v1.tar+gzip` |

Our kit ships **no `files/`** — the launcher lives in the image and the
starter is scaffolded by bootstrap, so it's a "pure behavioral mixin"
(network + `commands.startup` only). The spec explicitly permits this, **but
the manifest still requires exactly one layer — an *empty* tar+gzip, not zero
layers.** ORAS must emit that empty layer. This makes the ORAS path
concrete enough to be the primary CI mechanism if the `sbx`-on-runner probe
(open item #1) fails, with a maintainer-laptop runbook in RELEASING.md as the
honest plan C for v0.1.

**Job `bump-add-band`:** after a successful kit
publish, open an automated PR against `band-ai/add-band` bumping the
bootstrap's pinned release ref to the new tag. Authenticate with the repo's
existing GitHub App (`APP_CLIENT_ID`/`APP_PRIVATE_KEY`, already minted by
the `release` job) — `GITHUB_TOKEN` cannot open cross-repo PRs, and
Actions-token-authored PRs don't trigger CI anyway. One-time admin
prerequisite (open item #17): the App must be **installed on
`band-ai/add-band`** with `contents: write` + `pull-requests: write` —
bundle the ask with the GHCR org-admin request. Merging the bump PR stays a
human step (add-band's own CI validates it).

### One-time GHCR setup (org admin — a hard pre-merge gate)

**Sequencing:** `workflow_dispatch` only works once a workflow exists on the
default branch, so there is no rehearsal-before-merge option — the first
release-please merge after this PR lands fires the publish jobs for real.
Org enablement must therefore precede the merge, not run in parallel with
it: (1) org admin allows Actions/member package creation (requested on the
ticket from the org admin — confirm done before merging), (2) GitHub App
installed on add-band (bump job above), (3) merge, (4) rehearsal dispatch,
(5) first real release, (6) public visibility flip.

New GHCR packages default to **private** regardless of repo visibility, and
"once you make a package public, you cannot make it private again." After
the first successful publish: flip both packages
(`band-python-kit`, `band-python-kit/image`) to public, confirm the
Actions-linkage shows on the package pages, and confirm org policy allows
member-created public packages. band-ai has zero container packages
currently (verified via API), so there is no convention to inherit — this
run creates it; confirm push permissions with an org admin before assuming
defaults work.

### Weekly rebuild workflow (`.github/workflows/kit-image-rebuild.yml`)

Triggers: `schedule` (cron `0 5 * * 1`) + `workflow_dispatch`
(inputs: `force: boolean`, `reason: string`).

1. Resolve the latest released tag from `.release-please-manifest.json` on
   `main`; check out that tag.
2. Resolve the current manifest digest for the Dockerfile's pinned Python
   tag (`docker buildx imagetools inspect python:3.12.13-slim-trixie`) and
   compare with the committed pin; same for the uv image.
3. Scan the currently published `X.Y.Z(-rN)` image with Trivy
   (`aquasecurity/trivy-action`), report HIGH/CRITICAL.
4. Rebuild + publish only when (a) a pinned base has a newer digest,
   (b) the scan found fixable HIGH/CRITICAL, or (c) `force`. Rebuild passes
   the fresh digests as `--build-arg` (the committed pins are defaults, not
   ceilings), keeps the same `uv.lock` and quarantine gate, publishes
   `X.Y.Z-rN` (N = next free suffix, resolved by listing the existing GHCR
   tags for that version), moves `latest`/major, and republishes the kit
   artifact with the new image digest — all via the shared `workflow_call`
   pipeline (see the release.yml section), never an inline copy.
5. Open an automated PR bumping the Dockerfile digest ARGs so the committed
   pinning ledger stays truthful (the rebuild never leaves the repo lying
   about what `latest` is built from). Author it with the existing GitHub
   App token, not `GITHUB_TOKEN` — Actions-token-authored PRs don't trigger
   CI, so the bump PR would sit with no checks.
6. Write a run summary: scan results, digest deltas, what was (or wasn't)
   published and why. A no-change week publishes nothing.

### RELEASING.md (`docker/band_python_kit/RELEASING.md`)

The ticket's "release/patch process is written down" deliverable:

- artifact map (two GHCR refs + what each is), tag policy and immutability
  semantics;
- who owns the cadence (Integrations team), what runs when (release job,
  weekly rebuild, ad-hoc dispatch), and how customers learn about new tags;
- the quarantine policy: the 7-day cutoff, what a gate failure means —
  including the benign case (a fresh dev-only dep bump trips the whole-lock
  check) and the split-release state it leaves (PyPI published, no kit) —
  the recovery procedure ("Re-run failed jobs" after the package ages; the
  cutoff is recomputed at run time), and the justified-override procedure;
- the supported `sbx` version statement (v0.34.0 at ship; revalidate kit
  validate/push/consume on every CLI upgrade — the kit surface is
  experimental and has moved between releases);
- manual publish runbook (the plan-C fallback) and the one-time GHCR
  setup record;
- catalog upkeep policy: the add-band pin is bumped
  by the release workflow's automated cross-repo PR on every release
  (merging it stays a human step); the contrib digest pin is refreshed by a
  contrib PR on minor/major releases and on CVE rebuilds fixing
  HIGH/CRITICAL in the pinned image — patch releases are accepted as stale
  (keeps the burden on Docker's review queue low). The contrib copy of
  spec.yaml tracks the repo copy — drift is handled by PRs there, never by
  diverging the source.

## Catalog entry designs

### `band-ai/add-band`

Entries are one top-level folder per integration; "participating" entries
require `manifest.yaml` (five required flat fields), a hand-authored
`bootstrap.sh`, and a README with five fixed sections; CI
(`scripts/check.py` + pytest drift tests) enforces the shape, `bash -n`
syntax, and that every bootstrap references `BAND_API_KEY` (prompting from
`/dev/tty` when unset). Conventions: never vendor the integration; pin refs
(tag/digest, no moving branches); fail loud (`set -e`); never bake a secret.
A `STUB_ONLY` escape hatch exists for a placeholder entry, but this kit
warrants a full participating entry — it's the first Docker/sbx entry in
the catalog and should demonstrate the real one-command flow, not a stub.

New folder `band-python-kit/`:

- `manifest.yaml`:

  ```yaml
  name: Band Python Kit
  repo: https://github.com/band-ai/band-sdk-python
  connects_via: Docker Sandbox (sbx) kit
  status: available
  summary: Run a locked Python agent workspace on Band inside a Docker
    microVM sandbox — one command from the published GHCR kit.
  ```

- `bootstrap.sh` (the true one-command on-ramp): check `docker` + `sbx`
  binaries early; acquire `BAND_API_KEY` per the house rule; register an
  agent (the `scripts/register-agent.sh` pattern — outputs
  `BAND_AGENT_ID`/`BAND_AGENT_API_KEY`); scaffold a workspace from the
  echo-agent starter pinned to the release tag — fetch the **release
  tarball**
  (`codeload.github.com/band-ai/band-sdk-python/tar.gz/refs/tags/band-sdk-v<X.Y.Z>`)
  and extract `docker/band_python_kit/echo-agent/`, not per-file raw
  fetches (the starter is 6 files including `uv.lock` and the file list
  will drift across releases; note the tag prefix is `band-sdk-v`, not
  `v`); write `band.yaml` + gitignored `.band/secrets.env` (chmod 600);
  add `ghcr.io/band-ai/` to `kit.allowedSources` by **read-modify-write**
  (`sbx settings get`, append if missing — `sbx settings set` replaces the
  whole list and would clobber a user's customized allowlist); `sbx create
  --kit ghcr.io/band-ai/band-python-kit:<pinned tag> band-python-kit
  <workspace>`. Credentials go via the env file, never argv (the
  openclaw/nanoclaw precedent). The pinned tag is kept current by the
  release workflow's automated bump PR (see "Publish pipeline design").
- `README.md` five sections; **Verify** = "@mention the agent in a Band
  room — a reply means it's live inside the sandbox" (the catalog's standing
  verify signal).
- One row in the root README Integrations table.
- Pre-PR: `python3 scripts/check.py`, `pytest tests/ -q`,
  `scripts/local-bootstrap.sh band-python-kit --print`.

### `docker/sbx-kits-contrib`

Contrib kits vendor the **spec** in-tree (all 23 existing kits do);
`sandbox.image` may reference an externally published image — the sole
precedent is a community Docker Hub image
(`nanoclaw`: `docker.io/nanoco/nanoclaw:sbx-claude-alpha`); no merged kit
references a ghcr.io image yet, and nothing written forbids it (the PR
template explicitly invites flagging "an unusual image choice" for review).

New directory `band-python-kit/` in the contrib repo:

- `spec.yaml` — the distribution form: `schemaVersion: "2"`,
  `kind: sandbox`, `sandbox.image` = the published GHCR image **pinned by
  digest** (their distribution skill's rule: remote refs MUST pin), the
  same `commands.startup` launch and measured-minimal `caps.network.allow`
  as the repo spec, plus the SPEC-v2 optional metadata fields (`version`,
  `sourceURL: https://github.com/band-ai/band-sdk-python`,
  `licenses: ["MIT"]`) — adding these to the spec must be revalidated on
  v0.34.0 first.
- `README.md` in their house format (title/description, Usage, "How it
  works", Cleanup) — usage documents the workspace contract (`band.yaml`,
  locked uv project, opt-in credentials file) and links the SDK repo docs.
- Possibly `testdata/tck.yaml`; whether the shared TCK and the deny-all e2e
  can exercise a headless kit that needs a Band workspace is verification
  item #6 — the local `scripts/test-kit.sh` + `scripts/test-kit-e2e.sh`
  runs are **mandatory** before opening the PR regardless (fork PRs get no
  CI secrets, so their e2e legs skip).
- **Prerequisite (confirm before pushing the branch):** local git must be
  configured for **both** DCO sign-off (`git commit -s`) **and** cryptographic
  commit signatures (`git config commit.gpgsign true` + a registered signing
  key) — contrib requires both and rejects PRs missing either. This is a
  one-time developer-machine setup, not an in-repo change; confirm it is in
  place before the contrib branch exists.
- PR mechanics: title `Add band-python-kit kit`; DCO sign-off **and**
  cryptographic commit signatures (both required); body per template —
  Summary, "Spec choices worth flagging for review" (the ghcr.io-hosted
  image, the headless `commands.startup`-only launch with no
  `sandbox.entrypoint`, the root startup chain that drops to uid 1000),
  Test plan (local TCK/e2e evidence), Origin (Band's SDK team). Offer a
  CODEOWNERS line for the kit directory (existing per-kit precedent).
- Contingency: if review requires a Docker-Hub-hosted image (their default
  `kit.allowedSources` favors `docker.io/`), mirroring the image to Docker
  Hub is a contained follow-up — the kit spec changes one line. Do not
  pre-build this; surface it only if asked.

## Quickstart README rewrite

`docker/band_python_kit/README.md`'s Quickstart flips from "build it
yourself" to the published path (the build path moves to a "Developing the
kit" section):

```bash
# One-time host setup
brew install docker/tap/sbx && sbx login
sbx settings set kit.allowedSources '["docker.io/","ghcr.io/band-ai/"]'

# Workspace: start from the echo-agent starter (see echo-agent/README.md)
#   - set agent.id in band.yaml
#   - create .band/secrets.env from secrets.env.example (chmod 600)

# Create the sandbox from the published kit — the agent starts immediately.
sbx create --name my-band-agent \
  --kit ghcr.io/band-ai/band-python-kit:<X.Y.Z> \
  band-python-kit ~/my-band-agent
```

Content requirements from the ticket, all kept: prerequisites; the one
command; workspace-file config; the staging/prod switch (endpoints via
`band.yaml`/env overrides; non-production Band hosts granted per sandbox
with `sbx policy allow network --sandbox <name> <host>` — never baked into
the kit); and the honest v0.1 custody statement (the opt-in
`.band/secrets.env` means plaintext keys exist in the workspace and the VM —
laptop-equivalent custody; proxy injection replaces it in a later release,
INT-980/981). The `kit.allowedSources` line exists because the default
allowlist is `["docker.io/"]` (verified locally and in Docker's docs) — the
setting **replaces** the whole list, so the snippet always includes
`docker.io/`, and the README says so explicitly (a user with an already
customized allowlist must merge, not paste — `sbx settings get` first).

## Implementation deliverables

```text
band-sdk-python:
├── docker/band_python_kit/
│   ├── Dockerfile             # + UV_EXCLUDE_NEWER arg, + static OCI labels
│   ├── spec.yaml              # schemaVersion "2"; image stays :local in-repo
│   ├── README.md              # quickstart rewritten to the published path
│   └── RELEASING.md           # NEW — release/patch/cadence process
├── scripts/stamp-kit-spec.py  # NEW — writes the distribution spec (image → digest ref)
├── .github/workflows/
│   ├── release.yml            # + publish-kit-image, publish-kit-artifact, bump-add-band jobs
│   ├── kit-publish.yml        # NEW — reusable (workflow_call) build→attest→stamp→push pipeline
│   └── kit-image-rebuild.yml  # NEW — weekly/dispatch CVE rebuild (calls kit-publish.yml)
└── tests/docker/
    └── test_kit_spec.py       # drift: schemaVersion "2"; stamp-helper unit tests

band-ai/add-band (separate PR):
└── band-python-kit/           # manifest.yaml, bootstrap.sh, README.md + root table row

docker/sbx-kits-contrib (separate PR):
└── band-python-kit/           # distribution spec.yaml, README.md (+ testdata/tck.yaml if TCK-viable)
```

The stamp helper is a small argparse script (hyphenated filename per repo
naming rules; no env reading, so no settings class needed) with unit tests:
given a spec path, an image ref, and a digest, it emits the distribution
spec and refuses malformed digests — the one piece of release logic worth
testing off-runner. A hyphenated filename can't be imported with a plain
`import`, so the tests load it via `importlib.util.spec_from_file_location`
(small conftest helper) — decided up front so it doesn't surprise
mid-implementation. `scripts/` does not exist yet; this creates it.

## Status (2026-07-18 — implementation on this branch)

**Track A is fully implemented, reviewed, and hardened on this branch.** The
only Track A remainder is the local quarantine-gate proof (#12), which needs
a Docker daemon (the implementation environment had none). Track B stays
blocked on the org-admin GHCR prerequisites (#13 — requested from the org
admin via Slack) and the merge sequencing.

Done beyond the written plan, in the same commits:

- A full code review (8 finder angles + verification) surfaced and fixed 10
  findings; the biggest were in the rebuild workflow's decide job (a subshell
  bug that made the base-digest trigger dead, scan-failure/CVE conflation,
  registry auth, shell injection on a dispatch input, fail-loud `-rN`
  resolution) plus review passes over kit-publish.yml/release.yml (rehearsal
  `workflow_dispatch` path — without it the pre-first-release rehearsal was
  impossible; shared `kit-ghcr-publish` concurrency so release and rebuild
  can't race the floating tags; least-privilege on `bump-add-band`).
- The rebuild's comparison ledger is **dev's** Dockerfile pins (not the
  released tag's, which would re-trigger forever, and not main's, which only
  heals on promote — dev is where the bump PR lands per the repo's
  PRs-over-dev convention), and the scan targets the **currently published**
  `X.Y.Z(-rN)`, per this plan's own wording.
- Pre-publish CVE pass: pip-audit over the image's exact locked set found and
  fixed cryptography (GHSA-537c-gmf6-5ccf), idna (CVE-2026-45409), and
  pydantic-settings (GHSA-4xgf-cpjx-pc3j); the `PYTHON_BASE_IMAGE` digest pin
  was refreshed (upstream had moved). Rationale recorded in the commit: `-rN`
  rebuilds keep the lock, so Python-dep CVEs only ship out via lock bump +
  release — the first publish must start clean.
- Supply-chain hardening: every action in the kit workflows SHA-pinned
  (current versions verified against upstream docs); Dependabot groups action
  bumps into one weekly PR and now also watches the echo-agent starter's
  `uv.lock` (previously unowned).
- Docs aligned (root README, echo-agent README, RELEASING.md rehearsal
  procedure); repo-anchored test paths centralized in `tests/paths.py`.

## Implementation todo (ordered, GHCR blocker noted)

GHCR access blocks the *tail* of this list, not the start — the repo-side
code can be written and locally validated with zero registry access. Track A
below has no dependency on org-admin GHCR setup; Track B needs it (or a real
publish) and is ordered by its own internal dependencies.

**Track A — start immediately, no GHCR/merge dependency:**

1. ✅ Dockerfile: add `UV_EXCLUDE_NEWER` quarantine arg, wired into both
   `uv sync --locked` branches (wiring pinned by a mutation-tested drift
   test in `tests/docker/test_kit_spec.py`).
2. ✅ Dockerfile: add static OCI labels (source, licenses, title, description).
3. ✅ `spec.yaml`: bump `schemaVersion` `"1"` → `"2"`; update the drift test.
4. ✅ `scripts/stamp-kit-spec.py` helper + unit tests (library + the CLI
   contract the workflow invokes).
5. ✅ New reusable `.github/workflows/kit-publish.yml` (build→attest→stamp→
   push), plus a `workflow_dispatch` trigger for the pre-first-release
   rehearsal (`move-floating` defaults false on dispatch).
6. ✅ `release.yml`: add the kit publish call (`publish-kit`, via the
   reusable workflow) — image and kit artifact are jobs inside it.
7. ⚠️ `sbx`-on-runner remains unverified; the ORAS fallback was **promoted to
   primary** (media types per contrib's `spec/OCI-v2.md`; single empty
   tar+gzip layer). The push→pull→`--kit` roundtrip proof (open item #1) is
   still owed before the first release relies on it, and this drops the
   plan's `sbx kit validate` staging gate (the stamp helper's parse guards
   partially substitute).
8. ✅ `release.yml`: add the `bump-add-band` job (App-token, fail-loud on a
   sed miss, no-op until the add-band entry exists).
9. ✅ New `.github/workflows/kit-image-rebuild.yml` (weekly CVE rebuild).
10. ✅ Write `docker/band_python_kit/RELEASING.md`.
11. ✅ Rewrite `docker/band_python_kit/README.md` quickstart to the published
    path (workspace scaffolded from the release tarball — no repo checkout).
12. ❌ Run the quarantine gate proof locally (fail on a stale cutoff, pass on
    the real now−7d cutoff) — plain `docker build`, no registry needed.
    **Still owed**: needs a Docker daemon; run together with open item #7's
    pinned-uv reproduction and record in the PR test plan.

**— GHCR blocker line — everything below needs org-admin access, then a real
publish (which itself needs the PR merged to `dev`, then `main`) —**

13. ✅ GHCR org-admin prerequisites done (2026-07-19): package creation
    enabled for the org **and** the App installed on `add-band`. The
    pre-merge gate is cleared — items below are now blocked only on the
    merge/promote sequence.
14. 🔒 Post-merge release rehearsal — now concretely: dispatch `kit-publish`
    with a throwaway version (e.g. `0.0.0-rc1`), `move-floating: false`; see
    RELEASING.md. Blocked by #13.
15. 🔒 Flip both GHCR packages to public (one-way) — blocked by #14.
16. 🔒 Clean-machine acceptance proof (live) — blocked by #14 (needs a real
    published tag).
17. 🔒 `band-ai/add-band` catalog PR, merged to `main` — blocked by #14
    (bootstrap pins a real release tag).
18. 🔒 `docker/sbx-kits-contrib` catalog PR, opened — blocked by #14 (spec
    pins a real image digest).

## Validation

### Unit / drift (always-on CI)

- Stamp-helper tests: digest stamping, malformed-input rejection, output
  spec still parses and keeps every non-image field byte-identical.
- `test_kit_spec.py` drift updates: `schemaVersion == "2"`; existing launch
  shape/allowlist pins unchanged. The file carries no `docker_build` marker,
  so these run in ordinary (always-on) CI, not the opt-in Docker-build lane.
- Workflow YAML sanity is covered by GitHub's parser on push; no
  pseudo-tests that restate the workflow file.

### Quarantine proof (deliberate, mirrors INT-977's conflicting-dep proof)

Run the image build with a cutoff that predates locked packages (e.g.
`UV_EXCLUDE_NEWER=2020-01-01T00:00:00Z`): the build must **fail** with the
published-after-cutoff error — proving the gate is live, not decorative.
Then build with the real now−7d cutoff: passes (today's lock is older than
a week). Recorded once in the PR's test plan; the failing variant is not a
permanent CI job.

### Release rehearsal (after merge, before the first real release)

Only possible **after** the workflow lands on `main` (`workflow_dispatch`
requires the workflow on the default branch) — and the next release-please
PR merge fires the real publish, so rehearse promptly and hold the release
PR until it's green. `workflow_dispatch` a dry-run variant (or a temporary
prerelease tag like `0.0.0-rc`) exercising the full pipeline: multi-arch
build+push,
provenance/SBOM attached, digest output → stamp → `sbx kit validate` →
`sbx kit push`, floating tags. Verify on the GHCR package pages: repo
linkage, attestations, both tag sets. Then delete rehearsal tags and flip
package visibility to public (one-way door — flip only when the layout is
confirmed).

### Clean-machine acceptance proof (live)

On a machine (or fully scrubbed local state: no local kit dirs in play, no
`sbx template load`ed image, fresh sandbox, `kit.allowedSources` reset then
re-added per the quickstart):

1. Follow the quickstart verbatim: install/login, allowlist, workspace from
   the starter, configured `band.yaml` + credentials file.
2. `sbx create --kit ghcr.io/band-ai/band-python-kit:<tag> band-python-kit
   <workspace>` — no repo checkout, no local build.
3. Send a mentioned marker with the baseline toolkit
   (`ResourceManager`/`UserOps`/`reply_capture` — the same
   provision→send→await-reply barrier every kit proof has used); observe the
   echo reply from inside the sandbox.
4. `sbx policy log` shows only allowlisted hosts; reap all platform
   resources.

Local proof targets the dev platform per the INT-978 environment-targeting
decision (dev locally over VPN via a sandbox-scoped
`sbx policy allow network`; production endpoints are the kit's defaults) —
the flow is environment-agnostic by configuration.

### Catalog validations

- add-band: `scripts/check.py` + `pytest tests/ -q` green;
  `scripts/local-bootstrap.sh band-python-kit --print` sane; one live
  bootstrap run end-to-end (it *is* the quickstart in script form).
- sbx-kits-contrib: `sbx kit validate` clean on the contrib copy; local
  `scripts/test-kit.sh` and `scripts/test-kit-e2e.sh` runs recorded in the
  PR body; DCO + signed commits verified before pushing the branch.

## Acceptance criteria

- A clean machine completes the quickstart against the published GHCR kit
  (`sbx create … --kit ghcr.io/band-ai/band-python-kit:<tag>`) and the
  sandboxed agent answers a mentioned marker in a Band room (the ticket's
  `sbx run` phrasing realized via the kit's supported headless launch).
- `release.yml` publishes both artifacts on a release: semver-tagged,
  multi-arch, provenance+SBOM-attested image; v2 kit artifact whose spec
  pins that image by digest.
- The quarantine gate demonstrably fails a build containing under-aged
  packages and passes the aged lock.
- The weekly rebuild workflow exists, publishes `-rN` tags + moves floating
  tags only on real change, and PRs the digest-pin bumps back to the repo.
- RELEASING.md states artifact map, tag policy, cadence, ownership,
  quarantine override, and the supported `sbx` version.
- The add-band entry (full participating entry) is **merged** to `main`
  (band-ai owns it), and the sbx-kits-contrib PR (complete kit dir, locally
  validated, DCO+signed) is **opened** — both pointing at the published GHCR
  artifacts.
- Quickstart README covers prerequisites, allowlist step, the one command,
  workspace config, staging/prod switch, and the honest custody statement.

## Out of scope

- Proxy credential injection and "keys never enter the VM" claims
  (INT-980/981); self-registration (INT-982).
- Real-Sandbox automation on GitHub-hosted runners (nested-virt limitation
  stands; all sandbox-touching proofs remain developer-local).
- Docker Hub mirroring of the image (contingency only, if contrib review
  demands it).
- `SDK_EXTRA` image variants — v0.1 publishes the core-only image; the
  customer's framework extra comes from their own locked venv at runtime.
- Repo-wide SHA-pinning of pre-existing workflow actions; signing beyond
  GitHub-native attestations (no cosign).
- Usage/cost reporting; the **sbx-kits-contrib** merge timeline (owned by
  Docker's reviewers — add-band's merge is in scope, contrib's is not).

## Open verification during implementation

1. **[open — ORAS now primary]** **`sbx` CLI on ubuntu runners for registry-only ops** (`kit validate`,
   `kit push` — no VM needed in principle; Linux binary availability via
   `docker/sbx-releases` unconfirmed). ORAS fallback is fully specified (see
   "Fallback" above: exact media types, single empty tar+gzip layer for our
   no-`files/` kit); the only
   thing left to prove there is that ORAS emits the empty layer correctly and
   a push→pull→`--kit` roundtrip consumes clean. Last resort: the documented
   maintainer-laptop runbook.
2. **[open]** **schemaVersion "2" end-to-end on v0.34.0**: local `--kit <dir>` create,
   `kit push` → `kit pull` roundtrip, OCI-ref consume; plus revalidating the
   spec after adding `version`/`sourceURL`/`licenses`.
3. **[moot while ORAS is primary — we stamp explicitly]** **Whether v0.34.0 `sbx kit push` self-pins the image digest** (contrib's
   skill doc says it rewrites to distribution form; undocumented for this
   CLI version). We stamp explicitly either way — this only decides whether
   the stamp is belt-and-braces or load-bearing.
4. **[open — after the visibility flip]** **Anonymous public-GHCR pulls** by the sandbox VM (`sandbox.image`) and
   by `--kit` resolution (docs say pulls fall back to anonymous; v0.34.0
   rejected *local-only* image refs with 403 — a public GHCR ref must be
   proven once).
5. **[mitigated — verify on first publish]** **Kit-package repo linkage on CLI push**: the ORAS push now sets the `org.opencontainers.image.source` manifest annotation for GHCR linkage. Original concern (Actions auto-links only
   token-pushed packages; the kit artifact may need a manual link /
   visibility flip).
6. **[open — with the contrib PR]** **Contrib TCK/e2e viability for a headless, workspace-dependent kit** —
   run their harness locally early; adjust (`testdata/tck.yaml`, README
   caveats) based on what it actually exercises.
7. **[open — run with todo #12]** **Quarantine gate on the image's pinned uv 0.9.13** (probes ran on
   0.11.19): reproduce the `--locked` + `--exclude-newer` failure/pass pair
   with the pinned binary, or bump the uv pin in the same PR.
8. **[open — check at the acceptance proof]** **`sbx login` flow wording** for the quickstart (ticket lists it; confirm
   the current CLI's sign-in behavior on the pinned version).
9. **[resolved — `oras tag` re-tags, no repeat push]** **Floating-tag repush ergonomics** for the kit artifact (`sbx kit push`
   twice vs `crane`/`oras tag`) — pick whichever is idempotent.
10. **[done — org setting enabled 2026-07-19]** **First-publish org permissions** (zero band-ai packages currently): confirm
    with an org admin that workflow `GITHUB_TOKEN` package creation is
    allowed and public visibility is permitted. Requested on the ticket from
    the org admin; a **hard pre-merge gate** (see "One-time GHCR setup").
11. **[open]** **Attestation-laden image index consumability**:
    `provenance: mode=max` + `sbom: true` turns the pushed image into an
    OCI index carrying attestation manifests. Prove the sandbox VM's image
    puller tolerates it **early** with a throwaway attested image — under
    the plan's ordering this would otherwise surface only at the final
    clean-machine proof.
12. **[open — run with item #7; RELEASING.md already words the benign trip]** **Quarantine gate breadth**: confirm `uv sync --locked --exclude-newer`
    trips on a fresh **dev-only** locked package (whole-lock validation) —
    expected yes; informs RELEASING.md's benign-trip wording. Run together
    with item #7's pinned-uv reproduction.
13. **[open]** **Floating-tag freshness under kit caching**: v0.34.0 caches OCI kits —
    confirm `--kit …:latest` re-resolves on sandbox re-create rather than
    serving a stale cached artifact. The CVE-cadence story ("customers on
    latest pick up rebuilds on re-create") depends on this.
14. **[open — the stamp emits the combined form today]** **Combined `name:tag@digest` ref for the stamped `sandbox.image`**:
    confirm sbx accepts the combined form; if not, stamp digest-only.
15. **[open]** **`AGENT` positional resolution from an OCI kit ref**: `sbx create …
    band-python-kit <ws>` is verified for local-dir kits only, and
    `sbx create --help` shows `AGENT` is normally a fixed built-in list —
    make item #2's OCI-consume roundtrip exercise the same positional path.
16. **[open — now load-bearing: ORAS is the primary push path]** **ORAS and the missing `_kit_<tag>` GC anchor**: `sbx kit push`
    emits it, ORAS wouldn't — likely harmless on GHCR (the image is
    independently tagged in its own package) but confirm nothing on the
    pull path expects it.
17. **[done — installed 2026-07-19]** **GitHub App installation on `band-ai/add-band`** (for the automated
    bump PR): confirm installed with `contents: write` +
    `pull-requests: write`, or bundle the ask with the GHCR org-admin
    request.

## Information resources

### Linear

| Source | Relevance |
|---|---|
| [INT-979](https://linear.app/thenvoi/issue/INT-979) | Scope and acceptance source of truth. |
| [INT-977](https://linear.app/thenvoi/issue/INT-977) / PR [#443](https://github.com/band-ai/band-sdk-python/pull/443) | The image being published (digest pins, isolated venv, CA wiring). |
| [INT-978](https://linear.app/thenvoi/issue/INT-978) / PR [#445](https://github.com/band-ai/band-sdk-python/pull/445) | The kit being published (spec v2 shape, headless launch decision, live dev proof). |
| [INT-980](https://linear.app/thenvoi/issue/INT-980), [INT-981](https://linear.app/thenvoi/issue/INT-981) | The custody upgrade the quickstart's honest statement points to. |

### Repository

| Source | Relevance |
|---|---|
| `.github/workflows/release.yml` | The release-please pipeline the publish jobs extend (`release_created`, `tag_name` outputs; `environment: release`). |
| `release-please-config.json`, `.release-please-manifest.json` | Version source (1.2.0 on `main` at time of writing; git tags are `band-sdk-vX.Y.Z`). |
| `docker/band_python_kit/{Dockerfile,spec.yaml,entrypoint.sh,README.md}` | The artifacts being released; digest-pin ARGs; `:local` dev image ref. |
| `docker/band_python_kit/echo-agent/` | The starter the quickstart and add-band bootstrap scaffold from. |
| `tests/docker/test_kit_spec.py` | Drift tests to keep green (they pin launch shape + allowlist, not `sandbox.image`). |
| `tests/e2e/baseline/README.md` + toolkit | Provisioning/messaging/reply-capture for the clean-machine proof. |

### External

| Source | Relevance |
|---|---|
| [uv resolution — exclude-newer](https://docs.astral.sh/uv/concepts/resolution/) + [settings reference](https://docs.astral.sh/uv/reference/settings/#exclude-newer) | Quarantine flag semantics (RFC 3339 dates; duration/cooldown forms; PEP 700 `upload-time` requirement). |
| [astral-sh/uv#18775](https://github.com/astral-sh/uv/issues/18775) | Why the cutoff stays a CI flag and never enters `pyproject.toml`/`uv.lock`. |
| [PyPI incident report, 2026-04-02](https://blog.pypi.org/posts/2026-04-02-incident-report-litellm-telnyx-supply-chain-attack/) | First-party grounding for the dependency-cooldown design. |
| [Publish Docker images (GitHub tutorial)](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images) | Canonical GHCR workflow: permissions block, SHA pinning, attest step. |
| [GHCR docs](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) + [visibility/access](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility) | Private-by-default packages, one-way public flip, repo linkage rules, `org.opencontainers.image.source`. |
| [Docker build attestations in GHA](https://docs.docker.com/build/ci/github-actions/attestations/) | `provenance: mode=max`, `sbom: true`, registry-push requirement. |
| [arm64 hosted runners GA](https://github.blog/changelog/2025-08-07-arm64-hosted-runners-for-public-repositories-are-now-generally-available/) | The native-build upgrade path if QEMU proves slow. |
| [Docker kits guide](https://docs.docker.com/ai/sandboxes/customize/kits/) | `--kit <oci-ref>` resolution, `kit.allowedSources` syntax, push/pull auth asymmetry. |
| [sbx release notes](https://docs.docker.com/ai/sandboxes/release-notes/) / [docker/sbx-releases](https://github.com/docker/sbx-releases/releases) | v0.34.0 (OCI v2 kit artifacts, allowedSources default) vs v0.35.0 (`kit add` recreate semantics) — revalidate on any CLI upgrade. |
| [docker/sbx-kits-contrib](https://github.com/docker/sbx-kits-contrib) — `CONTRIBUTING.md`, `spec/SPEC-v2.md`, `spec/OCI-v2.md`, `skills/kit-author/topics/distribution.md` | Contribution contract (DCO+signatures, template, local e2e mandate), v2 kit metadata fields, OCI artifact format (the ORAS plan B), digest-pin rule, GC-anchor tag. |
| [band-ai/add-band](https://github.com/band-ai/add-band) — `CONTRIBUTING.md`, `scripts/check.py`, `_template/` | Participating-entry contract: manifest fields, `BAND_API_KEY` rule, five-section README, pin-refs + fail-loud conventions. |
| [aquasecurity/trivy-action](https://github.com/aquasecurity/trivy-action) | Scan step for the weekly rebuild gate. |
