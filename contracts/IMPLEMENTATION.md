# Contract implementation and expectation ledger

**Evidence scope:** this retained record describes the named historical source and environment. Counts, installed-skill state, publication state, and host qualification below are dated observations, not validation of this SDK build. Historical evidence artifacts remain in the originating audit archive and are named here without implying that private logs ship in the SDK. Current package usage is documented in [the SDK contract guide](../docs/contracts.md).

The reference implementation below is complete locally. The subsequent request to make it production ready is active in the [production acceptance ledger](PRODUCTION.md); production readiness is not yet established.

Requested by Eric on October 10, 2026. The local reference includes an Elixir room authority and a separate Python remote program/LLM host; since October 13 that host is an optional experiment, not a room participation requirement. The original reference repository had no configured remote. The three discovery header alternatives remain available; no final header choice has been made.

| Requested item | Disposition | Evidence |
|---|---|---|
| Deadlines for all or selected participants | Implemented: all/any, all roles or a selected set, separate duties, exact boundary behavior | [Runtime tests](contract-lab/test), [corpus](contract-lab/contracts) |
| Multiparticipant unit tests and a folder of contracts | 14 contracts; 158 compiler/runtime/transport/recovery/authoring/remote-gate tests pass | [Guide and corpus index](contract-lab/README.md), passing run (`contract-lab/evidence/agent-gate-fresh-vm-green.log`, historical artifact) |
| Preceding triple-double-quoted documentation | Implemented with Elixir literal indentation/newline behavior and no executable interpolation | [Compiler tests](contract-lab/test), initial RED (`contract-lab/evidence/compiler-red.log`, historical artifact) |
| Documentation returned as contract metadata by query APIs | Implemented in full, role, type, field, state, duty, outcome and scope queries; CLI output preserves it | [Compiler tests](contract-lab/test), query evidence (`contract-lab/evidence/authoring-wrapper-query.log`, historical artifact) |
| Contract generation from chat history | Creation skill, source-to-rule derivation, compiled example and positive/negative scenarios implemented | [Source chat](contract-lab/histories/review-chat.md), [derivation](contract-lab/histories/review-derivation.md), [scenarios](contract-lab/scenarios) |
| Master skill with creation and iteration sub-skills | Four skills written, validated and installed; later-requirement example preserves the original contract | [Master](skills/multi-agent-contracts/SKILL.md), [iteration example](contract-lab/histories/review-followup.md), installation checks (`contract-lab/evidence/skills-validation.json`, historical artifact) |
| Refined assignment protocol | Implemented: offer acceptance/refusal, exact references, independent review, requester acceptance, questions, bounded revisions, cancellation acknowledgement and effects_unknown | [Assignment contract](contract-lab/contracts/assignment.band), [runtime tests](contract-lab/test) |
| Find the prior design discussion | Technical conclusions mapped to the implementation; the internal meeting record is not published | Not published |
| Program replacement with room routing and supervision | Implemented: supervised callbacks, accepted-event routing, outgoing contract admission, actual code generation/validation, typed control events and runnable room bridge | [Room example](contract-lab/examples/room_recovery.exs), [command sidecar](contract-lab/lib/contract_lab/command_sidecar.ex), [replacement skill](skills/replace-agent-with-program/SKILL.md) |
| Event journal, snapshots, repair and replay recovery | Implemented; crash, timeout, restart, corruption, compatibility, repair budget and effect ambiguity tested | [Journal tests](contract-lab/test/journal_test.exs), [controller tests](contract-lab/test/replacement_test.exs), recorded recovery (`contract-lab/evidence/room-recovery-demo.log`, historical artifact) |
| LLM only for exceptions and explicit activation | Routing implemented; demo counts 0 ordinary calls and 1 crash repair, plus a separate explicit activation; live LLM provider and token billing not exercised | Recorded recovery (`contract-lab/evidence/room-recovery-demo.log`, historical artifact), adapter checks (`contract-lab/evidence/command-sidecar-checks.json`, historical artifact) |

