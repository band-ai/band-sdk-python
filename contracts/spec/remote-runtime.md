# Remote program and LLM runtime specification

**Optional experiment.** This document specifies the requirements of a client
runtime an operator may choose. It is not the required way to connect an agent
to a room. Ordinary agents need no journal, SQLite database, local checker,
replacement program, snapshot or repair controller. They use public discovery,
views and proposals as described in [room/client separation](room-clients-v2.md).
The `MUST` requirements below apply when opting into `remote-agent/1`; they do
not apply to every room participant. Its `llm_only` mode is one runtime mode,
not a mandatory wrapper around other model-based agents.

Profile `remote-agent/1` runs beside the agent, outside the room authority. It owns the selected program, journal, state, snapshots, outbox, provider adapter and repair workflow. The room continues to authenticate and admit every effect. The host MUST NOT infer semantic competence from a structurally valid contract reply.

## Coverage and routing

Each program declares a state schema version and coverage `{mode,handles,delegates}`. `mode` is `full`, `partial`, or `llm_only`; the lists contain nonempty human-readable conditions. Partial and LLM-only programs require at least one delegation condition. Full coverage is a claim for a bounded, tested task domain, not permission to handle arbitrary language. Host qualification must demonstrate the claim with positive, negative, ambiguous and adversarial cases relevant to the task.

- **Full:** handle every qualified domain case in code. Unexpected input, exceptions and invalid output still trigger a bounded fallback/hold.
- **Partial:** handle only qualified cases and explicitly delegate the rest. A narrow exact command such as `ping` is acceptable; broad free-form interpretation MUST NOT silently become keyword matching.
- **LLM-only:** invoke no ordinary program handler. Retain contract admission, journaling, state, controls and effect recovery around the model route.

`Delegate` selects one turn or ongoing LLM handling. A one-turn delegation leaves the durable mode unchanged; the next qualified turn can use the program. Ongoing delegation persists mode `llm` across restarts. Explicit host-authorized controls can wake or take over with an LLM. Returning to program mode requires compatibility and task-quality validation; a faulted/quarantined artifact cannot simply be re-enabled.

## Python callback ABI

The selected artifact is immutable UTF-8 source of at most 262,144 bytes, addressed by its SHA-256. The host verifies source bytes and its authorized artifact directory before execution and on recovery. A package/release identity also pins the SDK and execution policy. Program code exposes:

```python
STATE_VERSION = "1"  # nonempty string, at most 100 characters
COVERAGE = {
    "mode": "partial",
    "handles": ["exact, qualified command"],
    "delegates": ["language judgment or unqualified input"],
}


def initial(view) -> dict: ...
def handle(event, state, context) -> Handled | Delegate: ...
```

`view` is the documented public role view, never the private room checkpoint. `event` is the host-normalized, room-confirmed accepted fact (`id,session,kind,payload,actor,role` for session traffic). `state` is a JSON object copied from committed state. Each invocation gets fresh copies; mutating them does not mutate the host's committed state. `context.view` is the same public view. `context.checkpoint(state)` stages a JSON copy of final state and performs no independent storage write.

`Handled(state, message=None)` returns a JSON-object state and zero or one `Message(kind,payload)` effect intent. A message has exactly a nonempty string kind and JSON-object payload. The program does not choose its authenticated actor, recipients, session, message ID, expected version, service token or timestamp. Those are supplied or verified by the host/authority. If a checkpoint was requested, its staged state MUST equal the returned final state.

`Delegate(reason, scope="turn", repair=False)` returns a nonempty reason of at most 2,000 characters, scope `turn` or `ongoing`, and a boolean repair request. It changes no committed program state. A requested repair is constrained by host policy and budget. Any other return shape is a program failure. A structurally valid `Handled` remains subject to exact local and authoritative contract checks and semantic qualification.

The worker request is a JSON object with `op` (`describe` or `handle`), `artifact:{path,sha256}`, `isolation` policy and the exact callback arguments. Describe returns `{schema,coverage,initial}`. Handle returns either `{type:"handled",state,message,snapshot}` or `{type:"delegate",reason,scope,repair}`. A worker exception returns a bounded `{error}` object. All numbers use the host's safe-integer JSON profile. A fresh sandbox process runs each callback; program globals are not durable state.

