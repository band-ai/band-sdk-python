# Specification improvement ledger

Requested October 11, 2026: improve the specifications for independent
implementations. Baseline: `33f5b266ef6bf05a3a0677d4f3cba033c570af52`.
This repository is local only, with no configured remote.

This ledger records the completed rc.2 improvement pass. The following
[contract/Band integration pass](band-integration.md#evidence-and-remaining-implementation-work)
records rc.3 and its current checks separately; counts below describe the rc.2 snapshot.

| Item | Intended result | Disposition |
|---|---|---|
| S1 | Specify and exercise every documented compiler query shape | As expected: seven selector schemas and a fixture with documented fields, deadlines, scopes and unknown-name errors. Observed RED (exit 1), then all 68 cases / 232 requests pass (exit 0); [evidence](../agent-runtime/evidence/spec-improvements.json) |
| S2 | Make compiler error conformance independent of diagnostic prose | As expected: diagnostic JSON may vary; code, line and object shape stay exact. An altered-diagnostic adapter probe failed before the fix and passes afterward; four checker regressions and the full reference suite pass. [Evidence](../agent-runtime/evidence/spec-improvements.json) |
| S3 | Detect stale generated schemas and requirements without rewriting them during validation | As expected: both schemas and the requirements catalog are compared with their generators; revision disagreement fails. Isolated stale bundles were accepted before the fix and are rejected afterward without rewriting. Seven checker regressions and all portable cases pass; [evidence](../agent-runtime/evidence/spec-improvements.json) |
| S4 | Make fixture coverage and remaining acceptance obligations inspectable for every requirement | As expected: a checked [index](conformance/coverage.json) lists all 94 requirements, with the presence or absence of portable examples explicit. Every acceptance obligation remains explicit; no qualification is inferred. Observed RED/GREEN and 12 checker regressions pass; [evidence](../agent-runtime/evidence/spec-improvements.json) |
| S5 | Clarify profile composition, interoperability rules, capacity failures and recovery boundaries | As expected: revision 1.0.0-rc.2 adds a [worked session](walkthrough.md), precise initialization/timer/byte rules, recovery commit boundaries, profile composition and capacity qualification. 77 cases / 263 requests, 12 checker regressions, nine real HTTP schema checks and 53 local links/anchors pass. All 67 original fixture files and existing runtime wire schemas remain unchanged. [Evidence](../agent-runtime/evidence/spec-improvements.json) |
| S6 | Enforce the stated JSON domain on independent adapter output | As expected: strict UTF-8 and protocol JSON are enforced before comparison. Eight malformed output forms were accepted in the observed RED; all are rejected in GREEN while valid JSON and exact response counts remain supported. All 14 checker regressions and 77 cases / 263 requests pass; [evidence](../agent-runtime/evidence/spec-improvements.json) |

Specification changes do not complete the open production work in the
[production ledger](../PRODUCTION.md). Independent implementation qualification,
live model quality, workload qualification, publication and deployment retain
their separate acceptance boundaries.

The revision retains all 94 requirement IDs. `ROOM-004` now states its durable
room-event condition directly; `NET-001` through `NET-003` also carry the Band
profile tag because their existing shared transport rules apply there. The
rc.2 index links portable examples to 32 requirements and leaves 62
without portable examples. Neither number is a requirement-acceptance count.

Final combined validation: 14 checker regressions and 77 portable cases / 263
requests pass. The changes are local; they are not a production qualification,
external publication, merge or deployment.