Current publication status: local only. No changes have been merged, deployed, or sent to colleagues. The production Band service and the earlier Python prototype remain outside this implementation.

The four skills are installed under `~/.codex/skills` as links to the versioned sources in this repository. Their source and installed validators pass, references resolve, and the installed helper replays an asserted scenario successfully. The behavioral exercise was performed by the authoring agent; no independent subagent evaluation is claimed.

Validation on October 10: 158 contract-lab tests and 48 Python remote-runtime tests pass. The October 13 counts (343 core and 84 header tests) are in the last section of this ledger. Elixir compilation treats warnings as errors, formatting passes, all 14 contracts compile, and a deliberately wrong scenario expectation produces a nonzero CLI exit. The Python package builds and the remote HTTP/SQLite demo passes. Earlier unchanged components retain their recorded 74 discovery tests and 25 room-simulator tests. Feature changes have observed failing then passing checks; documentation and skill files have suitable structure, link and execution checks.

Integration limits: the demos restart participant controllers while their in-memory room authority remains running. Generated code is trusted host code, not sandboxed. The earlier Elixir journal has one owner per path: an exclusive flock(2) on `<journal>.owner`, held through the util-linux `flock` command, which is released when the owner exits; the remote Python host uses one local OS owner and one SQLite journal/snapshot database. Production room persistence, cross-machine transport, and compaction remain separate work. Uncertain non-idempotent effects are held for reconciliation. Demonstrated generation writes real code but uses a scripted provider; a live provider can be connected through the command adapter. No live-model quality, token billing, or production deployment is claimed.

The October 10 open design decisions were settled on October 13. The discovery header is YAML frontmatter. Engine `0.2.0-lab.1` has no generic idle timer ([defaults](spec/defaults-v2.md)), every contract declares its whole-run clock, and the earlier engine with its inactivity-reset policies is being removed.

The final audit (`evidence/implementation-final-audit.json`, historical artifact) records the verified source hashes, executed checks, local link check, and scope limits.

## October 10 follow-up: remote hybrid agent

The intermediary runs beside the remote agent, outside the room. The room retains final admission authority. This follow-up extends the earlier local experiment.

| Requested item | Current disposition | Evidence |
|---|---|---|
| Agent-side contract checks, including out-of-turn replies | Implemented: separate checker uses the same Elixir semantics; invalid program output triggers LLM takeover before publication; room retains authority | [Fresh-process checks](contract-lab/test/agent_gate_test.exs), [handoff tests](../tests/contracts/runtime) |
| Python snapshot library in the journal's SQLite database | Implemented: SDK stages a checkpoint; room-accepted state, journal entry and snapshot commit atomically in SQLite | [SDK](../src/remote_agent/sdk.py), [storage tests](../tests/contracts/runtime) |
| Per-turn or ongoing LLM takeover | Implemented: explicit delegation scope, durable routing, restart recovery, verified return to program | [Controller](../src/remote_agent/controller.py), passing tests (`agent-runtime/evidence/transport-green.log`, historical artifact) |
| Communication continues while code is repaired | Implemented: LLM completes a further turn while repair is waiting; candidate replays history, passes quality checks, and installs at a turn boundary | [Executable demo](agent-runtime/examples/hybrid_demo.py), recorded result (`agent-runtime/evidence/transport-demo.log`, historical artifact) |
| Control messages can wake the LLM | Implemented: authenticated HTTP endpoint, actor-specific wake/takeover/repair/resume/retry grants, durable duplicate handling | [Endpoint tests](../tests/contracts/runtime), [endpoint](../src/remote_agent/endpoint.py) |
| Skill/prompt covers failure handling and limits of automation | Implemented and installed: full/partial/LLM-only classification, semantic quality requirements, all runtime failure categories, and bounded recovery | [Replacement skill](skills/replace-agent-with-program/SKILL.md), [sidecar prompt](../src/remote_agent/sidecar_prompt.md), skill validation (`agent-runtime/evidence/skills-validation.json`, historical artifact) |

