# Generating another implementation

Use the specification and machine-readable artifacts as the input, rather than translating reference source line by line. The intended output is an independently understandable implementation with the same observable decisions and recovery guarantees.

Read [one review, explained from scratch](start-here-v2.md) and the
[reviewed profile](profile-v2.md) first, then the [worked session](walkthrough.md)
for the distinction between a current state, a historical duplicate receipt, a
recorded callback and a durable effect. Then implement against the schemas and algorithms, including their
failure order and byte-count boundaries.

## Input bundle

Supply the generator with this directory in full: the profile/version registry, the [requirements](requirements.json) and the reviewed profile's [v2 requirements](requirements-v2.json), DSL grammar and metadata rules, normalized representation, canonical encoding, reducer algorithms, [schemas](schemas) (including the [v2](schemas/protocol-v2.schema.json), [coordination](schemas/protocol-coordination.schema.json), [participant](schemas/protocol-participants.schema.json) and [contribution](schemas/protocol-contributions.schema.json) bundles), positive/negative [conformance](conformance) vectors, the optional remote invocation/effect lifecycle, transports and operations. Include only the relevant reference contracts/examples and a bounded list of known open qualification items from [PRODUCTION.md](../PRODUCTION.md). Do not pass credentials or private operational journals as training/example material.

The requirements file is the traceability source. Each requirement has a stable ID, normative statement, profile applicability, specification location and acceptance obligation. Build an implementation ledger with one disposition per applicable requirement. If a language/runtime cannot meet a requirement, report that before substituting weaker semantics. Do not silently change string length units, deadline boundaries, duplicate order, reference identity, actor binding, output-record timing or unknown-effect handling.

Use the [coverage index](conformance/coverage.json) to find existing portable
examples and requirements without them. It is an example inventory, not a
completion ledger. For each requirement record applicability, implemented
behavior, exact tested artifact, command/result, missing acceptance checks and
review disposition. A requirement with several acceptance clauses stays open
until all applicable clauses have evidence; a linked happy-path example cannot
close its fault or recovery obligations.

## Required implementation boundaries

Keep these interfaces separable even when compiled into one executable:

1. **Loader/compiler:** bounded discovery and immutable source loading; compiled JSON-compatible IR; documented query views; deterministic error codes and identities.
2. **Pure reducer:** initialization, tick, admission, view and state import/export. Actor and time are explicit trusted arguments. It performs no network/database/model effects.
3. **Durable authority:** serialization/transactions, persisted deadlines/receipts, clock policy, authenticated membership and effect publication.
4. **Optional experimental client runtime:** invocation journal, state/snapshots/outbox, program ABI/sandbox, local checker, model/repair controls and recovery. Implement only when selecting that profile; none is required for a general agent client or room authority.
5. **Transport/operations:** authenticated adapters, lifecycle, build/configuration, backup/migration, metrics and release qualification.

The compiler's normalized IR is not executable code. The pure engine cannot authenticate a supplied actor. A database journal cannot sandbox a program. A local checker cannot grant room acceptance. A schema-valid model answer cannot establish task quality. Preserve these responsibilities explicitly in generated code and tests.

For the new profile, [room/client separation](room-clients-v2.md) is the current
product boundary. Core qualification must use an independent ordinary client
that discovers the rules, handles a useful rejection, fetches its current view
and submits a valid message without the experimental runner. Invocation,
program-isolation, model-routing and repair checks below apply only to the
optional runtime. Durable acceptance, authorization, privacy, deadlines and
receipt recovery remain room-host requirements.

## Implementation plan and test tools

The planned implementation order is the Elixir reference, then Python, TypeScript
and Rust. Each must stay compatible with the others and embeddable in a host, and
none is claimed until it passes the same portable vectors, schemas and semantic
assertions as the reference. Only the Elixir reference exists today.

The repository's room simulator (`room-sim/`, see its README) is a lightweight
in-memory room for developing participants and engine adapters without Band. It
is non-normative test tooling: it implements room transport behavior only, and
passing its tests does not qualify a Band deployment.

## Conformance adapter

An independent implementation should provide a JSON-lines executable accepting one request per line and producing one response per line. Standard output contains protocol JSON only; diagnostics go to standard error. Requests and responses use the protocol canonical JSON domain. The driver bounds bytes/time and checks actual exit status.

