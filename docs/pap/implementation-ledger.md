# PAP integration implementation ledger

Date: 2026-10-16. Status: foundation and initial discovery validation implemented; no completed PAP integration is claimed. Draft PR: [#820](https://github.com/band-ai/band-sdk-python/pull/820).

Worktree: `sdk-worktrees/pap-integration`. Branch: `feat/pap-protocol-integration`. Base: `c73cd9cd` from upstream main. Package: `packages/band-pap` (`bandpap`). Maintained architecture: [design](design.md) and [implementation contract](contract.md). This tracked ledger is authoritative; the earlier local planning copy is not the implementation record.

## Product north star

Band is the collaboration and communication layer for agents. PAP brings external agents and companies into that collaboration through protocol participants. It must not become a reasoning framework, a shopping coordinator, or a business-policy engine.

The target includes both client adapter and server gateway. All conversations occur in one Band room with independently addressed participants and configurable participant visibility. External identity, authorization, sessions, and conversations remain scoped to their owners and companies. No dependency on a particular agent runtime; no changes to existing Band SDK runtime internals. New integration code may be separately packaged against public SDK interfaces.

## Completion and review rules

- States: pending, active, review, complete. An implementation phase is complete only when its acceptance evidence exists and both adversarial reviews have been resolved. A completed phase is not a claim that the integration is ready to release.
- Every item P01–P07 receives two independent `gpt-6.1-sol` adversarial reviews: A attacks protocol/identity/isolation/recovery correctness; B attacks Band product fit, architecture, integration boundaries, and test sufficiency. Both may challenge any issue they find.
- Record reviewed revision/files, reviewer names, findings, dispositions, and rerun evidence. Re-review material changes; do not count an earlier review as approval of later implementation.
- Tests must exercise observable behavior. Record failed, skipped, and unavailable validation explicitly. Live interoperability and participant-visibility enforcement remain unverified until tested.
- Open a draft PR early and keep it draft until the complete target contract passes. Keep PR prose concise; store progress and evidence here.

## Implementation items

| ID | Item and full scope | Acceptance evidence | State | Review A | Review B |
|---|---|---|---|---|---|
| P01 | Isolated worktree/branch/draft PR; integration contract; package and public extension boundaries; protocol revision and test plan. | Clean base without unrelated reproduction tests; runtime-neutral architecture; version provenance; reviewable ledger and contract. | complete | approved | approved |
| P02 | Protocol models, discovery/capability rules, errors, durable scoped connections/conversations/delivery/cursor/operation storage. | Wire fixtures and invalid-input cases; collision/ownership isolation; restart and transaction failure tests. | active — discovery increment only | increment approved; remainder pending | increment approved; remainder pending |
| P03 | Identity, signing, account linking, session renewal/revocation, DPoP/Bearer handling, credential isolation. | Wrong issuer/user/client/resource rejection; proof replay/nonce tests; expiry and revocation; secrets excluded from agent views. | pending | pending | pending |
| P04 | Outbound company participant adapter; addressed routing; messages and protocol state; concurrent connections; lifecycle and recovery. | Multiple company participants in one Band room; live/replay filtering; independent progress; loss/duplicate/restart cases. | pending | pending | pending |
| P05 | Inbound PAP gateway; service discovery; authorized participant bindings; conversation/event endpoints; human participation, handoff, closing. | Both directions in the same room; client ownership checks; event ordering/reconnect; no implicit room access; roster projection tests. | pending | pending | pending |
| P06 | Operations and advertised API/browser channels; trusted approval/execution interfaces, cancellation, uncertainty and deduplication. | Revision-bound confirmation; cross-channel deduplication; scope enforcement; unknown effects remain unresolved; application policy stays outside protocol. | pending | pending | pending |
| P07 | Complete conformance, integration/fault testing, live interoperability, observability, deployment/examples, compatibility. | Entire acceptance matrix including lifecycle and visibility; recorded live results or explicit release blockers; no runtime coupling. | pending | pending | pending |

## Evidence and review log

| Entry | Item | Evidence or finding | Disposition |
|---|---|---|---|
| E001 | P01 | User authorized implementation, isolated worktree/branch/draft PR, and two Sol adversarial reviews per item. | Start from current upstream main; do not reuse the NOOA reproduction branch. |
| E002 | P01/P04/P05 | Earlier platform source review confirmed mention-gated live agent delivery, but default history can include messages with empty/missing mentions. Roster filtering was not verified. | Explicit addressed-input filtering and visibility acceptance tests are required; do not infer privacy from delivery alone. |
| A01 | P01 | Sol reviewer A (`pap_review_a`): HIGH — proxy mention alone does not authorize selecting downstream user/client credentials. | Contract now requires authenticated actor grants, unambiguous owner/client/connection authority, and grant-bound reply recipients, including replay. Reviewer A approved P01 on re-review of `242ebf40`. |
| A02 | P01 | Reviewer A: MEDIUM — coordination objectives/disclosure/purchase intents appeared as generic integration storage. | Design labels these application-owned; integration retains only protocol state and authorization references. Reviewer A approved P01 on re-review of `242ebf40`. |
| B01 | P01 | Sol reviewer B (`pap_review_b`): HIGH — distinct participants cannot be labels on one gateway credential. | Contract now requires independently authorized proxy credentials and transport ownership. Reviewer B approved on re-review. |
| B02 | P01 | Reviewer B: HIGH — private structured actions cannot use unfiltered event metadata. | Contract specifies authorized command/record storage with grant-scoped opaque references; no raw private payload on unfiltered events. Reviewer B approved on re-review. |
| B03 | P01 | Reviewer B: MEDIUM — separate package is invisible to SDK-only guards. | Added package source/type roots, explicit import-boundary test, normal tests collection, and installed-wheel CI smoke; adapter constructor conformance remains an explicit P04 obligation. |
| E003 | P01/P02 | Initial local checks: 24 PAP tests passed; PAP Pyrefly check passed; entire repository Ruff and format checks passed (1,634 Python files). Installed PAP wheel smoke passed with freshly built SDK wheel and no agent extras. | Preliminary evidence; rerun affected checks after review fixes. |
| A03/B04 | P02 discovery increment | Both reviewers: MEDIUM — `urlsplit` accepts or discards control characters, allowing inconsistent endpoint/issuer interpretation. | Reject C0 and DEL before parsing; 99 parameterized regression cases cover endpoints, issuers, and fetched URLs. Both reviewers approved the fix on re-review. |
| B05 | P01 | Reviewer B: MEDIUM — the original import guard allowed `from band import runtime`; its public-module list also referenced stale module locations. | Exact public-symbol allowlist replaces broad Band-module acceptance. Tests reject 9 forbidden import forms and accept 5 public forms; reviewer B approved the correction. |
| E004 | P01/P02 | Broad local checks in minimal Python 3.12 environment: repository Pyrefly reported 152 errors; full unit collection stopped with 94 errors and 12 skips, including absent optional framework dependencies. | No clean full-suite baseline claimed. Focused PAP tests run through normal root conftest. Full development CI and live interoperability remain unverified. |
| E005 | P01/P02 | After review fixes: **137 PAP tests passed** (8 binding, 15 schema, 99 control-character, 15 import-boundary cases). Focused Ruff/format and PAP Pyrefly checks passed. Rebuilt and reinstalled the PAP wheel in the isolated consumer environment; smoke passed. Both reviewers independently ran all 137 tests; B also ran the isolated wheel smoke. | Current focused evidence. Broad failures in E004 remain unresolved/unverified; no full-suite or live-interoperability pass claimed. |
| E006 | P01/P02 | Initial PR CI at `242ebf40`: lint and packaging passed. [Core coverage job](https://github.com/band-ai/band-sdk-python/actions/runs/38975805849/job/117224846160) failed before tests during the separate core-repository checkout: `Input required and not supplied: token`. Other jobs were still running when inspected. | CI is not fully green. Checkout credentials are an external workflow prerequisite; no secrets or workflow permissions were changed. |

Reviewed implementation snapshot: foundation commit `242ebf40`, followed by the URL-controls and import-guard corrections. SHA-256 of sorted `packages/band-pap/src/**/*.py` and `tests/pap/**/*.py` paths/content (each path and content NUL-delimited): `fbdb3e7c1def04f1bd9651201b423e920c5958213439f47a67d81e427edecc4d`. Reviewer A (`pap_review_a`) and reviewer B (`pap_review_b`), both `gpt-6.1-sol`, approved the reviewed discovery increment after fixes on 2026-10-16. P01 architecture/package review is approved by both; P02 approval covers only this increment. Ledger-only status updates do not change this snapshot.

## Current implementation coverage

Only the package scaffold and discovery validation are implemented. The parser preserves exact issuer spelling and verifies final discovery host, exact issuer equality, and issuer-authorized company domains. It validates required interfaces and basic HTTPS metadata shape. It does not fetch endpoints, validate network destinations, authenticate, or authorize an action. Partial discovery models are not a complete protocol implementation.

P02 still requires the remaining wire/extension models, network discovery behavior, structured errors, and durable scoped state with fault tests. P03–P07 are pending. No gateway or adapter class is advertised as usable.

## Reproducing focused checks

Run in a workspace environment with the SDK and `band-pap` installed, plus pytest, Ruff, and Pyrefly:

```sh
uv run --all-packages pytest tests/pap -q
uv run --all-packages ruff check packages/band-pap tests/pap .github/scripts/pap-wheel-smoke.py
uv run --all-packages pyrefly check packages/band-pap/src
uv build --package band-pap --wheel -o dist-band-pap
```

The CI wheel job installs both built wheels into a fresh environment and runs `.github/scripts/pap-wheel-smoke.py`. Local validation used isolated environments under `/private/tmp` without modifying existing development environments. Tool versions: Python 3.12.12, pytest 9.1.1, Pydantic 2.14.1, Ruff 0.17.1, Pyrefly 1.4.0.

## Release gate

P01–P07 must all be complete with both reviews resolved. Business examples demonstrate collaboration but cannot impose a global coordinator, purchase policy, or one-request-per-room lock on the generic integration. Unsupported draft capabilities and unavailable live counterparties must be disclosed rather than reported as passing.