The remote demo completes eight turns with zero calls for its first ordinary case and seven scripted calls overall (four responses, one repair, two explicit wakes). It demonstrates the mechanism, not general language quality or measured token savings. A full or partial replacement must pass task-specific semantic checks; arbitrary free-form English remains delegated. The reference HTTP endpoint binds to loopback and needs a host transport/TLS proxy for actual remote access.

The [remote runtime guide](agent-runtime/README.md) gives the architecture, SDK, endpoint schema, provider bridge, recovery invariants, limits, and runnable commands. The follow-up audit (`agent-runtime/evidence/final-audit.json`, historical artifact) records source identities, executed checks, and local-only scope.

## October 13 follow-up: compact timeout scopes

Eric requested the agreed terse syntax in the presentation and supporting
language. Message deadlines retain parentheses and combine a transition with
its duty. State and whole-run caps use the same `@duration` notation.

| Requested item | Disposition | Evidence |
|---|---|---|
| Timed message rules and group replies | Implemented locally; 6 focused tests, observed 5 failures then green | [Message tests](contract-lab/test/inline_deadline_test.exs) |
| State entry timeouts | Implemented locally; 8 focused tests, observed 7 failures then green | [State tests](contract-lab/test/state_timeout_test.exs) |
| Whole-run timeouts and named outcomes | Implemented locally; 7 focused tests, observed 6 failures then green | [Contract tests](contract-lab/test/contract_timeout_test.exs) |
| Guides, examples and installed skills | Updated to prefer the combined form; independent `due` retained | [Timeout specification](spec/timeouts-v2.md), [scoped example](contract-lab/contracts/v2/scoped-review.band) |
| Narrated presentation and existing hosted series | Published and live-checked: all three parts, updated narration and timeout scopes | Presentations (privately hosted; not linked), delivery ledger (`../contract-inline-deadlines-20261013/LEDGER.md`, historical artifact) |
| Band integration or deployment | Not performed | Existing production boundary unchanged |

The full reference suite passes 280 tests after the three runtime commits.
The repository remains local-only with no configured remote. New phase and
contract clock metadata uses opt-in `band-contract/timeout/1`; old files keep
their prior compiled shapes and semantics.

Publication receipt: version `01M4SEQCAHBTGWZARATTZMW8XK`, same approved URL and audience. The three HTML decks match their tested bytes; the contents page differs only by the hosting service’s verified preview metadata. Runtime source and presentation citations are pinned to `d42ce0ecf7596146d1dc7677fbc8dc4939925629`; this receipt changes documentation only.

## October 13 follow-up: room core and optional clients

Eric clarified that room enforcement is the product core. Any authorized agent
must be able to discover its rules and participate. The client journal, local
checker, program replacement, snapshots and repair are optional experiments.

| Requested item | Disposition | Evidence |
|---|---|---|
| Lowercase `as`, `each`, `event` and other body keywords | Implemented locally in `8aeb5ba`; six expected failures then eight focused tests pass | [Grammar](spec/lowercase-v2.md), [tests](contract-lab/test/lowercase_keywords_test.exs) |
| Existing behavior and source identity | Uppercase aliases retain behavior; exact source hashes change; legacy grammar unchanged | 288 core tests; 55 new-profile assertions; original 78 compatibility cases and 33 Band wire examples pass |
| Separate the room core from the client experiment | Updated specification, architecture, examples and four authoring skills | [Boundary and requirements](spec/room-clients-v2.md) |
| Ordinary client discovery and correction | Six local checks pass without a client runtime; public room catalog and rejection pointers are specified host integration work | [Direct client example](contract-lab/examples/direct_agent.exs) |
| Syntax highlighting and presentation separation | Published and verified; three core parts plus an optional experimental runner part | Delivery ledger (`../contract-room-first-20261013/LEDGER.md`, historical artifact) |
| Band rollout or deployment | Not performed | New public discovery integration still needs implementation and qualification |

