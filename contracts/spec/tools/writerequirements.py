"""Maintain the normative requirements catalog from the reviewed rows below."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from contracts.spec.tools.specversion import REVISION

ROWS = """
DSL-001|P13|compiler/2|contract-dsl.md|The loader MUST support explicitly selected frontmatter and standard discovery and reject the removed native directive header.|Compile equivalent current packages in both supported forms; reject the native header without executing a body.
DSL-002|P13|compiler/2|contract-dsl.md|Discovery MUST enforce a 4096-byte maximum including delimiters without reading contract bodies.|Probe exact boundary, oversized, unterminated, CRLF and invalid UTF-8 prefixes.
DSL-003|P13|compiler/2|contract-dsl.md|Source loading MUST use bounded immutable bytes and verify package paths, unchanged headers and referenced digests.|Reject traversal, symlinks, changed prefixes, changed bodies and oversized source.
DSL-004|P13|compiler/2|contract-dsl.md|The compiler MUST implement the complete line grammar and reject unsupported directives or expressions.|Run positive and negative grammar vectors for every declaration and option.
DSL-005|P13|compiler/2|contract-dsl.md|Preceding triple-quoted documentation MUST attach to the next node and remain nonexecutable metadata.|Check indentation, escapes, newline, field/rule/duty attachment, interpolation rejection and orphan literals.
DSL-006|P13|compiler/2|contract-dsl.md|Types MUST preserve exact fields, bounds, enum values and accepted-fact reference kinds.|Reject invalid bounds, duplicate values and unknown reference types.
DSL-007|P13|compiler/2|contract-dsl.md|Static validation MUST reject ambiguous rules, unknown names, unmatched references and structurally unsatisfiable duties.|Assert each documented error code with a bounded line/detail result.
DSL-008|P13|compiler/2|contract-dsl.md|Compilation MUST produce deterministic normalized IR, rule/duty IDs and behavior/source digests.|Compare full IR in independent adapters; documentation-only changes preserve only the behavior digest.
DSL-009|P13|compiler/2|contract-dsl.md|Query APIs MUST retain attached documentation in full and role-specific definitions.|Query every node category and check filtering plus metadata preservation.
DSL-010|P13|compiler/2|contract-dsl.md|Untrusted malformed source MUST return bounded errors without executing code or crashing the service.|Fuzz bounded source, escapes, invalid Unicode, huge digits and unsupported YAML constructs.
ENG-001|P14|session-engine/2|engine.md|Session configuration MUST bind every role to one distinct trusted principal and validate all limits and time arithmetic.|Reject missing/extra/duplicate bindings, invalid IDs, absent policy and overflow.
ENG-002|P14|session-engine/2|engine.md|Wire normalization MUST reject duplicate keys, unsupported numbers, unknown fields and transport conflicts.|Run raw JSON and transport-carrier vectors including literal escapes and nested duplicate keys.
ENG-003|P14|session-engine/2|engine.md|Canonical bytes and SHA-256 identities MUST match the specified integer-only UTF-8 profile.|Compare byte vectors in at least two languages including controls and astral Unicode.
ENG-004|P14|session-engine/2|engine.md|Text fields MUST enforce a finite UTF-8 byte bound and any declared Unicode 16.0 extended grapheme bound independently of runtime defaults.|Pass the pinned Unicode GraphemeBreakTest corpus and cross-runtime drift regressions; test exact multibyte byte bounds, grapheme bounds and rejected grapheme-only declarations.
ENG-005|P14|session-engine/2|engine.md|Admission MUST follow the specified clock, identity, envelope, duplicate, version, rule, payload, reference and quota precedence.|Use inputs violating multiple conditions to assert the selected reason and any persisted expiry.
ENG-006|P14|session-engine/2|engine.md|Accepted messages MUST update facts, receipts, variables, counters, contributions, duties and state atomically in the reducer.|Compare exact state and decision after every trace step.
ENG-007|P14|session-engine/2|engine.md|Exact references MUST equal the currently bound accepted ID and a permitted fact kind.|Reject a nonexistent ID, another fact of the same kind and an obsolete rebound ID.
ENG-008|P14|session-engine/2|engine.md|Timed rules and events with each MUST preserve all-participant duties; grouped rules without each MUST preserve first-responder duties and independent selected obligations.|Test all roles, selected sets, duplicate contributions, multiple timed events and first-responder behavior; reject removed due all/any grammar.
ENG-009|P14|session-engine/2|engine.md|Timers MUST expire at the inclusive boundary with deterministic earliest-time and priority/ID ties.|Test just-before/equal/after, ties, late observation, clock regression and terminal idempotence.
ENG-010|P14|session-engine/2|engine.md|The current profile MUST have no idle timer and MUST reject inactivity-timeout and activity declarations; owed work and explicit contract/state clocks still expire, while optional accepted/transition/marked configuration remains valid but inert.|Exercise accepted events, transitions, duplicate/rejected/read attempts without idle expiry; reject removed source forms and check timed owed-work expiry.
ENG-011|P14|session-engine/2|engine.md|max_entries MUST count initial/reentries and select its terminal overflow outcome deterministically.|Exercise exact limit, overflow, self-loop and preservation of attempted-entry count.
ENG-012|P14|session-engine/2|engine.md|Duplicate receipt handling MUST precede terminal/version checks and preserve the original decision without another effect.|Resend before/after completion/expiry and reject conflicting actor/payload/version under the same ID.
ENG-013|P14|session-engine/2|engine.md|Recipient routing and message budgets MUST be exact and unaffected by duplicate attempts; mentions is a per-message default or rule override, never a cumulative session cap.|Check sender exclusion, deduplicated delivery, message-cap recovery, more than 200 accepted mentions, and rejected mention_budget syntax.
ENG-014|P14|session-engine/2|engine.md|Role views MUST expose only authored/addressed facts and relevant legal moves/duties while retaining documentation.|Compare views of every participant and reject outsiders.
ENG-015|P14|session-engine/2|engine.md|Portable state/checkpoints MUST have explicit profiles, strict schemas, pinned identities and validated invariants.|Round-trip full/redacted state and reject malformed recomputed-checksum packets.
ENG-016|P14|session-engine/2|engine.md|Local preflight MUST use the same semantics and a fresh authority time/version while retaining final room authority.|Compare remote/local decisions, stale views, deadline races and fresh-process restoration.
ROOM-001|P1|room-events/1|engine.md|Sessionless events MUST remain separate from session state and use explicit room bindings/policy.|Verify no session transition/counter changes and reject unauthorized/session-bearing traffic.
ROOM-002|P1|room-events/1|engine.md|Room event/mention windows MUST reset on aligned boundaries independently of session quotas.|Test multi-window gaps, exact boundaries and budget exhaustion.
ROOM-003|P1|room-events/1|engine.md|Room receipts MUST survive quota resets and cannot be silently evicted to permit repeated effects.|Test duplicates across windows and explicit retention-limit rejection.
ROOM-004|P3|room-events/1,durable-room/1|engine.md|An authority offering durable sessionless room events MUST preserve their state and receipts across service restart and concurrent admission.|Restart and race independent owners against the durable event authority.
DUR-001|P3|durable-room/1|engine.md|An authority MUST serialize decisions per scope through exclusive ownership or transactional database locking.|Race simultaneous proposals through separate processes and assert one version order.
DUR-002|P3|durable-room/1|engine.md|Acceptance MUST become durable before an acknowledgement or visible resulting message is exposed.|Crash after commit before reply and prove exactly one accepted effect/receipt.
DUR-003|P3|durable-room/1|engine.md|A failed commit MUST invalidate speculative state and recover only from verified durable history.|Inject commit/snapshot failure and compare restored state and decisions.
DUR-004|P3|durable-room/1|engine.md|Idle and offline deadlines MUST advance without a participant request and remain absolute across restart.|Stop before a deadline, restart after it and observe the original expired_at and durable outcome.
DUR-005|P3|durable-room/1|engine.md|Journal/snapshot replay MUST verify identities, history integrity and recorded decision/state agreement.|Reject corrupted records, every corrupted snapshot and mismatched configuration/source.
DUR-006|P4|durable-room/1,remote-agent/1|operations.md|Storage growth MUST be bounded without dropping active work or deduplication receipts.|Exhaust record, record-byte, database and disk capacities and verify prior commits remain recoverable.
DUR-007|P4|durable-room/1,remote-agent/1|operations.md|Backups/restores MUST be private, verified, consistent, atomically published and no-overwrite.|Restore a backup containing accepted receipts and pending work; reject corrupt/aliased targets.
AG-001|P7|remote-agent/1|remote-runtime.md|Programs MUST declare full, partial or LLM-only coverage and qualify the claimed semantic domain.|Exercise representative handled, delegated, ambiguous and unsuitable cases with expected semantics.
AG-002|P7|remote-agent/1|remote-runtime.md|The callback ABI MUST use copied JSON inputs/state and only Handled or Delegate outputs.|Check schema, checkpoint equality, illegal result shapes and no mutation of committed host state.
AG-003|P6|remote-agent/1|remote-runtime.md|Incoming events MUST be confirmed against authenticated room facts and deduplicated before execution.|Reject forged/conflicting events and repeat a completed event without code/model/effect repetition.
AG-004|P7|remote-agent/1|remote-runtime.md|Per-turn and ongoing delegation MUST persist the intended routing across turns and restart.|Alternate qualified and free-form tasks and restart during ongoing takeover.
AG-005|P6|remote-agent/1|remote-runtime.md|Local rejection MUST prevent program publication and final room rejection MUST not commit candidate state.|Propose wrong-turn, wrong-schema, stale-reference and stale-version outputs and inspect actual room effects.
AG-006|P3|remote-agent/1|remote-runtime.md|Accepted state, input completion, receipt, outbox clearing and optional snapshot MUST commit atomically.|Fail the transaction after room acceptance, recover, and assert one reply and one state transition.
AG-007|P9|remote-agent/1|remote-runtime.md|Unknown effects MUST retain exact intents and use destination reconciliation before any replacement output.|Lose acknowledgements for idempotent and non-idempotent destinations and assert the permitted retry count.
AG-008|P9|remote-agent/1|remote-runtime.md|Queues, retries, calls and repair counts MUST be bounded and persist across restart.|Exhaust each budget, retry/restart and verify no reset or hot loop.
IO-001|P17|remote-agent/1|remote-runtime.md|Every Python callback input MUST be durably recorded before executing code.|Observe the committed exact request at the actual execution boundary for initialization, turns and validation.
IO-002|P17|remote-agent/1|remote-runtime.md|Every complete bounded callback output/error MUST be recorded before staging or publishing effects.|Inspect raw stdout/stderr/result and failure records before the first room send.
IO-003|P17|remote-agent/1|remote-runtime.md|Invocation identity MUST bind its program, operation, exact inputs, execution policy and purpose.|Reject conflicting reuse of an invocation ID and isolate validation identities from live work.
IO-004|P17|remote-agent/1|remote-runtime.md|Recovery MUST reuse recorded results without executing code or rerunning initialization.|Crash after output commit before staging and block all execution functions during recovery.
IO-005|P17|remote-agent/1|remote-runtime.md|An input without durable output MUST remain explicit unresolved work and cannot silently resample.|Exit after input commit and fail output persistence; verify no rerun or publication on ordinary recovery.
IO-006|P17|remote-agent/1|remote-runtime.md|Output overflow/timeout MUST retain bounded evidence and cannot masquerade as a complete successful result.|Emit excessive/partial output, terminate, and assert explicit incomplete failure and preserved prior state.
IO-007|P17|remote-agent/1|remote-runtime.md|Offline journal replay/export MUST use recorded data without code, provider, clock advancement or external effects.|Disable execution/network interfaces while reconstructing and exporting a recorded invocation/history.
LLM-001|P9|remote-agent/1|remote-runtime.md|Provider operations MUST use trusted host configuration and bounded durable call/result records.|Crash and timeout before/after provider result recording; verify uncertainty and completed-result reuse.
LLM-002|P7|remote-agent/1|remote-runtime.md|Model output MUST pass the same state, local-contract and room-admission checks as program output.|Return malformed, illegal and semantically wrong model replies and assert the relevant hold/rejection.
LLM-003|P9|remote-agent/1|remote-runtime.md|Late provider results MUST not mutate state or publish and duplicate concurrent operations MUST be prevented.|Stall a provider beyond its deadline and release it after another control/turn boundary.
LLM-004|P7|remote-agent/1|remote-runtime.md|Live provider quality and actual usage MUST be measured separately from scripted mechanism tests and saved calls.|Run the declared semantic quality set with the pinned provider/model and record usage without secrets.
LLM-005|P10|remote-agent/1|remote-runtime.md|Provider/model context MUST be bounded and contain only authorized public context, never credentials/private checkpoints.|Inspect captured prompts for private markers and stress long history/output capacity.
LLM-006|P10|remote-agent/1|openai-provider.md|Reported provider usage MUST be counted once per durable result, and missing usage or prices MUST remain explicitly unknown.|Recover completed calls, race response and repair results, and compare reported/unreported calls and exact integer token/cost aggregates.
LLM-007|P9|remote-agent/1|openai-provider.md|A declared native OpenAI binding MUST journal its prepared body, fix its authenticated TLS destination, bound requests/results and preserve uncertain outcomes without automatic resampling.|Capture the real prepared request before dispatch; simulate refusal, incomplete output, body overflow, rate limit and uncertain HTTP failure; verify no effect or repeated call.
REP-001|P8|remote-agent/1|remote-runtime.md|Background repair MUST allow subsequent LLM turns while generation/validation is pending.|Hold repair execution and complete another live turn before releasing it.
REP-002|P8|remote-agent/1|remote-runtime.md|Candidates MUST preserve state schema, initial state and accepted history and pass independent semantic checks.|Reject changed schema, rewritten answers, bad pending-fault handling and insufficient validator evidence.
REP-003|P8|remote-agent/1|remote-runtime.md|Candidate validation MUST be isolated, journaled and incapable of dispatching effects.|Inspect validation invocation records and deny network/filesystem/process effects during probes.
REP-004|P8|remote-agent/1|remote-runtime.md|Installation MUST catch up concurrent history and commit at a safe boundary with routing-epoch protection.|Advance conversation/control during validation and prove bounded catch-up and no stale automatic resume.
REP-005|P8|remote-agent/1|remote-runtime.md|Interrupted repair and rollback MUST preserve accepted state, pending effects and explicit generation uncertainty.|Restart during generation/install and roll back a compatible artifact without replaying old effects.
SEC-001|P2|remote-agent/1|remote-runtime.md|Generated code MUST not access host secrets/journal, network, unrestricted processes or writable artifacts.|Run real namespace/seccomp probes on the qualified release host and prove required denials.
SEC-002|P2|remote-agent/1|remote-runtime.md|Missing isolation or exhausted callback resources MUST fail closed with bounded cleanup.|Remove isolation capability and exercise memory, CPU, descriptors, output and wall-clock exhaustion.
SEC-003|P4|durable-room/1,remote-agent/1|operations.md|State/artifact/backup files MUST enforce private ownership and resist symlink/hard-link aliasing.|Use permissive umask, wrong ownership/writable directories and aliased paths in filesystem probes.
SEC-004|P6|standalone-http/1,band-room/1|transport.md|Authenticated principals and explicit permissions MUST determine actor and operation authorization.|Test spoofed body actors, insufficient grants, wrong rooms and revoked membership.
SEC-005|P6|remote-agent/1|remote-runtime.md|Controls MUST be action-scoped, journaled, deduplicated and unable to escalate a wake caller's grants.|Replay/conflict controls and request unauthorized takeover, repair or resume.
SEC-006|P12|compiler/2,durable-room/1,remote-agent/1|operations.md|Release security/dependency review MUST cover the actual trusted runtime and isolated execution boundary.|Audit pinned dependencies, input boundaries, storage/effects and documented residual trust with findings dispositions.
NET-001|P6|standalone-http/1,band-room/1|transport.md|Remote requests MUST use verified TLS with no credential-bearing redirect or remote cleartext fallback.|Use valid/untrusted/expired/wrong-host certificates, redirects and nonloopback HTTP.
NET-002|P6|standalone-http/1,band-room/1|transport.md|Framing, request/response bytes, headers, connection count and absolute deadlines MUST be bounded.|Test duplicate Content-Length, transfer encoding, slow TLS/headers/body, response overflow and saturation.
NET-003|P6|standalone-http/1,band-room/1|transport.md|Capability negotiation MUST pin actor, session, contract identities and durable idempotence.|Reject mismatched/unsupported capabilities before processing agent turns.
NET-004|P10|standalone-http/1|transport.md|Health and observer metrics MUST distinguish readiness from liveness without exposing payloads or credentials.|Fail recovery/storage and inspect unauthenticated/authenticated metrics/log output.
NET-005|P9|standalone-http/1|transport.md|Shutdown MUST drain admitted handlers before their backend resources close.|Stop during active bounded requests and restart with pending work intact.
NET-006|P6|standalone-http/1,band-room/1|transport.md|Reconnect MUST recover durable facts/receipts rather than rely solely on notifications.|Drop notifications/connection, replay history and prove no lost or duplicate replies.
BAND-001|P6|band-room/1|band-transport.md|Band requests MUST preserve acting agent/user, tenant and room membership authorization.|Exercise both HTTP surfaces, agent-owned keys, human acting contexts, malformed typed IDs, removed/blocked/unbound participants and cross-tenant access; verify exact role coverage, distinct active bindings, immutable create retries and competing rooms claiming one session ID.
BAND-002|P3|band-room/1|band-transport.md|State, receipt and required accepted chat message MUST commit atomically under a database session lock.|Race separate request owners and inject each transaction failure boundary, quota refusal and commit-before-reply loss; confirm one accepted state/receipt/chat record, original duplicate decisions and after-commit-only notification.
BAND-003|P6|band-room/1|band-transport.md|Ordinary caller-supplied chat metadata MUST not fabricate an authoritative contract fact.|Forge contract-looking metadata and verify adapter/authority rejection; submit a private canary in the proposal ID and prove it is absent from shared chat records and notifications while the expected receipt_handle is present; inspect the same records for private payload/documentation canaries and prove that only authenticated role data supplies the actual facts.
BAND-004|P6|band-room/1|band-transport.md|Remote programs and models MUST remain outside Band and use authenticated public role data.|Run a remote host against a real Band room; pin actor/session/source identities, inspect credentials/data/authority boundaries, drop notifications and reconcile an exact pending outbox without repeating execution.
BAND-005|P12|band-room/1|band-transport.md|Band integration MUST preserve existing durable history, notifications and ordinary room behavior.|Run affected native regression gates and authenticated end-to-end room delivery/reconnect checks; verify hidden advancing inbox pages, deadlines on identity/view/proposal/inbox and offline sweep, inactive/deleted rooms, terminal receipts and individual chat deletion.
OPS-001|P5|durable-room/1,remote-agent/1|operations.md|Releases MUST include reproducible packages, locks, entrypoints, schemas, configuration and service lifecycle files.|Build/install in a clean environment and serve a real request through installed entrypoints.
OPS-002|P5|durable-room/1,remote-agent/1|operations.md|Upgrades and rollback MUST preserve receipts, pending work and compatibility with declared durable versions.|Migrate/rollback a nonempty store with an unresolved effect and inspect exact identities afterward.
OPS-003|P4|durable-room/1,remote-agent/1|operations.md|Retention/archive policy MUST be explicit and cannot silently delete active work or advertised receipts.|Exercise policy boundaries and verify archived recovery and deduplication guarantees.
OPS-004|P10|durable-room/1,remote-agent/1|operations.md|Operators MUST see queue/storage pressure, faults, repairs, provider use and unknown effects without secret payload logging.|Inspect metrics/logs with synthetic secret markers and induced faults/capacity pressure.
OPS-005|P5|durable-room/1,remote-agent/1|operations.md|Startup MUST complete ownership, integrity, capability, artifact, expiry and isolation checks before readiness.|Start with each prerequisite broken and observe fail-closed readiness.
OPS-006|P9|durable-room/1,remote-agent/1|operations.md|Lifecycle MUST terminate owned children and prevent detached workers from mutating closed/reacquired stores.|Exercise callback/provider/repair shutdown and subsequent ownership acquisition.
OPS-007|P4|durable-room/1,remote-agent/1,band-room/1|operations.md|Backup recovery drills MUST include source/artifacts and pending as well as accepted work.|Restore an independently verified release backup and measure recovery with no repeated effects.
OPS-008|P11|durable-room/1,remote-agent/1,band-room/1|operations.md|Production qualification MUST name and meet a bounded workload, resource envelope and recovery/latency objectives.|Run declared concurrency/load/soak/crash tests and publish measured distributions and limits.
QUAL-001|P16|compiler/2,session-engine/2,remote-agent/1|implementation-guide.md|Requirements, schemas and conformance fixtures MUST be complete, versioned, linked and validated.|Validate IDs, links, schema instances, fixture manifests and profile coverage.
QUAL-002|P14|session-engine/2|implementation-guide.md|Interoperability claims MUST include independent semantic assertions or a second implementation in addition to reference output.|Run a second-language reducer/oracle and mutation-sensitive expected trace checks.
QUAL-003|P12|durable-room/1,remote-agent/1,band-room/1|operations.md|Evidence MUST identify exact tested artifacts, commands, real exit codes and failed/skipped/incomplete checks.|Verify a machine-readable release audit against produced artifacts and requirement dispositions.
QUAL-004|P15|remote-agent/1|implementation-guide.md|Generation guidance MUST preserve authority boundaries, unknown-effect handling and independent semantic quality checks.|Generate/inspect an adapter from the specification and reject a deliberately weakened boundary.
QUAL-005|P12|standalone-http/1,band-room/1|operations.md|Implemented, reviewed, packaged, published, merged and deployed states MUST be reported separately.|Audit final delivery against both selected deployment profiles without relabeling pending work.
""".strip()

requirements = []
for row in ROWS.splitlines():
    identifier, ledger, profiles, spec, statement, acceptance = row.split("|")
    requirements.append(
        {
            "id": identifier,
            "level": "MUST",
            "ledger": ledger,
            "profiles": profiles.split(","),
            "specification": spec,
            "statement": statement,
            "acceptance": [acceptance],
        }
    )
catalog = {
    "profile": "band-contract/requirements/1",
    "revision": REVISION,
    "production_ledger": "../PRODUCTION.md",
    "requirements": requirements,
}
if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "requirements.json"
    path.write_text(json.dumps(catalog, indent=2) + "\n")
    sys.stdout.write(str(f"{len(requirements)} requirements written") + "\n")