## Isolation and effects

Generated Python MUST run without provider credentials, the journal, the host home directory, ambient Python paths, a writable source artifact, host process visibility, network access or the ability to spawn unrestricted processes. The supported Linux boundary uses separate user/PID/network/IPC namespaces, read-only runtime/package/source mounts, an empty environment, syscall restrictions and hard resource limits. A missing boundary MUST fail closed; there is no automatic unsandboxed fallback.

Default limits are 256 MiB address space, 32 descriptors, no child processes, no file output/core dumps, CPU seconds rounded up from the callback timeout, and a host wall-clock deadline. The supervisor kills the process group on timeout, excess output, owner shutdown or completion. The shared kernel and installed runtime are trusted components; release qualification must exercise the boundary on the actual deployment host.

Every required deny-rule syscall name MUST resolve before generated source is
evaluated. An unknown name (`__NR_SCMP_ERROR`, `-1`) MUST fail closed, not be
treated as architecture absence. The reference may omit a known single-syscall
architecture-absence pseudo number (`<= -10000`); multiplexed pseudo numbers,
such as `socket=-101`, still require libseccomp rule installation. Any failed
rule installation or filter activation MUST refuse the callback. An older
libseccomp that cannot resolve a required name is unsupported, even when
bubblewrap itself can start.

The host MUST use explicit allowlisted environments for child processes; it MUST
NOT forward its ambient provider credentials to the engine, command providers,
validators or isolated programs. The reference host preserves only its configured
`PATH`, with fixed `LANG=C.UTF-8` and `TZ=UTC`. Command providers and validators
also receive `PYTHONPATH` pinned to the installed runtime package root, never the
ambient import path. The bubblewrap launcher instead has fixed
`PATH=/usr/bin:/bin`, and bubblewrap clears the generated program's environment.
Only the trusted native OpenAI worker additionally receives `OPENAI_API_KEY`;
no other host environment variables are forwarded. Command integrations cannot
rely on ambient credentials, `HOME`, loader options or import configuration.

Environment allowlisting is not filesystem isolation for operator-trusted command
providers or validators. A command running under the service's UID can still read
`provider.env` and `room.token` from disk when that UID has access, even if the
files have mode 0600 and their contents are absent from the child environment.
Untrusted command integrations need a separate UID and filesystem access controls
that deny access to those credential files; environment stripping alone is not
that boundary. Generated programs instead use the isolated boundary above.

Programs MUST express supported external effects as intents. The host dispatches only journaled, contract-admitted intents. Direct networking, host filesystem writes, subprocess execution and provider access are prohibited. Time/randomness visible inside a process are not a substitute for durable input/state: a recovery replay uses recorded results. Re-executing a candidate is an explicit validation operation and must remain isolated from effects.

## Exact invocation journal

Every Python callback, including initialization and repair/quality-validation probes, MUST have an invocation record. The callback's exact explicit inputs and complete bounded process outputs MUST be retained, whether the callback succeeds, delegates, crashes, times out, returns invalid JSON, exceeds capacity or proposes an illegal message. Logging only accepted turns is insufficient.

1. Allocate a stable invocation ID for a live turn (session, actor, accepted input and bounded attempt identity). Validation probes have separate identities/purposes and cannot masquerade as live work. The reference uses an exact-input digest for validation identities, covering the artifact, purpose, request and execution bounds; repeated validation replays retained outputs rather than appending another copy. Different explicit inputs create a new retained probe. Interrupted probes remain unresolved; this reuse never authorizes resampling a live turn.
2. Canonically encode and durably append `program_input` **before** launching code. Record the program/source identity, operation, exact request, public view, prior state, isolation policy, timeout, output bound, purpose and input fingerprint.
3. Execute only after that commit succeeds. Capture both process output descriptors as bytes. The reference worker reserves stdout for its result and routes ordinary Python print diagnostics to stderr. Low-level writes are still captured, even when they corrupt the result protocol.
4. Durably append `program_output` with the same invocation ID, input fingerprint, raw stdout/stderr (base64), exit status, elapsed milliseconds, failure and completeness **before** interpreting the result for live state or effect publication.
5. Only a fully recorded, valid successful result may proceed to preflight and outbox staging. A database error recording output leaves the invocation unresolved and cannot authorize publication.