The original reference repository had no configured remote. This room-only revision preceded the explicit
clock-renewal work recorded below.

## Multi-agent skill coordination — October 13, 2026

The authoring artifact now combines retained role/state/message guidance with its
enforceable contract. Lowercase sigils and multiline author comments are implemented.
Runtime rosters bind named agent lanes at creation; explicit collections retain
per-member results and continue after failure, timeout or a trusted offline grace.
Explicit `@self`, `@state` and `@contract` renewal uses precise timer targets and
immutable maximums. See the [design comparisons](spec/multi-agent-skills.md),
[collection semantics](spec/coordination.md), and [renewal
semantics](spec/renewable-timeouts.md).

Observed RED/GREEN evidence is in
`~/tv/verification/multi-agent-skill-evolution-20261013`. The final local suite
passed 315 tests, including portable restart, privacy, time boundaries and a regression
ensuring hard-only maximums cannot shorten initial response windows. Eight current
examples compile and return role queries. The coordination suite passes 17 independent
assertions across 28 adapter requests and 111 schema definitions; the earlier v2 suite
passes 55 assertions / 110 requests, and the immutable original suite remains 78 cases /
283 requests plus 33 Band wire examples. Four installed authoring skills pass validation
and 459 Markdown file links resolve.

These are local reference changes. No Band rollout, merge or downstream qualification is
claimed. The narrated presentation update (privately hosted; not linked) is
published and verified. Its 60 slides cover three core parts and a separate optional
client experiment. All four decks embed the approved Pocket Alba narration as MP3, work
offline, and retain copyable highlighted examples.

Published version `01M4T0AJEX2WDHFSQ0Z908WHE9` cites specification/source commit
`ac66c9817fdcf9615953c31fc7e81139d1f92675`. Final validation passed 981 deck browser
checks, 112 highlighting checks, 31 contents-page checks and 70 hosted checks. The four
served decks match the tested bytes exactly; the contents page permits only the exact
recorded host preview-metadata insertion.

See the request-by-request delivery
ledger (`../multi-agent-skill-evolution-20261013/LEDGER.md`, historical artifact) and machine-readable
receipt (`../multi-agent-skill-evolution-20261013/delivery.json`, historical artifact). This publication
receipt changes documentation only; runtime, specifications, examples and installed
skill sources remain at the tested source pin.

## Ordinary prompt strings — October 13, 2026

Ordinary `"short guidance"` and `"""` multiline strings are the default authoring
forms. The compiler attaches their decoded text to the next item as `meta.doc`
with its source line, and role queries and agent views retain it. This guidance
carries business rules that the room cannot directly evaluate; it does not
become an admission predicate or grant authority. Sigils remain available when
their special behavior is needed.

Six focused tests went from five observed failures to passing. The full suite
passes 321 tests. Eight examples compile and query; the four converted examples
have identical compiled metadata and behavior, apart from their exact source
hashes. Portable v2 checks pass 62 assertions / 116 requests, coordination passes
17 / 28, and legacy compatibility remains 78 cases / 283 requests plus 33 Band
wire examples. Four installed authoring skills validate and 258 Markdown file
links resolve. Compilation, formatting and whitespace checks pass.

These changes are local reference work in a repository with no configured
remote. The subsequent grapheme explanation, infinite-window validation,
participant-type choice and narrated presentation are delivered in the
clarification receipt below.
The current request ledger (`../plain-prompt-metadata-20261013/LEDGER.md`, historical artifact) records
each item and its evidence.

## Selected participant types and clock validation — October 13, 2026

Eric selected option C: declare reusable participant guidance and attributes with
`type reviewer: agent`, then bind a runtime collection using
`reviewers: list(reviewer)`. Creation validates each member's attributes. Role
views retain the type prompt, roster prompt and individual assignment. Typed
rosters preserve collection behavior and recovery, and use explicit participant,
roster and state profiles so earlier readers reject unsupported data.