Required operations for compiler/engine conformance are `compile`, `query`, `new`, `step`, `tick`, `view`, `checkpoint`, and `restore`; a request names its operation and supplies the exact input. Contracts that use the reviewed profile's extensions add `host_signal` ([host signals](host-signals.md)) and `stop_contribution` / `fail_contribution` ([contribution outcomes](contribution-outcomes.md)). The fixture container declares the profile. Additional operations cover local preflight, room events, wire decoding and canonicalization. The adapter keeps no hidden time or actor state. Session handles used by a test are harness-only identifiers, not public authorization. See the [conformance interface](conformance/README.md) and manifest for exact request/response shapes.

For every vector, compare canonical JSON outputs, decision codes/order, identities, state versions, recipient ordering, metadata and portable checkpoint data. Compiler error `detail` is diagnostic and may vary; its `code`, `line` and error shape remain exact. Trace vectors include accepted and rejected messages at exact time boundaries, duplicates after termination, conflicting IDs, out-of-turn replies, each/all/any behavior, reference mismatches, quotas, visibility, overflow and recovery. Negative parser/wire fixtures must return their specified error response; the adapter still exits successfully after processing the whole stream. A rejected compiler or wire input is not a failure of the conformance process.

Vectors and semantic assertions compare these error codes exactly. Each row
names where the rule is specified; a code listed here without a rule in that
document is still exact for conformance.