The reference input and combined-output limit is 256 KiB each. Limits are contractual: an over-limit stream is terminated and represented as an explicit incomplete failure with retained prefix bytes, not a successful truncated response. The journal's record cap must accommodate encoding and index overhead. A host must reject an execution whose required input/output record cannot fit; it must not run it and silently omit its history.

Use the following state distinction:

| Durable state | Recovery behavior |
|---|---|
| No invocation input | No callback is known to have started; admission of a new bounded attempt is permitted |
| Input committed, no output | Outcome unresolved; do not infer success or silently rerun/resample it |
| Input and output committed | Reuse recorded bytes/result; no program execution is needed |
| Result staged as effect | Reconcile the exact stable outbox identity, independently of callback execution |
| Destination acceptance committed | Restore accepted state and receipt; do not republish the historical effect |

For an unresolved pure invocation, an explicit host policy/control may abandon that attempt and choose LLM handling or another validated attempt while preserving the original unresolved record. It MUST NOT erase an unresolved external outbox effect. A process exit between execution and output commit cannot be made magically atomic with computation; the explicit unresolved state is required.

An offline replay/inspection operation reads and validates recorded input/output and reconstructs state from recorded accepted transitions. It MUST NOT execute code, call a model, send a message, advance a live room, or repeat an external effect. Candidate re-execution, live recovery, and offline replay are distinct operations. The reference `InvocationJournal.recorded(id)` exposes this nonexecuting record read.

## Transactional state, snapshots and outbox

The durable store contains the append-only event journal, derived current state, snapshots and outbox in one transactional database. Journal records are ordered, hash-linked and include their materialized-state updates. Snapshots bind program hash, state schema, state value, journal sequence and journal head hash. Restore verifies the chain, every retained snapshot and agreement of derived state with journal updates. Corruption fails closed and preserves evidence.

The reference private journal hash profile uses the public canonical JSON domain and key ordering, with lowercase hexadecimal digits in otherwise identical `\u00hh` control escapes. This preserves its existing on-disk history; it MUST NOT be confused with public protocol checksums, whose hexadecimal escape digits are uppercase. A frame hashes `{sequence, kind, data, updates, previous}` before adding `hash`; the first previous hash is 64 zeroes. Invocation `input_hash` uses this private profile over the complete recorded input object. The portable invocation fixture supplies both bytes and hash expectations. Other private stores MAY use a different explicitly versioned encoding, but imports and migrations MUST verify the original hash profile before converting it.

The host pins actor/session/contract/source, initial artifact, controls, configured bounds and destination idempotence policy. Configuration changes after committed initialization require a verified migration. Before the atomic `initialize` commit, the effect-free describe callback may run again under a fresh initialization attempt ID, preserving every failed or interrupted attempt. An uncommitted pending configuration may be corrected; it is not the pinned configuration. Initialization metadata and initial state are recorded; restart after committed initialization verifies selected source bytes without rerunning initialization. A repaired artifact's installation and state snapshot are committed atomically.

For a live turn:

1. Admit inputs through a single consumer of the authority's ordered durable inbox. Revalidate each event against an authenticated accepted room fact visible to this actor. Reject forged or conflicting duplicate events. Journal the normalized input and enqueue it before callbacks/models run. HTTP pushes are verified notifications only: they wake this consumer without appending input, advancing its cursor or running callbacks/models. Notification arrival order cannot determine turn order; periodic polling recovers missed pushes and process-local wake signals. A notification does not clear retained held work or authorize a retry.
2. Process the bounded queue in order. Record the chosen route, attempt identity, committed prior state and callback view. Do not run a second producer while a prior effect is unresolved.
3. Execute the selected route using the invocation journal. Record failures/delegation and enforce response/model/repair budgets. Obtain a fresh authority projection after callback latency.
4. Form a stable outbound envelope with the fresh expected version. Its ID comes from the keyed derivation in [outbound proposal IDs](#outbound-proposal-ids), never from peer-visible data alone. Local preflight must accept it. An unanswered active role duty is not silently treated as a completed turn with no message.
5. Commit a staged record containing input, prior/candidate state, exact message/envelope, producer, invocation/program identity, view, snapshot request, attempt and send count **before** dispatching it.
6. Send once under the transport policy. On acceptance or duplicate, verify the original receipt's ID, session, actor, kind and payload against the staged proposal. Then atomically append the completed turn, update committed state, dequeue/complete the input, clear outbox and optionally snapshot.

The state remains unchanged while a proposal is unaccepted. A definitive room rejection may discard that candidate, record the reason and select a bounded fallback with fresh context. An unknown acknowledgement retains the exact proposal and candidate state; it is not a rejection and cannot justify a replacement reply. Identical input duplicates return the recorded result without a new state transition or provider call.

An idempotent destination supports exact resend/receipt reconciliation across its own restarts. A non-idempotent or unverifiable destination leaves the effect held for reconciliation; the host MUST NOT retry it blindly. Local storage plus an outbox does not create a general exactly-once guarantee for arbitrary external systems. The room profile achieves one admitted effect per retained actor/message fingerprint by combining atomic durable acceptance with exact retries.

The reference controller supports only idempotent room authorities and rejects an unsupported capability before initialization or callback dispatch. It has no non-idempotent reconciliation mode. The existing `idempotent_room:true` configuration pin is retained for journal compatibility, not as an operator-selectable alternative adapter policy.

### Outbound proposal IDs

The host, never a callback or model, allocates a proposal ID. All clients MUST
use at least 128 bits of OS-CSPRNG output or a keyed derivation retaining at
least 128 bits with a host-private random key. IDs MUST NOT be derived from
payload content. IDs share the whole session's namespace;
another actor's reuse of an accepted ID is `id_conflict`. See the
[ordinary client rules](room-clients-v2.md#proposal-ids-private-randomness-shared-namespace).

For `remote-agent/1`, the host MUST use this keyed derivation for new envelopes:

```text
"remote:" + hex(HMAC-SHA-256(host_key,
  canonical(["band-remote-id/1", session, actor, input_id, attempt])))[:40]
```

Here `canonical` is the [protocol JSON encoding](engine.md#json-and-canonical-bytes),
not concatenation or the private journal hash encoding. The domain label is
required. The lowercase 40-hex-character output retains 160 bits. The key is
32 bytes from the OS CSPRNG, held only in host-private storage and used only for
proposal-ID derivation, never authentication, tokens or checksums. Payload data
is not part of the derivation. Identical inputs/attempts under the same key
produce the same ID across a restart; independent host keys produce different
IDs. The reference runner implements this derivation for newly formed envelopes;
pre-H7 envelopes already stored in the outbox retain their original IDs.

The exact envelope, including its ID, MUST be committed in the staged outbox
before dispatch. Recovery/reconciliation MUST read that persisted ID, never
re-derive it or replace it, including after key rotation, key loss or restore.
Key changes affect only newly formed envelopes. They do not authorize a new
reply for an unresolved staged proposal. Key generation, storage, export,
backup, rotation and disclosure recovery are specified in
[operations](operations.md#outbound-id-host-key-lifecycle).

### Commit boundaries and recovery

The following names describe durable milestones, not required private database
column names. The host's `completed` result and HTTP 202 `held` status are not
engine decisions. Engine decisions remain `accepted`, `duplicate` or `rejected`.

| Last durable milestone | Work that recovery may do | Work it must not infer or repeat automatically |
|---|---|---|
| Accepted input queued; no invocation input | Select a bounded attempt and record its invocation input | Claim a callback has completed |
| Invocation input; no durable output | Retain unresolved computation and expose the hold | Rerun Python or resample a model as if nothing happened |
| Complete invocation output; no staged proposal | Read the recorded result, refresh authority context and validate/stage it | Execute that completed invocation again |
| Proposal staged; room outcome unknown | Reconcile the exact ID, payload, actor and expected version with an idempotent authority | Replace its envelope, clear its uncertainty or install an artifact underneath it |
| Room acceptance durable; host turn commit absent | Verify/recover the original receipt and finish the local transaction | Produce a replacement answer or apply candidate state twice |
| Host turn commit durable | Return the retained completed-input result on redelivery | Execute a new turn for the identical input |

Only the room knows whether its commit happened before a lost reply. A network
timeout therefore belongs to the staged/unknown row even if the sending process
believes the request failed. A definitive engine rejection permits a new bounded
attempt after recording the rejection; a transport error does not supply one.
Explicit authorized abandonment of unresolved computation follows the control
rules below and never abandons a staged effect.

`Handled(state, message=None)` has a separate, effect-free path. The host still
records the invocation output, refreshes the authority view and verifies that
an unanswered role duty is not being silently completed. When the no-message
result is permitted, it commits input completion, candidate state and any
snapshot together with a null receipt. There is no room acceptance to invent
or reconcile. A restart after staging this result can finish that local commit
without a callback or network publication.

These identities have different lifetimes:

| Identity | What must remain stable |
|---|---|
| Accepted input ID | The verified incoming fact and its completed result across redelivery |
| Invocation/model operation ID | Program/provider identity, exact request, execution policy and purpose for one attempt |
| Outbound proposal ID | The complete staged envelope and its original accepted receipt across reconciliation |
| Program SHA-256 and state schema | Artifact bytes and compatible state interpretation across execution, validation and install |
| Control actor and ID | The exact authorized action/reason and its activation/result across retries |

Allocating a new invocation identity is not permission to replace an unresolved
proposal. A new program artifact is not a migration of accepted history. Repair
validation has its own invocation identities and cannot generate live effects.

## Model provider boundary

The optional [native OpenAI binding](openai-provider.md) defines an exact
prepared request, bounded API result, uncertainty policy and durable token/cost
diagnostics. Other providers retain the command/callback boundary below and
must report unknown usage honestly.

Provider integration is host configuration, never a command supplied by room text. Providers expose `respond`, `wake`, and `repair`; quality validators are separately trusted host configuration. The model receives the documented role contract, visible accepted context, committed state/schema, relevant recorded history, fault, coverage, selected source identity and explicit budgets. It MUST NOT receive transport secrets or private/opaque engine checkpoints.

`respond` returns `{state,message,scope}` with turn/ongoing scope; the host applies the same result, contract and effect rules as for a program. A model's self-assessment cannot bypass preflight or room admission. `wake` returns an allowed requested action (`none`, `takeover`, `repair`) and a note; it does not itself publish a room message. `repair` returns source or a verified artifact and a boolean resume request. Provider-specific tokens, finish reasons and usage are recorded separately from business state.

Every model operation must be bounded by call count, wall time, input/output capacity and provider resource policy. A timed-out late result cannot later publish or alter accepted state. There must be no concurrent duplicate operation when a previous callback is still running. Production adapters MUST durably reserve calls and record complete results/errors, and MUST preserve uncertainty across a crash after dispatch. Completed provider results are reused; an unrecorded in-flight provider outcome is not silently resampled. Those requirements apply even though model computation cannot share a database transaction with the host.

Qualification must measure task quality independently from saved calls. Test full/partial/LLM-only cases against expected semantics, include abstention/delegation, and report model/provider identity and usage. Do not derive a token or dollar saving merely from the number of callbacks suppressed. Prompt and source changes require requalification of the affected domain.

Model durability uses `remote-agent/model-input/1` and `remote-agent/model-output/1` in the reference host. The input binds a stable operation ID, operation, provider identity, exact prepared request, deadline and output limit; its commit also reserves the call budget. The result links its input hash and records either raw command stdout/stderr/exit information or a trusted callback value/failure. Command-provider requests include the exact system prompt, pinned source bytes and bundle. The result is durable before parsing generated source, staging a reply or publishing an effect. A completed result is read without preparing a new prompt or calling the provider. An unfinished/uncertain result holds the original execution plan. Timeouts retain that uncertainty even when a late callback eventually returns.

Model input is bounded to 512 KiB and raw combined output to 256 KiB in this profile. The public role view and current state remain complete in a bundle capped at 128 KiB; if they do not fit, the call is held. A maximum of 30 historical entries is supplied as bounded summaries, with explicit omission counts. Invocation input/output summaries omit nested prompt/source/result bodies, preventing recursively growing prompts. Full private journal records remain available to authorized recovery tooling. Private configurations predating the model-journal profile require explicit migration before resuming work.

Input limits count the canonical complete `program_input` or `model_input`
object, including request and identity metadata. Raw process output limits count
stdout plus stderr bytes before base64 encoding. A process result, its journal
frame and its materialized index can require more storage than the raw stream;
hosts must account for each representation when enforcing record capacity.
The public protocol's uppercase control escapes and the private journal's
lowercase control escapes remain separate canonicalization profiles.

## Background repair and installation

A recorded program fault can quarantine the artifact and move live traffic to LLM mode. Repair runs outside the conversation's serial turn path, with a separate budget and bounded provider operation. A slow repair must not stop subsequent LLM turns. Interrupted generation is retained as interrupted/unknown, not silently repeated at startup.

A requested job with no recorded model dispatch can resume. A definitive recorded generation result can resume validation without another model call or repair-budget reservation. A started/uncertain generation remains interrupted. A validated candidate waits while any turn execution or staged proposal is pending; after that boundary clears, validation catches up with accepted history using the same generated result. The original routing epoch still governs automatic resume. Recovery must not swap the program underneath an execution pinned to a different artifact.

The repair prompt must state the exact failure, current schema, immutable accepted history, contract/role documentation, coverage boundaries and sandbox permissions. Generated source is an untrusted candidate. It cannot relax authorization, turn a held effect into success, invent prior receipts, alter the contract, change accepted answers, or grant itself new controls.

Before installation, the host MUST:

- Verify artifact location/hash, resource isolation, metadata and initial-state/schema compatibility.
- Re-execute the candidate against all relevant recorded input/prior-state/view cases in an isolated validation purpose; journal these inputs and outputs and dispatch no effects.
- Require equal candidate state and message for previously committed handled behavior. A candidate may continue delegating a turn previously handled by the LLM, but cannot rewrite that accepted answer.
- Probe the fault case, including a fault with no completed LLM answer. Require the desired handled/delegated behavior and local contract validity.
- Run independent task-specific semantic checks. A validator's boolean without named, inspectable checks is insufficient production evidence.
- Catch up with conversation history that arrived during repair and install only at a turn boundary with no unresolved staged effect or in-flight execution. Retry catch-up a bounded number of times; otherwise retain the old routing and record rejection.

Commit selected artifact, metadata, schema-compatible state snapshot, repair outcome and routing epoch together. A resume request takes effect only if host policy allows it and no later explicit routing control changed the epoch. Rejection, failure or timeout must leave conversation state/history intact. Rollback selects a previously verified compatible artifact with an auditable installation record; it does not roll back accepted room effects.

## Controls and limits

Controls are exactly `{id,action,reason}`. IDs match `[A-Za-z0-9_.:-]{1,160}`; actions are `wake`, `takeover`, `resume`, `repair`, `retry`; reason is a nonempty string of at most 2,000 characters. The authenticated actor is outside the body. Each actor has explicit action grants, separately from event-delivery and observer grants.

Journal receipt of a control before acting. Reusing the same actor/ID/content returns its recorded result; changing any of them under the same ID is a conflict. An uncompleted activation remains visibly pending and is not activated again on an identical retry. A wake cannot request an action beyond its caller's grants. Resume requires a validated, nonquarantined program and no pending execution or effect. Retry preserves prior attempt limits and effect uncertainty.

An authorized takeover may abandon an unstaged pure-program execution and select LLM handling under a fresh execution identity. An explicit `retry` may abandon an uncertain program/model computation only before an effect is staged and after any previous callback has stopped. It records the abandonment and reason, preserves the old invocation history, and consumes the next normal attempt/call budget. This can incur another provider charge. Automatic recovery and identical control duplicates do not grant that resampling permission. A completed durable result is reused. No control may discard or replace a staged proposal with an unknown room outcome.

Default reference bounds are 16 queued inputs, 100 model calls, 3 repair calls, 3 response/publication attempts, snapshots every 10 accepted turns, 2 seconds per program callback, and 30 seconds per provider/validator callback. They are configurable host policy, not DSL semantics. Counters survive restart. Exhaustion is a held/rejected state with evidence and operational visibility, never fabricated success or unbounded retry. Shutdown stops new work, drains or records in-flight work, preserves unresolved entries, terminates children, and releases ownership only after workers can no longer mutate the store.