The compiler also rejects `contract @infinity max @4h => expired`: a finite
maximum cannot be at least an infinite initial window. A fixed four-hour cap
is simply `contract @4h => expired`. This correction has its own commit and
observed failing/passing checks.

Participant-type tests observed six failures before implementation; all eight
tests now pass, including validation, view metadata, reuse, bounded attributes,
collection behavior and portable recovery. The full suite passes 330 tests.
Portable coordination passes 27 assertions / 35 requests against 115 schema
definitions, including older-reader rejection and the infinite-window error.
Earlier v2 compatibility remains 62 assertions / 116 requests; the original
suite remains 78 cases / 283 requests and 33 Band wire examples. Eight examples
compile and return role/type queries. The four installed skills prefer the
selected typed participant syntax.

The existing narrated series includes these choices, ordinary prompt strings,
and the distinction between UTF-8 bytes and Unicode graphemes.
Publication evidence is tracked in the current ledger (`../plain-prompt-metadata-20261013/LEDGER.md`, historical artifact).

The current guide and presentation teach the agreed participant and collection
behavior directly. Settled design comparisons have been removed from learner-facing
content; their prior discussion remains in version history.

## Published clarification receipt — October 13, 2026

The narrated presentation (privately hosted; not linked) now teaches the
agreed participant and collection behavior directly, without settled option
comparisons. It uses ordinary prompt strings, explains UTF-8 bytes versus
graphemes with accepted and rejected examples, and identifies an infinite
window under a finite maximum as a compile error.

Published version `01M4T4ZQ8GN2Y3PR2W9GMMW5Q5` contains 61 slides across
three core parts and one optional client experiment. All clips use the approved
Pocket Alba voice. Final validation passes 993 deck browser checks,
122 highlighting checks, 31 contents checks and 70 hosted checks. The
served decks match the tested bytes; the contents page allows only the exact
recorded hosting preview-metadata insertion. Source citations are pinned to
`3e603f986ec5e13fc3bf853a71d0cd317a62aaa2`.

The runtime still passes 330 tests and all portable compatibility suites. This
receipt changes documentation only. See the complete request ledger (`../plain-prompt-metadata-20261013/LEDGER.md`, historical artifact)
and delivery receipt (`../plain-prompt-metadata-20261013/delivery.json`, historical artifact) for evidence
and the explicit local-reference scope.

## Contribution outcomes and regex authoring — October 13, 2026

Current partial collections declare `room.contributions/1` and their successful
reply shape. An authenticated member can stop its own contribution; the trusted
host can report terminal execution failure without a message from that agent.
Reports identify the exact session, collection round and member. Causes retain
their original type and optional evidence reference. Recoverable errors leave
the contribution pending. The new feature uses contribution/1, collection/2 and
state/5, while earlier source identities and recovery profiles remain supported.
The [normative specification](spec/contribution-outcomes.md), portable schema,
review-lanes example and four installed authoring skills are updated.

Regex examples now use `~r`; ordinary agent guidance keeps plain quotes. The
existing quoted pattern grammar remains compatible. Its focused validation
passes 16 tests; this authoring correction is documentation-only.

The contribution feature first produced 12 expected failing scenarios before
implementation. Thirteen focused scenarios now cover producer authority, scope,
retries, races, redaction, recovery and capacity. An unused-extension projection
case also observed failure before its correction. The full engine passes 343
tests, header parsers pass 84, and portable coordination passes 40 assertions /
46 requests against 122 schema definitions. Earlier v2 checks remain 62 / 116;
original compatibility remains 78 cases / 283 requests and 33 Band wire examples.

The request ledger (`../contribution-lifecycle-20261013/LEDGER.md`, historical artifact) tracks the
presentation update and publication receipt. This remains a local reference
implementation; Band host integration and deployment are separate work.

## Published contribution outcomes receipt — October 13, 2026