| Boundary | Codes | Rule |
|---|---|---|
| Wire decoding and carrier normalization | `json`, `duplicate_key`, `unsupported_number`, `envelope`, `transport_fields`, `transport_conflict`, `message_size` | [Carrier map profile](transport.md#carrier-map-profile) |
| Source loading (`compile` of a file source) | Returned as a bare code, not a compiler error object: `input` for a malformed or unknown source envelope or format; `source_size` for a file or body over its byte limit; `invalid_utf8`; `file_digest_mismatch` when the envelope hash is not the hash of the file bytes; `digest_mismatch` when a standard package body differs from its pinned reference; `frontmatter_required` for the retired native format | [File source](file-identity.md#file-source). The loader checks the envelope (`input`), size (`source_size`), UTF-8 (`invalid_utf8`) and the envelope hash (`file_digest_mismatch`) before the format and the header, so a native source with a wrong hash is `file_digest_mismatch`, not `frontmatter_required`; the body bound and `digest_mismatch` come after the header. A standard package's separate `body` is also checked after the header: a non-string `body` is `input`, then its size (`source_size`), UTF-8 (`invalid_utf8`) and pinned digest (`digest_mismatch`), in that order. A file that selects any engine other than the current one is the compiler error `engine` at line 1 ([contract DSL](contract-dsl.md#static-validation)), not a bare code. Other header problems are compiler errors ([contract DSL](contract-dsl.md#static-validation)): `header` and `missing_field` at line 1; `unknown_field` and `field_value`, including a declared encoding other than UTF-8, at the field's YAML key line; `duplicate_field` at its first repeat |
| Checkpoint export and restore | `checkpoint_size` when the canonical data exceeds 8 MiB; `invalid_checkpoint` for a wrong profile, hash, shape or identity, including a state profile the reader does not support; `host_policy_required` for an unchecked checkpoint on a hosted profile; `contract_mismatch` when the checkpoint's contract or source pin differs from the contract selected for the restore | [Portable state](engine.md#portable-state-and-recovery), [host evidence](agents-and-extensions.md#band-permission-binding-version-1) |
| Session creation (`new`) | `unsupported_extension`, `inactivity_policy_required`, `invalid_bindings`, the host evidence codes `host_policy_required`, `invalid_room_policy`, `room_access`, `agent_binding_required`, `role_assignment` and `room_permission`, `watch_bindings`, `control_budget_required`, `invalid_initial_presence` for a malformed presence snapshot, `mention_limit`, `invalid_config`, `invalid_time_range`, `completion_size` when the byte cap cannot carry a reply a duty or collection can require | [Session configuration](engine.md#session-configuration-and-trusted-context), [host signals](host-signals.md), [collections](coordination.md), [response capacity](defaults-v2.md#reserve-known-response-capacity) |
| Room creation (`room_new`) | `invalid_bindings` for missing, extra or duplicate room bindings; `invalid_time` for an initial time that is not a nonnegative safe integer; `room_policy_required` when the host policy is missing a limit, names an unknown key or has a limit that is not a positive safe integer | [Sessionless room events](engine.md#sessionless-room-events) |
| Extension negotiation at `new` | `unsupported_extension` when the host's capabilities do not cover every declared extension | [Extensions](agents-and-extensions.md#declare-extensions-then-negotiate-support) |
| Host evidence | `host_policy_required`, `invalid_room_policy`, `room_access`, `agent_binding_required`, `role_assignment`, `room_permission` | [Host evidence](agents-and-extensions.md#band-permission-binding-version-1) |
| Contribution controls | `invalid_contribution` for a malformed report; `unauthorized_contribution` when a member reports for another member; `contribution_scope` for a wrong session, collection entry or member; `already_contributed` when that member is already settled, by its reply, an earlier failure or the deadline; `contribution_capacity`; `id_conflict` for a reused report ID with different content; `terminal` after the session ended; `host_authority_required` for a failure report not from the trusted host; `unsupported_extension` without `room.contributions/1`; plus the host evidence codes for a member stop | [Contribution outcomes](contribution-outcomes.md) |
| Host events | `host_authority_required`, `invalid_host_event`, `unsupported_extension`, `subject_binding`, `duplicate_host_event`, `id_conflict`, `participant_removed`, `stale_host_event`, `invalid_time_range`, `terminal`, `control_capacity`; `control_budget_required` at creation. An exact retry is `duplicate_host_event` first; after it, a time that is invalid or before the state's last observed time is `stale_host_event` | [Host signals](host-signals.md#authority-ordering-and-continuity) |
| Any operation that takes `now` | `invalid_time` for a time that is not a nonnegative safe integer; `clock_regression` for a time before the state's last observed time; host events excepted (see the Host events row) | [Session configuration](engine.md#session-configuration-and-trusted-context) |

Reference-generated full objects are accompanied by manually stated semantic assertions. Use both: byte comparison finds representation drift; independent assertions detect a mutually repeated reference defect. Implement an independent small transition oracle or second-language reducer for claimed engine interoperability. Test canonical bytes in at least two languages with control characters, astral characters and literal escape sequences. Validate Unicode grapheme behavior against the pinned Unicode conformance data, not only a runtime's current string library.

## Fault and operational tests

The portable reducer vectors do not qualify persistence or effects. Add real process tests at these boundaries: before input commit, after input commit, after execution but before output commit, after output commit before staging, after stage before send, after destination commit before acknowledgement, after acknowledgement before local state/snapshot commit, during repair generation/validation/install, and during shutdown. Assert whether replay is allowed, whether code/model invocation occurred, how many destination effects exist, and which state/receipt is durable.

Test malformed/duplicate JSON, partial writes, corrupt snapshots, wrong source/runtime/schema, stale view, clock regression/offline expiry, exhausted limits, permission/ownership/alias violations, missing isolation, resource exhaustion, bad TLS, wrong actor/tenant/member, simultaneous requests and notification loss. Include controls and both standalone/Band behavior when claiming those profiles.

Use task-specific semantic fixtures for program coverage and a live-provider qualification set for LLM fallback/repair. Record model identities and actual usage. A deterministic scripted provider is appropriate for repeatable fault tests but cannot establish live language quality or provider billing.

## Completion contract

A generated implementation is reviewable when its source, dependency locks, schemas, release artifacts, configuration, operator runbooks, requirement ledger and evidence are complete. It is conformant only for the profiles whose portable and deployment-specific checks pass. It is production-qualified only for the environment/workload named in its release manifest. It is deployed only after authorized rollout and actual deployment verification.

The generator's final report must list implemented profiles, exact qualified artifact identities, actual check exit codes, remaining requirements and migration compatibility. Keep unresolved work visible; do not hide it behind a broad “production ready” label.

The reference library exposes `ContractLab.compile_source/1` for the exact JSON-compatible `source` object. It shares card validation and the compiler with both file formats (frontmatter and standard), rejects extra fields and header-only coercions, and never resolves a caller-supplied file path. The conformance adapter uses this same public boundary.

The loader supports the exact YAML library releases 2.9.0 and 2.12.2 with yamerl 0.10.0. Both pass the same 84 loader/Unicode tests. The standalone lockfile stays on 2.12.2; an embedding application may retain its already locked 2.9.0 without overriding the platform dependency.
