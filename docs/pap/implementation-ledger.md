# PAP integration implementation ledger

Updated: 2026-10-17. Status: P01–P06 implemented with two independent Sol reviews per item. P07 is partial: the runnable multi-peer example, host-factory tutorial and offline acceptance tests are implemented; live acceptance remains outstanding. The private-core coverage credential is also a release gate; no live interoperability or release-readiness claim. Draft PR: [#820](https://github.com/band-ai/band-sdk-python/pull/820).

Worktree: `sdk-worktrees/pap-integration`. Branch: `feat/pap-protocol-integration`. Base: `c73cd9cd` from upstream main. Package: `packages/band-pap` (`bandpap`). Maintained architecture: [design](design.md) and [implementation contract](contract.md). This tracked ledger is authoritative; the earlier local planning copy is not the implementation record.

## Product north star

Band is the collaboration and communication layer for agents. PAP brings external agents and companies into that collaboration through protocol participants. It must not become a reasoning framework, a shopping coordinator, or a business-policy engine.

The target includes both client adapter and server gateway. All conversations occur in one Band room with independently addressed participants and configurable participant visibility. External identity, authorization, sessions, and conversations remain scoped to their owners and companies. No dependency on a particular agent runtime; no changes to existing Band SDK runtime internals. New integration code may be separately packaged against public SDK interfaces.

## Completion and review rules

- States: pending, active, review, complete. An implementation phase is complete only when its acceptance evidence exists and both adversarial reviews have been resolved. A completed phase is not a claim that the integration is ready to release.
- Every item P01–P07 receives two independent `gpt-6.1-sol` adversarial reviews covering protocol/identity/isolation/recovery and product fit/architecture/test sufficiency. An author never counts as their own independent reviewer. Reviewer assignments below reflect authorship changes during implementation.
- Record reviewed revision/files, reviewer names, findings, dispositions, and rerun evidence. Re-review material changes; do not count an earlier review as approval of later implementation.
- Tests must exercise observable behavior. Record failed, skipped, and unavailable validation explicitly. Live interoperability and participant-visibility enforcement remain unverified until tested.
- Open a draft PR early and keep it draft until the complete target contract passes. Keep PR prose concise; store progress and evidence here.

## Implementation items

| ID | Item and full scope | Acceptance evidence | State | Review A | Review B |
|---|---|---|---|---|---|
| P01 | Isolated worktree/branch/draft PR; integration contract; package and public extension boundaries; protocol revision and test plan. | Clean base without unrelated reproduction tests; runtime-neutral architecture; version provenance; reviewable ledger and contract. | complete | approved | approved |
| P02 | Protocol models, discovery/capability rules, errors, durable scoped connections/conversations/delivery/cursor/operation storage. | Wire fixtures and invalid-input cases; collision/ownership isolation; restart and transaction failure tests. | complete — protocol/state primitives | approved | approved |
| P03 | Identity, signing, account linking, session renewal/revocation, DPoP/Bearer handling, credential isolation. | Wrong issuer/user/client/resource rejection; proof replay/nonce tests; expiry and revocation; secrets excluded from agent views. | complete — implementation | approved | approved |
| P04 | Outbound company participant adapter; addressed routing; messages and protocol state; concurrent connections; lifecycle and recovery. | Multiple company participants in one Band room; live/replay filtering; independent progress; loss/duplicate/restart cases. | complete — implementation | approved | approved |
| P05 | Inbound PAP gateway; service discovery; authorized participant bindings; conversation/event endpoints; human participation, handoff, closing. | Both directions in the same room; client ownership checks; event ordering/reconnect; no implicit room access; roster projection tests. | complete — implementation | approved | approved |
| P06 | Operations and advertised API/browser channels; trusted approval/execution interfaces, cancellation, uncertainty and deduplication. | Revision-bound confirmation; cross-channel deduplication; scope enforcement; unknown effects remain unresolved; application policy stays outside protocol. | complete — implementation | approved | approved |
| P07 | Complete conformance, integration/fault testing, live interoperability, observability, deployment/examples, compatibility. | Entire acceptance matrix including lifecycle and visibility; recorded live results or explicit release blockers; no runtime coupling. | active — offline demo complete; live acceptance outstanding | approved for offline scope | approved for offline scope |

## P02 implementation breakdown

| Subitem | Scope | State |
|---|---|---|
| P02a | Conversation/event/error and Operations wire models; preserve structured data and skip unknown events | complete |
| P02b | Complete discovery fields, explicit capability selection, bounded HTTPS fetching with connected-address validation | complete |
| P02c | Durable connection/conversation/message/event/cursor storage; distinct conversation/operation ownership | complete |
| P02d | Atomic delivery leases, uncertain-result recovery, restart/isolation/transaction-failure tests | complete |
| P02e | Two Sol adversarial reviews of the combined implementation; resolve findings and record evidence | complete — both approved |

These are implementation dependencies, not reduced product scope. Authentication enforcement and token/JWT handling remain P03; actual Band forwarding P04/P05; operation execution/approval and channel clients P06. Storage inputs are trusted integration inputs and do not themselves authenticate a caller.

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

Initial discovery review snapshot: foundation commit `242ebf40`, followed by the URL-controls and import-guard corrections. SHA-256 of sorted `packages/band-pap/src/**/*.py` and `tests/pap/**/*.py` paths/content (each path and content NUL-delimited): `fbdb3e7c1def04f1bd9651201b423e920c5958213439f47a67d81e427edecc4d`. Reviewer A (`pap_review_a`) and reviewer B (`pap_review_b`), both `gpt-6.1-sol`, approved the reviewed discovery increment after fixes on 2026-10-16. P01 architecture/package review is approved by both; P02 approval covers only this increment. Ledger-only status updates do not change this snapshot.

## P02 adversarial review and failure ledger

Both reviewers read the pinned protocol before reviewing the implementation. They reviewed the combined P02a–P02d code and tests; P02e records their final verdicts. Findings below were reproduced against the work-in-progress implementation, not against the earlier discovery-only commit.

| ID | Reviewer / severity | What failed | Regression and disposition |
|---|---|---|---|
| A04/B08 | A and B / medium | An in-progress operation snapshot omitting optional proposal expiry was rejected as changed terms. The first full P02 test run failed this case. | `test_confirmed_snapshot_may_omit_proposal_expiry_and_uncertainty_stays_in_progress`: retain recorded expiry for comparison when the later non-proposal snapshot omits it; fixed and passing. |
| A05 | A / high | A 429/503 response could mark an outbound message delivered and remove retry work. Reviewer probe reproduced it. | `test_retryable_response_keeps_work_and_retry_time_across_restart`: accepted settlement requires valid 201/202 acknowledgment; `RetryFailure` preserves refusal and retry time on disk; uncertain acceptance retains the same message ID. Fixed and passing for 429 and 503. |
| A06/B07 | A and B / medium | A succeeded action with no confirmation, or expired action with confirmation, was accepted as a valid wire snapshot. | `test_executed_outcome_requires_confirmation` and `test_expired_proposal_cannot_have_confirmation`: contradictory states rejected. Fixed and passing. |
| A07 | A / medium | Known context fields accepted malformed language tags, nonexistent timezones and explicit null availability. | `test_invalid_known_context_does_not_cross_wire` and `test_valid_context_retains_original_values`: validate with langcodes and zoneinfo/tzdata, preserve original valid tags and unknown JSON context. Fixed and passing. |
| B06 | B / medium | Waiting for TLS socket shutdown could outlast the total discovery deadline. Reviewer reproduced the delay through httpcore. | `test_stalled_tls_shutdown_is_aborted_without_waiting`: abort transport without a peer handshake; also abort failed/cancelled TLS upgrades. Fixed and passing. |
| E007 | Test run / error normalization | A NUL redirect header was rejected by h11 before application validation, producing `RemoteProtocolError` instead of the documented discovery failure. The first full P02 test run failed this case. | Normalize HTTP protocol failures to `DiscoveryFailure`; the forbidden location is never dialed. Fixed and passing. |
| A08 | A / medium | The first final-state guard froze the entire operation snapshot, rejecting a legal later URL addition or optional expiry omission. | `test_final_snapshot_can_refresh_optional_metadata_not_state_or_terms`: freeze terminal state, revision terms and confirmation, while accepting optional metadata refresh. Fixed; both reviewers approved the final correction. |

## P02 implementation coverage at `453136b3` (historical snapshot)

P02 implements conversation requests/responses, typed known events and skippable unknown events, structured errors, context and Operations wire models. Arbitrary `data`, context extensions and operation terms survive JSON and durable roundtrips. It includes conditional sign-in discovery metadata and explicit capability intersection; advertisement alone never selects an unsupported feature.

Discovery fetching follows bounded HTTPS redirects, verifies each connected public numeric address and the original TLS hostname, rejects oversized/compressed/non-JSON responses, and verifies final host, exact issuer and issuer-authorized domains. RFC 8414 issuer-path placement is covered. It deliberately has no ambient proxy, cookies or authorization headers. HTTP caching remains unimplemented; this draft does not claim the protocol's SHOULD-cache optimization.

SQLite stores scoped connections, independent conversation bindings, user-wide message deduplication, original acknowledgments, leased delivery/retry state, events and cursors, and immutable operation revision terms. Tests reopen disk databases, use independent connections/concurrent insertion, and inject both cursor-write and commit failures. Operations are authenticated observations only; recording one never approves or executes an effect. The scope includes tenant, user, client, issuer and direction; neither room nor connection instance partitions message-ID deduplication.

The store takes trusted host inputs; it does not authenticate callers or evaluate grants. Recorded connection revocation fences subsequent claims/replays/settlements, but cannot recall a request already in flight. P03 must implement authenticated authority and token handling. P04/P05 were still required to enforce source grants, original-binding visibility, actual Band handoff and direct-conversation lifecycle. P06 was still required to implement operation execution/approvals and channel clients. These are phase boundaries, not claims of completed integration. At that P02 snapshot no adapter or gateway was usable. P03–P07 below record the implemented runtime and outstanding acceptance work.

## P02 test ledger

| Test file | What it establishes | Latest result |
|---|---|---|
| `test_capabilities.py` | Unknown protocol/extension isolation; scopes/resources retained; conditional OAuth endpoints required | 4 passed |
| `test_delivery_store.py` | Lost first-response recovery; user-wide deduplication; independent queues; multi-handle insertion; lease fencing; retry timing; revocation; owner/account isolation; failed-commit rollback | 16 passed |
| `test_event_store.py` | Ordered opaque cursors; replay dedupe; unknown events; expiry gaps; cursor/event atomicity; concurrent cursor fencing | 4 passed |
| `test_fetch.py` | Actual HTTP parsing with deterministic network doubles: redirects, OAuth chain, RFC 8414 path, size/encoding/status limits, timeouts and host mismatch | 18 passed |
| `test_network.py` | Nonpublic/mixed DNS rejection; numeric dial without re-resolution; original TLS hostname; failed TLS and nonblocking shutdown | 17 passed |
| `test_operation_store.py` | Session-only versus account-bound ownership; restart; revision CAS; terms immutability; legal optional metadata updates; terminal-state protection | 5 passed |
| `test_wire.py` | IDs, sender provenance, structured/context-only messages, event types, wait/cursor rules, Operations states and context formats | 35 passed |
| Existing discovery/binding/control/import-boundary tests | Earlier discovery guarantees and runtime independence; import allowlist extended for declared HTTP/validation libraries | 137 passed |

**Total: 236 passed.** Focused package Pyrefly reports zero diagnostics. Repository Ruff and formatting checks passed (1,647 Python files). The final rebuilt wheel installed without agent-framework extras passed module/dependency imports, domain-mismatch rejection and disk-reopen smoke. Reviewer B independently repeated the installed-wheel smoke.

Broad checks were rerun: the minimal local environment still has **152 repository Pyrefly errors**, and unit collection still stops with **94 errors, 12 skips**. These include missing optional frameworks and existing type diagnostics; no full-suite pass is claimed. An additional attempt to run shared MCP source-boundary guards stopped in their conftest because the minimal environment lacks `mcp`; PAP’s dedicated import-boundary cases passed. Live HTTPS counterparty interoperability and full remote CI remain unverified. The deterministic fetch tests do not claim a live service was contacted.

Final P02 reviewed source/test snapshot: SHA-256 `f8ffb465074765fa003c6ab13cad041d2550f19aae24339668fb51a88a58870e`, using sorted `packages/band-pap/src/**/*.py` and `tests/pap/**/*.py` paths and contents, each NUL-delimited. Reviewer A (`pap_review_a`) and reviewer B (`pap_review_b`), both `gpt-6.1-sol`, independently ran all 236 tests and approved this snapshot after re-review. No unresolved P02 findings remain. Approval does not cover P03–P07 or release readiness. Subsequent ledger/PR status edits do not change this fingerprint.

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

## P03–P07 implementation and independent review ledger

Historical source/test snapshot at `b24d33e7` (same sorted path/content NUL-delimited algorithm as
P02): SHA-256 `2b9f422eb576cc24031a39adc4e115eeaccc2d1bfa05d2292d25f7bd190fcbdb`.
Documentation-only updates do not change it. Existing Band SDK runtime files are
unchanged. Public integration exports, deployment guidance and an ASGI launcher are
included. The launcher requires a host factory; no self-contained example is bundled.

| Item | Implemented result | Independent reviewers and final evidence |
|---|---|---|
| P03 | Encrypted credential store, pairwise identity, assertions/DPoP, gateway JWT verification with live resolver, direct/device/mediated sign-in, scoped refresh claims and revocation recovery | B (`pap_review_b`) and Operations reviewer (`pap_operations`); both approved after fixes. 61 focused cases passed. A authored this phase and is not counted as its independent reviewer. |
| P04 | Public SDK adapter, grant-bound input, durable per-connection pumps, typed records, account-aware continuation, handoff/direct/close, real generated REST encoding and uncertain-send reconciliation | A (`pap_review_a`) and Operations reviewer; both approved. 36 focused cases passed. A independently reran the final 425-case suite and probed token-wait revocation. |
| P05 | Authenticated ASGI gateway, discovery, deduplicated durable conversations/events, bounded JSON/SSE, direct/handoff/close, roster projection, operation routes, explicit same-room reply correlation | A and Operations reviewer; both approved. 43 gateway/bridge/operation-route cases passed. B authored the gateway and is not counted as its independent reviewer. |
| P06 | Application-owned authorization/approval/execution interfaces, durable operation identity and recovery, constrained OpenAPI/MCP/browser clients | A and B; both approved after authority-race and dispatch-fence corrections. 43 focused cases passed. Operations reviewer authored this phase. |
| P07 | Four offline roundtrip/fault/isolation scenarios, lifespan shutdown/failure coverage, package exports, deployment guide, launcher, rebuilt installed-wheel validation | A and B approved implementation/offline evidence and documents. Six integration/lifespan cases passed. A independently ran the final wheel smoke and matched every installed module against current source. Approval covers the offline scope only; the standalone example and live acceptance remain outstanding. |

All three review agents used `gpt-6.1-sol`. No independent approval is attributed to
an agent for code it authored. The reviewers exchanged findings while root owned
the adapter, durable room bridge and final integration. Final approvals follow
fixes and regressions, not just initial source inspection.

## P03–P07 discovered-failure ledger

These failures describe work-in-progress implementations or reviewer probes.
**Every regression below now passes; no failing PAP test is being hidden by xfail.**
The final broad-environment and external failures are listed separately below.

| ID / phase | What failed and why it matters | Regression / final disposition |
|---|---|---|
| F03-01 / P03 | Revocation deleted the refresh token before remote success; reconstructing a client could revive locally revoked access. | `test_failed_revocation_remains_disabled_and_retryable_after_restart`: preserve disabled encrypted retry material; constructors never revive tombstones. Fixed. |
| F03-02 / P03 | Independent OAuth clients could rotate one refresh token concurrently; post-network CAS alone could lose the surviving token. | `test_independent_clients_cannot_rotate_the_same_refresh_concurrently`, `test_lost_refresh_response_is_not_replayed_after_restart`, `test_expired_exchange_claim_never_reuses_refresh`: durable scoped ownership and uncertain-refresh fencing. Fixed. |
| F03-03 / P03 | Direct/device sign-in forgot requested scopes and accepted elevated returned scopes. | `test_direct_state_issuer_and_scope_cannot_be_swapped`, `test_device_timing_slowdown_and_completion_scope`: consent scopes persist through completion. Fixed. |
| F03-04 / P03 | Malformed embedded JWK/cnf could escape as runtime errors; ordinary assertions without optional `typ` and mixed public JWKS were rejected. | `test_malformed_public_key_is_an_authentication_error`, assertion/public-key cases and gateway claim matrix: fail closed and accept the supported compatible assertion shape. Fixed. |
| F03-05 / P03 | Endpoint prefix checks accepted dot-segment traversal; device links were not validated before display. | Path-traversal/credential-header/resource negatives in `test_oauth_requests.py`; device-link cases in `test_oauth_signin.py`. Fixed. |
| F03-06 / P03 | Cached invalid tokens kept being reused; mediated sign-in racing revocation could discard a newly issued refresh credential. | `test_invalid_token_renews_and_mcp_headers_remain_available`, `test_late_mediated_refresh_is_retained_only_for_revocation`: bounded renewal and late-response fencing. Fixed. |
| F03-07 / P03 | A successful direct/device response could be signed out or omit the account refresh credential, replacing valid prior consent. | Four `test_signin_completion_requires_signed_in_refresh_credential_without_replacing_existing` cases reject malformed completion before commit. Fixed. |
| F04-01 / P04 | Grant provisioning could point at another connection's existing conversation, and fail after leaving orphan state. | `test_existing_binding_cannot_select_another_downstream_connection`: transactional grant/connection/conversation checks. Fixed. |
| F04-02 / P04 | Original anonymous grant sessions were reused after account binding; old account history could remain anonymously available or legitimate renewal fail. | `test_account_use_binds_history_and_signed_out_cannot_replay`, current-account direct cases: resolve live session and bind local history before use. Fixed. |
| F04-03 / P04 | Revocation during token renewal or Band identity lookup could still be followed by a message POST. | `test_revocation_while_request_acquires_token_prevents_company_post`, actual generated REST identity/authority tests: recheck immediately before dispatch. Fixed. |
| F04-04 / P04 | Empty reconciliation IDs could authorize blind resend; denied grants could exhaust the outbox budget and starve another owner. | `test_uncertain_band_send_is_not_automatically_repeated_after_restart`, `test_denied_grants_cannot_exhaust_budget_before_valid_owner`: explicit evidence and per-grant progress. Fixed. |
| F04-05 / P04 | Control acknowledgments wrongly required repeated conversation IDs; direct conversations used stale account sessions. | Four `test_pap_controls.py` cases cover optional IDs, current-account human direct conversations and permanent refusal recovery. Fixed. |
| F04-06 / P04 | Serializing a staged context with unset fields emitted nulls rejected on durable reread. | `test_typed_input_uses_trusted_original_message_binding`: persist the wire projection, retaining valid data/context. Initial test failed, now fixed. |
| F05-01 / P05 | Company-initiated parent closure left a direct child open; fresh-create authority was unnecessarily required to recover an existing first message. | `test_company_closing_parent_also_closes_direct_child`, original-authority retry and direct lifecycle tests. Fixed. |
| F05-02 / P05 | Account binding could leave stale anonymous pending work that crashed the whole dispatcher and blocked another owner. | `test_account_transition_reauthorizes_pending_work_without_blocking_other_owner`, explicit fresh-authority resume test: migrate/revalidate continuation and isolate blocked work. Fixed. |
| F05-03 / P05 | Scope errors omitted required metadata; sign-in advertisement omitted conditional endpoints; decoded paths broke DPoP for native operation IDs. | Gateway authentication/configuration matrix and `test_native_operation_id_uses_exact_encoded_request_target`. Fixed. |
| F05-04 / P05 | An unexpected host exception during SSE could escape after response start with a private diagnostic. | `test_midstream_host_diagnostic_does_not_escape_to_asgi_logging`: terminate the stream without exposing the host exception. Fixed. |
| F05-05 / P05 | An intermediate middleware registration used an API absent from the installed Starlette release. | Whole-suite constructor failures reproduced; supported middleware registration restored. Final suite passes. |
| F06-01 / P06 | Authorization for revision 1 could read, cancel or recover revision 2 after an asynchronous race. | Two-store gated `test_changed_snapshot_requires_new_authority_before_projection_or_mutation` cases reauthorize changed snapshots before use. Fixed. |
| F06-02 / P06 | Approval could be withdrawn during final authorization/token renewal and still dispatch; cancel omitted its JSON body. | `test_withdrawn_approval_during_final_authorization_blocks_claim`, `test_withdrawn_approval_during_refresh_blocks_confirm_dispatch`, cancellation wire assertion. Fixed. |
| F07-01 / P07 | Initial roundtrip evidence exercised each direction but did not chain an inbound client through the outbound adapter and back through the reply adapter. | Fourth `test_roundtrip.py` scenario uses both actual adapters, gateway, DPoP and durable outboxes in one room. Added and passing. |

The suggested malformed-`jti` finding was retracted after execution confirmed the
JWT library already rejects it; it is not counted as a discovered defect.

## Test and verification ledger at `b24d33e7` (historical snapshot)

| Coverage group | Test files / observable behavior | Result |
|---|---|---|
| P02 existing foundation | Discovery, destination validation, wire data, connection/delivery/event/operation storage and imports | 236 passed |
| P03 | `test_auth_gateway`, `test_auth_proofs`, `test_auth_storage`, `test_oauth_lifecycle`, `test_oauth_requests`, `test_oauth_signin`: cryptographic identity, replay/claim isolation, consent, lifecycle/race recovery | 61 passed |
| P04 | `test_pap_adapter`, `test_pap_worker`, `test_pap_grants`, `test_pap_controls`, `test_room_outbox`, `test_room_rest`: public SDK inputs, typed routing, account privacy, lifecycle, exact generated REST projection, uncertainty | 36 passed |
| P05 | `test_gateway_auth`, `test_gateway_conversations`, `test_gateway_recovery`, `test_gateway_views`, `test_gateway_operations`, `test_gateway_bridge`: authorization, persistent protocol endpoints, visibility and reply correlation | 43 passed |
| P06 | `test_operations_engine`, `test_operations_client`, `test_openapi_channel`, `test_mcp_channel`, `test_browser_channel`: trusted approvals, uncertain effects and advertised channels | 43 passed |
| P07 | `test_roundtrip` and `test_pap_service`: two companies, independent progress, lost response/reopen, complete inbound-to-outbound return path, shutdown and participant failure | 6 passed |
| Total PAP | Normal root pytest collection; no skipped/xfail PAP cases | **425 passed** |
| Repository Ruff / formatting | Entire checkout, including new examples/tests | Passed; 1,695 Python files formatted |
| Package Pyrefly | Entire `packages/band-pap/src`, warnings included | Zero diagnostics |
| Installed wheel | Rebuilt PAP wheel plus SDK wheel in isolated Python 3.12 environment, without agent-framework extras | Passed; reviewer A repeated smoke and matched installed modules to source |
| Broad repository Pyrefly | Minimal local environment | **139 errors**; optional frameworks and existing repository diagnostics remain. Earlier P02 run had 152 before auth/web dependencies were installed. No clean broad type claim. |
| Broad unit suite | Full non-live suite attempted in the same minimal environment | Collection stopped: **85 errors, 12 skipped**, including absent optional frameworks. Earlier P02 environment had 94 collection errors. No full local suite pass claimed. |
| CI on previous `453136b3` | Main CI lint, packaging and Python/OS matrix | Passed; this is earlier-commit evidence, not a pass for the final implementation |
| Python Core Coverage on `453136b3` | [Failed private-core checkout](https://github.com/band-ai/band-sdk-python/actions/runs/39004037826/job/117312720592) | Failed **before tests**: `Input required and not supplied: token`. Workflow expects `CORE_SDK_READ_KEY`; this run received none. Not a PAP test assertion or coverage-floor failure. |

## Remaining release gates

The runnable example and worked tutorial are now implemented; see the P07
increment below. P07 remains active for live acceptance.

- Run each updated head through the complete development CI matrix and the
  instrumented private-core coverage workflow. Its checkout requires an authorized
  credential/context; this integration does not change secret access or bypass
  coverage. Merely rerunning the same credential-less job cannot fix the checkout.
- Configure an authorized public PAP counterparty and actual issuer/client metadata,
  HTTPS ingress, signing keys and account test identities for live interoperability.
- Supply the worktree's required `.env.test` / agent configuration and verify actual
  Band participant delivery, roster/history visibility and removal across peers.
  Neither file exists here; deterministic transport tests do not stand in for that
  deployment evidence.
- Exercise host-specific account UI, approval policy, effect recovery, retention,
  credential/key rotation and deployment throughput. These application interfaces
  are deliberately required, not embedded business policy.

The draft PR remains draft for these gates. Remote test-matrix results below
supersede the earlier unverified CI status. No live privacy, external-counterparty
or exactly-once remote Band delivery claim is made.


## 2026-10-17 completion audit and CI correction at `1bd6771e` (historical snapshot)

The previous P07 completion label overstated the example/onboarding scope. Both
Sol reviewers (`pap_review_a`, `pap_review_b`) confirmed their P07 approval covered
offline code/tests and documentation, not a bundled runnable application or live
acceptance. The architecture document is now explicitly labeled as the original
design baseline, with this ledger as the current implementation record.

| Evidence | Result and meaning |
|---|---|
| Main CI at `b24d33e7` | Ubuntu/Windows Python 3.11–3.13 and framework test jobs, packaging and wheel checks passed. These results cover that commit, not the subsequent correction. |
| F06-03: [CI lint failure](https://github.com/band-ai/band-sdk-python/actions/runs/39006479452/job/117319891073) | Pinned Pyrefly 0.61.1 rejected iterating potentially null OpenAPI `servers`. Earlier local Pyrefly 1.4.0 missed this. Explicitly reject non-array server declarations before iteration or authenticated dispatch. |
| `test_malformed_servers_fail_before_authenticated_dispatch` | Nine regressions: null/object/string at document, path and operation levels. Each asserts a controlled error and no authenticated API request. All 13 OpenAPI tests pass. Both independent Sol reviewers approved this correction and ran the focused cases. |
| Current PAP suite after correction | **434 passed**, no skips/xfails. P06 now has 52 cases; other group counts above are unchanged. |
| Current repository Ruff / formatting | Passed across the checkout; 1,695 Python files already formatted. |
| Current package type check | **Zero errors with CI-pinned Pyrefly 0.61.1**, using the isolated Python 3.12 development environment. |
| [Core coverage at `b24d33e7`](https://github.com/band-ai/band-sdk-python/actions/runs/39006479506/job/117319891323) | Still fails before tests: private `band-sdk-core-core-v2.8.0` checkout reports `Input required and not supplied: token`. The workflow's `CORE_SDK_READ_KEY` is unavailable in this run. No coverage percentage or coverage pass can be claimed. |

Current source/test fingerprint: `97837ba9115b5a31b2833eb3d0224b39900ca0dec9d7214c9e7b4ec01671a1ae` (same algorithm above).
The prior installed-wheel evidence applies to `b24d33e7`; remote validation of the
correction is pending. Architecture, protocol contract, deployment/recovery guidance
and package documentation exist. A copy-and-run host composition tutorial and live
Band/PAP interoperability evidence are still missing.

## P07 runnable example and onboarding increment

Status: offline implementation, verification and both independent Sol reviews complete. Scope: a self-contained offline multi-peer example, a worked
host-factory tutorial, example acceptance tests and two independent Sol reviews.
Band remains the collaboration layer; deterministic company behavior belongs to
the example host. No existing SDK runtime files were changed.

| Item | Acceptance | State |
|---|---|---|
| P07a | Run without credentials, inference or test-helper imports; real PAP authentication, gateways, adapters and durable stores connect two distinct company peers in one simulated Band room | complete |
| P07b | Both full request/reply paths, explicit recipients, private records and trusted reply correlation are observable and tested | complete |
| P07c | Working ASGI factory with cleanup; minimal dependency command; tutorial maps example composition to required production host interfaces | complete |
| P07d | Two independent Sol reviews of code/tests/docs; record findings, fixes and reruns | complete — A and B approved |

Live PAP/Band acceptance and private-core coverage remain separate release gates.


The example lives in `examples/pap/demo.py`, `host.py` and `simulation.py`, with
its worked factory tutorial in `examples/pap/tutorial.md`. One personal client
and owner uses two per-company sessions/proxies in one simulated room. Downstream
company identities are pairwise. Real gateways, both adapters, JWT/DPoP verification,
SQLite, private-record access and reply correlation run; discovery/token issuance
are deliberately seeded, and transport is in process. No actual company endpoint,
model or live Band service is contacted.

### P07 increment failure and review ledger

| ID | Failure / reviewer | Test and disposition |
|---|---|---|
| F07-02 | A: an accepted gateway handoff retained authority after its source session was revoked. Initial route checks validated recipients but not the live session. | `test_revoked_source_cannot_flush_an_accepted_handoff`: every route now rechecks its owning live session before send. Passing. |
| F07-03 | A/B: checking only the downstream company session still allowed reading a prior reply after its central source session was revoked. Root's regression failed before the second authority check was added. | `test_revoked_session_cannot_read_previously_delivered_record`, source and company variants: require both source and remote authority for outbound/private-record access. Both passing. |
| F07-04 | B: successful demo output hid two uncertain outbox records because the reply handler parsed state events as messages. | `test_success_leaves_no_unsettled_room_deliveries`: handle authenticated state events explicitly; the demo refuses success with pending/uncertain work. Passing with zero ready and zero uncertain deliveries. |
| F07-05 | A: `asyncio.gather` allowed a sibling to continue after a peer failed, racing database/client cleanup. | `test_failed_peer_is_joined_before_resources_close`, request and exchange variants: TaskGroups cancel and join siblings before teardown. Both passing. Startup and repeated-lifespan failure tests also verify resource removal. |
| E07-01 | First root example-test run failed on `host.room.id`, an interface mismatch while source/tests were being composed. | Use the explicit demo room constant for record authorization; corrected. No protocol/runtime defect claimed. |
| E07-02 | Review found the tutorial overstated calls through the outer factory; the driver uses its composed gateways directly. | Corrected the explanation and added `test_factory_serves_demo_and_keeps_protocol_routes_authenticated` for `/demo/run`, repeated calls and unauthenticated PAP rejection. Passing. |

### Current verification

| Check | Evidence |
|---|---|
| PAP suite | **448 passed**, no skips/xfails, including **14 example acceptance cases** in `test_demo.py`. |
| Example scenarios | Both company reply chains, one personal owner with separate company sessions/threads, explicit recipients, denied cross-peer records, no private-note output, no socket connections, CLI subprocess from copied example files. |
| Lifecycle | Fresh state on repeated factory lifespans; startup and runtime failure cleanup; failed sibling joined before resources close; zero unfinished outbox deliveries. |
| Minimal setup | Documented `uv run --no-project --with . --with ./packages/band-pap python -m examples.pap.demo` succeeds using runtime dependencies. |
| Installed wheels | Rebuilt PAP wheel plus SDK wheel in isolated consumer; copied only example files outside checkout. Demo passes without pytest or agent-framework extras; both replies and cleanup verified. CI packaging now runs the same demo. |
| Static checks | Repository Ruff and formatting pass (1,700 Python files). CI-pinned Pyrefly 0.61.1 checks package and all three new example modules with zero errors. |
| Prior-head CI `1bd6771e` | Lint correction passed remotely; packaging and other test lanes passed. [Windows Python 3.11](https://github.com/band-ai/band-sdk-python/actions/runs/39048330771/job/117441491434) failed two existing ACP process-closure cases on 2-second cleanup budgets (8,809 passed, 811 skipped). This is not a PAP assertion failure; those SDK files were not changed. New-head CI remains pending. |
| Core coverage | [Prior-head job](https://github.com/band-ai/band-sdk-python/actions/runs/39048330791/job/117441491536) remains failed; private-core checkout credential is still an outstanding workflow prerequisite. No coverage percentage or pass claimed. |

Both independent Sol reviewers examined the example, test suite, tutorial and
installed-wheel CI change. Their initial findings were reproduced and fixed;
both approved after re-review of the final source/tests/tutorial and CI change. Operations authored
the example and does not count as its own independent reviewer. Root authored the
acceptance tests and documentation. Existing Band SDK runtime internals are unchanged.

Review snapshot SHA-256: `74b31ed152ba55ec9d41992e81887464c4244b4a67082731a164682211736748`. This increment includes sorted
`packages/band-pap/src/**/*.py`, `tests/pap/**/*.py` and `examples/pap/*.py`,
each relative path and content NUL-delimited. Documentation edits do not change it.


| Independent reviewer | Final verdict and independently repeated evidence |
|---|---|
| A (`pap_review_a`, `gpt-6.1-sol`) | Approved offline increment. Repeated 14 demo tests and the 448-case suite; pinned example type check, Ruff/format and installed-wheel demo pass. Reviewed both authority checks, state projection and failure cleanup. |
| B (`pap_review_b`, `gpt-6.1-sol`) | Approved offline increment. Repeated all 448 tests including 14 demo cases, minimal runtime-dependency command, Ruff/format; matched the expanded snapshot fingerprint. Reviewed same-owner session isolation, output redaction, tutorial and ledger. |

Local workflow syntax (`actionlint`) and documentation relative-link checks also
pass. These reviews close the standalone example/tutorial work, not the remaining
live acceptance, platform visibility or private-core coverage gates.