The narrated presentation (privately hosted; not linked) now teaches contribution
outcomes separately from successful business replies. It shows an authenticated
member stopping its own contribution and a trusted host reporting terminal
execution failure with the exact session, collection entry and member. Causes
retain their source type and evidence reference; recoverable failures remain
pending. The regex examples use `~r`, while prose keeps ordinary quotes.

Published version `01M4TEE7ZJ128VA0PK9R1TCC3Y` contains 63 narrated slides
across three core parts and one optional client experiment. All clips use the
approved Pocket Alba voice. Final verification passes 1017 deck browser
checks, 152 highlighting checks, 31 contents-page checks and 70 hosted
checks. The served decks match the tested files. The contents page permits only
the exact recorded hosting preview-metadata insertion. The 127 source
references are pinned to `9fc3680e6b0c7d20a39272e224800e387922984b`.

The runtime passes 343 core tests and 84 header tests; portable coordination
passes 40 assertions / 46 requests against 122 schema definitions. Earlier v2
and original compatibility suites pass. This receipt changes documentation only.
See the completed request ledger (`../contribution-lifecycle-20261013/LEDGER.md`, historical artifact)
and delivery receipt (`../contribution-lifecycle-20261013/delivery.json`, historical artifact).
Band host integration and deployment remain separate work.

## Spec integration evidence at 7fc1435 — October 13, 2026

These counts were measured on spec integration commit `7fc1435`. That commit
merges the October 13 syntax, clock and mention changes with the duty-first
tie order and fail-closed host evidence. They replace the October 13 figures
above: 343 engine tests, coordination 40 / 46 and v2 62 / 116.

| Check | Result at `7fc1435` |
|---|---|
| Engine (`contract-lab`, `mix test`) | 393 tests, 0 failures |
| Header parsers (`header-spikes`) | 84 tests, 0 failures |
| v2 checks (`check_v2`) | Passed: 84 assertions, 152 adapter requests |
| Portable coordination (`check_coordination`) | Passed: 58 assertions, 110 requests, 122 schema definitions, 0 pending |
| Original compatibility (`check_spec`) | Passed: 94 requirements, 78 cases, 283 requests, 33 Band wire examples |
| Contract examples in the docs, skills and `.band` files | 41 compiled: 39 ok, 0 fail, 2 labelled excerpts |
| Spec site | 303 internal links, 0 broken |

The coordination check `tie-duty-before-collection` now passes, so no check is
pending. Quoted regex literals are rejected with a syntax error at this commit,
so the October 13 entry's "remains compatible" sentence no longer holds; see
[safe patterns](spec/safe-patterns.md#raw-pattern-literals). This is still a
local reference implementation; Band host integration and deployment remain
separate work.

## Spec integration evidence at 9157dcb — October 14, 2026

These counts were measured on spec integration commit `9157dcb`. It adds two
changes to `7fc1435`: private, unpredictable proposal IDs derived with a
host-private key, and the versioned `band-contract/carrier/1` map for envelope
fields that a transport supplies outside the JSON body. They replace the
`7fc1435` figures above.

| Check | Result at `9157dcb` |
|---|---|
| Engine (`contract-lab`, `mix test`) | 427 tests, 0 failures |
| Header parsers (`header-spikes`) | 84 tests, 0 failures |
| Remote runtime (`agent-runtime`) | 177 tests, 0 failures |
| v2 checks (`check_v2`) | Passed: 85 assertions, 158 adapter requests |
| Portable coordination (`check_coordination`) | Passed: 58 assertions, 110 requests, 128 schema definitions, 0 pending |
| Original compatibility (`check_spec`) | Passed: 94 requirements, 136 cases, 346 requests, 33 Band wire examples |
| Spec tool unit tests | 28 tests, OK |
| Carrier normalization examples (`check_carrier_examples`) | 8 of 8 commands passed |
| Contract examples in the docs, skills and `.band` files | 41 compiled: 39 ok, 0 fail, 2 labelled excerpts |
| Spec site | 326 internal links, 0 broken |

This is still a local reference implementation; Band host integration and
deployment remain separate work.
