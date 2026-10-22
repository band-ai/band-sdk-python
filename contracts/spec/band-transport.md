# Band room transport binding

**New-profile applicability:** see [room/client separation](room-clients-v2.md).
Room access, admission, accepted records and deadlines are authority obligations.
Receiver journaling below describes the optional recoverable receiver profile;
it is not a requirement to install a journal or SDK before joining a room.
The existing route shapes below do not yet implement the new room catalog and
discovery-guided ordinary-chat rejection flow.

The Band prototype routes predate this binding. A Band deployment claims it
only after [qualification](band-integration.md#recover-and-qualify-the-boundary).

This normative binding defines `band-room/1`: contract sessions attached to
existing Band rooms. It composes `compiler/2`, `session-engine/2`,
`durable-room/1` and the shared [transport rules](transport.md#shared-transport-rules).
It returns `contract-room/http/1` identities, engine decisions and projections,
and MUST offer `contract-room/inbox/1`. The [integration guide](band-integration.md)
connects these interfaces to the DSL and a checked example. Implementation and
deployment status are separate; see its [evidence ledger](band-integration.md#evidence-and-remaining-implementation-work).

## Room and contract ownership

A Band room owns membership, tenant/acting-principal authorization, ordinary chat
history and platform message quotas. A contract session owns its immutable
source/configuration, role bindings, state, deadlines, accepted facts and
receipts. A room MAY contain several independent sessions. Session completion
MUST NOT complete or delete its Band room or other sessions.

Creating a session MUST NOT create a room, invite participants or grant room
permissions. A role binding grants a contract role only while the bound principal
also passes current Band authorization. Adding a participant later MUST NOT
automatically add a role or grant contract-data access. This binding offers no
source, binding or configuration update endpoint. A changed contract requires
a new session ID; any future migration needs a separately specified protocol.
The DSL's `room` rules are sessionless and are not hosted by these session routes.

## Authentication and paths

The agent surface uses Band's validated `X-API-Key` principal under
`/api/v1/agent`. The human surface uses existing Band human authentication and
human API access rules under `/api/v1/me`. Agent credentials MUST NOT select the
human surface or the owning user's role. Role bindings and proposals use exactly
`Agent:<canonical-uuid>` principals; the adapter refuses human role bindings
([agents and extensions](agents-and-extensions.md)). A human caller is
`User:<canonical-uuid>`, the human participant identity rather than a guessed
account/database identifier. The human surface MUST offer creation, including
exact creation retries, to an authorized active room owner/admin; it MUST NOT
offer agent-only identity, view, proposal or inbox operations to human callers.
Room and session UUIDs use lowercase hexadecimal with the `8-4-4-4-12` hyphen
layout. `band_actor` constrains role bindings to agents;
`band_authenticated_actor` permits the authenticated creator identity to be a
human or agent, and `band_uuid` enforces the UUID shape.

Under the agent prefix, the API MUST expose:

| Method | Suffix | Request schema | Success schema/status |
|---|---|---|---|
| POST | `/chats/{chat_id}/contracts` | `band_create_request` | `band_http_identity`, 201, including an exact retry |
| GET | `/chats/{chat_id}/contracts/{id}/v1/identity` | No body | `band_http_identity`, 200 |
| POST | `/chats/{chat_id}/contracts/{id}/v1/view` | `band_view_request`: exactly `{}` | `projection`, 200 |
| POST | `/chats/{chat_id}/contracts/{id}/v1/proposals` | `band_proposal_body` on carrier/1 adoption; normalized `band_envelope` otherwise | `decision`, 200 |
| POST | `/chats/{chat_id}/contracts/{id}/v1/inbox` | `inbox_request`: exactly `{after,limit}` | `inbox_response`, 200 |

These names select definitions in [protocol-v2.schema.json](schemas/protocol-v2.schema.json).
Responses MUST be the portable object without an additional `data` wrapper.
Actor and clock MUST come from the authenticated context and authority clock,
never the request body. Binding-specific schemas constrain UUIDs and typed
principals while preserving the shared decision/projection field layout.
JSON Schema validates structure; membership, role completeness/distinctness,
source compilation, byte limits and atomicity require semantic checks.

### Contribution-control qualification

Band integration of `room.contributions/1` is **unqualified**. The routes above
do not specify a member-authenticated `stop_contribution` route, a
host-authenticated `fail_contribution` route, or their request/response schemas. A deployment MUST NOT advertise this capability or
claim Band contribution-control integration until those member-stop/failure routes,
its schemas and operation discovery are specified, implemented and qualified
against [contribution outcomes](contribution-outcomes.md) and
[room/client discovery](room-clients-v2.md).

Qualification MUST verify current membership and bound-member authorization,
exact contribution-scope validation, durable control receipts and exact-retry
reconciliation. Terminal host-failure controls remain a separate trusted
interface. Local engine APIs and the trusted JSON-lines pipe do not supply the
missing routes; that pipe MUST NOT be exposed directly to participants.

### Band carrier map

On adoption of [`band-contract/carrier/1`](transport.md#carrier-map-profile),
Band uses the immutable map named `band-room/1`:

```json
{
  "profile": "band-contract/carrier/1",
  "name": "band-room/1",
  "fields": {
    "id": "body",
    "session": "path:id",
    "kind": "body",
    "expected_version": "body"
  }
}
```

`path:id` is the canonical session UUID in `/contracts/{id}`, not the chat UUID
or proposal ID. The adapter supplies exactly `transport={"path:id": <session
UUID>}`; the request body cannot supply this trusted extraction context or the
configured map. The extracted session value remains untrusted proposal data,
checked against the authorized session. Authentication and the authority clock
remain separate trusted context.

`band_envelope` continues to describe the complete normalized message, with
required `session`. The adopting endpoint's `band_proposal_body` describes the
carrier-elided JSON body: it permits omission of `session` but still requires
`id`, `kind` and `payload`, and has optional `expected_version`. Existing full
bodies remain valid; if they contain `session`, it MUST match `path:id` exactly,
otherwise normalization fails as `transport_conflict`. Routes, normalized
messages, admission, fingerprints and decisions do not change. For example:

```json
{"id": "ba6faf15f71e470898830479dac30c665", "kind": "ping", "payload": {}, "expected_version": 0}
```

An adopting deployment MUST persist the full resolved map with its immutable
binding session identity and advertise it as a top-level `carrier` on identity
responses (`band_carrier_http_identity`). It MUST NOT add it to compiled
identity, caller configuration or pure reducer checkpoints. Creation retries,
restore and proposal reconciliation use the originally pinned map, not a new
deployment default. This is an adoption requirement, not a claim that existing
Band routes or clients implement carrier advertisement or persistence; the
earlier `band_http_identity` fixture shapes remain valid without that claim.

Room participation is the authorization boundary for deliberately shared rooms,
including cross-owner rooms. An unrelated tenant or principal MUST NOT gain
access from knowing IDs. There is no owner or tenant-admin bypass around the
actor's own active participation. Existing-session identity, view, proposal and
inbox operations additionally require the caller's immutable agent binding;
creation and exact creation retries instead require current owner/admin
authority, not a contract role. Every operation, including exact retries, MUST
recheck current authorization. Removal or blocking denies
identity, views, proposals and inbox reads; it MUST NOT erase accepted receipts.
Restored membership MUST NOT rewrite the original binding or contract.

In an installed remote agent, set `agent.room.authentication` to `band-api-key`,
supply its private credential file, and use the base URL through
`/contracts/{id}`. The client appends `/v1/...` and pins session, typed actor,
definition and source identities before processing input. Human authentication
continues to follow the existing human API; the agent's credential mode does
not define a new human login flow.

## Creation and immutable identities

Create accepts exactly `{id,source,config}`. `source` is the verified
`band-contract/file-source/1` object for the exact contract file
([file identity](file-identity.md)). `config` contains `bindings`,
`max_messages` and `max_bytes`; an optional `inactivity` member has no effect.
The adapter adds the trusted members that the contract's extensions need from
verified host state, never from the request body
([engine](engine.md#session-configuration-and-trusted-context)). The authority
selects the initial time and version 0.

Creation MUST require an active room owner or admin, human or agent, but MUST
NOT require the creator to be bound to a contract role. Creation authority is
separate from contract participation: the returned identity names the
authenticated creator and grants no role or access to contract facts. Bindings
MUST cover exactly the declared roles with distinct, active agent participants. Compilation MUST validate the complete source, including its
mandatory [contract clock](contract-dsl.md#mandatory-contract-clock), before
storing a session. Both behavior and source identities
MUST be fixed for that session; editing documentation can change source identity
even when behavior identity stays the same.

The global session ID MUST be unique across rooms. Under concurrent creation,
the authority MUST serialize claims to that ID as well as room/session writes.
An exact retry succeeds only after current create authorization and binding
checks, and only when the canonical original `{id,source,config}` agrees. JSON
whitespace/key order is irrelevant; source file bytes, configuration and
bindings are significant. Retrying creation MUST preserve the original time,
state, versions, receipts and quotas. Current creation authorization and
binding validation MUST finish before checking a global ID conflict. A
conflicting request, including a claim in another room, returns HTTP 409 with
a bounded generic conflict error; it MUST NOT replace the existing session or
reveal its room, source, bindings, configuration, state or receipts.

**Known existence disclosure:** keeping caller-chosen globally unique UUIDs
means an authorized creator can distinguish a fresh ID (201) from an already
claimed ID (409), including a claim in another room. This binding does not
promise to conceal that existence bit after creation authorization. An
unauthorized caller MUST receive the same authorization refusal before any ID
lookup, whether the UUID is claimed or not. Removing the authorized existence
oracle needs a separately approved change to ID allocation or room-scoped
uniqueness; this revision does not silently change either identity contract.

## Authority and visibility

Authority transactions lock the room, authenticating participant and session in
that order. Creation additionally validates/locks binding memberships in a
stable order and serializes the global session-ID claim. A proposal MUST restore
the latest validated checkpoint, apply the persisted clock floor, advance
deadlines, then run the engine's admission order. A local preflight cannot grant
admission or reserve a version.

A new acceptance reserves the existing platform room-message quota. Its ordinary
chat message, ordered receipt and private portable checkpoint MUST commit in one
database transaction before acknowledgement. Failure MUST roll all three back
and release a failed reservation. Exact retries recover the original decision
without another chat message or quota reservation, even after session termination.
Before replying with any engine decision (`accepted`, `duplicate` or
`rejected`), the authority MUST durably persist the resulting session state,
including elapsed deadline changes and the advanced clock floor. A rejection
or duplicate creates no new accepted receipt or chat message. If persistence
fails, return a nonauthoritative storage failure instead of the engine decision.
Nonacceptance does not authorize deleting earlier receipts.

The stable `(session,proposal-id)` pair links a decision to its chat receipt.
The database additionally retains the concrete chat-message ID; current wire
decisions do not add that field. The normal `system` chat record says
`Contract event accepted.` Its contract metadata includes only session,
`receipt_handle` and definition digest. The authority derives `receipt_handle`
as lowercase hexadecimal SHA-256 of the UTF-8 bytes of the canonical session
UUID, a literal `|`, and the exact proposal ID (`SHA-256(session|id)`). The raw
caller-chosen proposal ID MUST NOT be copied into shared system metadata, room
history or notifications. The private `(session,proposal-id)` pair and chat ID
still provide reconciliation; the opaque shared handle grants no fact access.
Payload and attached contract documentation MUST NOT enter shared room history
or notifications. Qualification MUST submit a private canary in the proposal ID
and prove it is absent from shared records/notifications while the expected
handle is present, as required by `BAND-003`. The sender/recipient role
view and recipient-filtered inbox carry the actual facts. Ordinary chat text
and caller-written metadata MUST NOT create an accepted fact.

Notification uses the existing chat delivery path after commit. Authority
functions MUST reject an outer transaction that could roll back after a direct
notification. Notifications are hints: the durable inbox recovers missed
notifications and remote-host downtime. Programs, models, repair execution and
their credentials stay on remote hosts; Band compiles and interprets the
declarative contract only.

## Inbox and reconnect

Accepted receipts receive increasing, durable per-session scan positions under
the database lock. `after` and the returned `cursor` are safe integers in
0–9,007,199,254,740,991; 0 is the position before the first record. Their meaning
is opaque to clients: they are not engine versions, proposal/chat IDs, timestamps,
array offsets or counts of visible messages. Gaps are permitted. A client MUST
retain its last successful cursor with the pinned session and actor, use 0 only
to start/replay that session, and never calculate or reuse another session's
position. Authority-internal records MAY occupy scan positions but MUST NOT
become accepted events.

The authority MUST retain a durable per-session high-watermark (highest allocated scan
position, initially 0). After current authorization, it MUST compare `after`
with that high-watermark in the same serialized snapshot used to capture the page.
After input validation, `after > high_watermark` returns HTTP 409 with the body
`{error, code:"cursor_ahead"}` and without a cursor or events: it MUST NOT clamp,
acknowledge a future position, or wait for future acceptances to reach it. The
code is the shared [`contract-room/inbox/1`](transport.md#inbox-cursor-domain)
signal: a client holds and reconciles only on it, and any other 409 is an
ordinary failure.
Malformed or unsafe cursors and invalid page limits remain HTTP 400, including
when the supplied position is also ahead of the high-watermark.
`after == high_watermark` returns an empty page with the unchanged cursor. Otherwise
scan exclusively after `after` in increasing position order. `limit` bounds
records scanned (1–128), including hidden receipts and internal records, not
the numeric distance between positions or the number of returned events.
A response's `events` contains only original accepted decisions whose recipients
include that actor. The sender can recover its own receipt by exact proposal
reconciliation and authored facts through its role view.

The cursor MUST advance across scanned hidden records, so an empty advancing
page is valid. Visible decisions count toward the 2 MiB page-event bound:
the sum of their individual canonical protocol JSON UTF-8 byte lengths, excluding
the outer response/array punctuation. The separate 4 MiB response cap still
applies. Exactly 2,097,152 event bytes fit; hidden records consume no event bytes.
If a later decision would overflow the page, stop before that decision and
leave it for the next page, retaining the last included/scanned position even
if intervening records were hidden. If the first visible decision cannot fit,
return HTTP 503 without a cursor or events, even after scanning hidden records;
never skip it or continue to a later fitting decision. Never infer a missing
fact from a gap in sequence.

Restart, checkpoint restore, journal compaction and verified terminal archival
MUST preserve the session's positions, durable high-watermark, original receipts and
authorized paging/reconciliation. They MUST NOT renumber/reuse positions, reset
the cursor origin, invalidate a saved cursor, or erase an unread receipt to free
capacity. With unchanged history, actor, `after` and `limit`, replay returns the
same original accepted IDs and cursor. Physical relocation is permitted only
with equivalent retained reads. If the authority cannot recover that mapping
or serve the retained archive, it MUST fail closed (503), not return a successful
empty page. Sequence exhaustion also fails closed without evicting receipts.
Explicit room hard deletion ends retention as specified below; archival does not.

The shared page body is exactly `{cursor,events}`. The standalone binding's
required journal `head` extension is absent from Band responses.

The [portable inbox vectors](conformance/README.md#inbox-paging-vectors) exercise
these semantic obligations separately from engine conformance. Their oracle
check does not qualify live Band storage, authorization, compaction or archival.

A receiver MUST durably record each input before advancing its local cursor.
It MUST deduplicate `(session,id)`, confirm authenticated facts and reconcile
any pending outbox before producing replacement output. A lost proposal reply
requires resending the exact original envelope, including `expected_version`;
the returned duplicate contains the historical acceptance, not current state.
Use a fresh role view for current legal moves and deadlines.

## Room lifecycle and deadlines

| Condition | Required behavior |
|---|---|
| Room active; caller active and bound | Existing-session operations permitted subject to role/admission; create additionally requires owner/admin authority (see creation) |
| Room inactive but retained | New creation and proposals refused; authorized reads may inspect existing sessions |
| Caller removed or blocked | Every contract operation refused, including receipt reconciliation and creation retries |
| Active caller unbound | Existing-session identity, view, proposal and inbox operations refused; owner/admin creation and exact creation retries do not require a role binding |
| Room soft-deleted | All contract access refused immediately |
| Room hard-deleted | Sessions and receipts cascade with room deletion; durable-idempotence retention ends with this explicit scope deletion |
| Individual chat record deleted | Accepted contract receipt remains; its chat pointer may become null |
| Session terminal | Authorized views/inbox and exact receipt retries remain; a new proposal is rejected by the engine |

Every successful identity, view or inbox operation, and every proposal operation
returning an engine decision (accepted, duplicate or rejected), MUST durably
advance elapsed deadlines and persist the resulting clock floor before replying.
Clock observation
uses `max(authority_time,persisted_floor)` and MUST NOT renew any clock merely
because the session was read. Historical decisions remain unchanged. The
constant identity member `ready:true` is not a health or active-session signal.

Quiet sessions MUST also reach their deadlines without requests. The reference supervised worker
selects at most 100 due IDs per pass and loads one checkpoint at a time under
`FOR UPDATE SKIP LOCKED`; the next pass starts one second after completion.
Concurrent nodes MUST serialize expiry safely. Engine timestamps record the
actual deadline, not later processing time. Corrupt checkpoints fail closed,
report failure and rotate behind other due work without altering authoritative
state. The observed reference gap for metadata/inbox reads is recorded in the
[integration ledger](band-integration.md#evidence-and-remaining-implementation-work);
it does not relax the required deadline behavior.

## Bounds, failures and operation

The binding MUST cap requests at 768 KiB and body reading at an absolute
five seconds. It accepts JSON objects and rejects duplicate fields, floats,
unsafe integers and caller-supplied authority fields. The canonical source
object is capped at 512 KiB, and the file inside it at the
[file identity](file-identity.md) bounds. A room retains
at most 256 unarchived sessions; terminal sessions count until verified archival.
Under the [retention policy](operations.md#storage-capacity-and-retention), the
authority MAY release a terminal session's slot only after atomically verifying
an immutable source/configuration, snapshot/history and receipt archive with
equivalent authorized reads, exact reconciliation and stable inbox cursors.
Archival MUST preserve the global ID claim, accepted receipts and their
reachability; active sessions and unresolved effects MUST NOT be archived to
free capacity. A new creation at the cap returns non-retryable HTTP 409
`{"error":"room_session_limit"}`; retrying without a verified slot release
cannot succeed. An authorized exact creation retry uses its existing claim and
MUST NOT consume a slot or fail solely because the room is now full.
Configuration permits
1–10,000 messages and 1–65,536 canonical envelope bytes. Engine checkpoint/view
and inbox bounds also apply. Limits MUST fail closed without evicting receipts.
Qualify these bounds jointly as required by [operations](operations.md).

| Response | Meaning and caller action |
|---|---|
| 200 `accepted` | Durable new acceptance; record returned receipt before completing the host turn |
| 200 `duplicate` | Original acceptance recovered; no new accepted message |
| 200 `rejected` | Definitive engine decision; retain history and use a new ID for any revised attempt |
| 400 | Invalid JSON/nonobject body, or malformed create/view/inbox input; fix input |
| 401/403 | Existing Band credential/access refusal; restore authorized access |
| 404 | Disabled capability, unavailable room/session or unauthorized/unbound membership; create also uses this for an inactive room or insufficient room role |
| 408 | Request body deadline expired |
| 409 | Immutable create-ID conflict, inactive-room proposal, valid inbox cursor ahead of the durable high-watermark (`code:"cursor_ahead"`), or `room_session_limit` on new creation; the capacity error needs a verified slot release, not blind retries |
| 413 / 415 | Body too large / unsupported media type or content encoding |
| 422 | Invalid source or role bindings |
| 429 | Platform room quota refuses a new acceptance |
| 503 | Storage, recovery, checkpoint/projection/response capacity unavailable |

Controller-generated errors contain a bounded `error` string; the ahead-of-head
inbox 409 also carries `code:"cursor_ahead"`. Existing
authentication middleware retains its own error shape. The response cap is
4 MiB and responses use `Cache-Control: no-store`. A timeout, lost connection or
nonauthoritative error does not establish whether a previously attempted effect
committed. Keep the exact outbox envelope until destination reconciliation
resolves it; current loss of authorization is not evidence of historical rejection.

Proposal validation reached through the engine returns HTTP 200 with a rejected
decision, including malformed envelope fields or a mismatched session. A schema
failure does not by itself determine the HTTP error code: distinguish JSON
decoding and endpoint input checks from the engine's admission decision.

## Installation and compatibility

Apply the additive session/receipt migration before enabling
`CONTRACT_ROOMS_ENABLED=true`; the feature defaults off. PostgreSQL backups MUST
include both tables and linked room history. Application rollback MUST preserve
current accepted database history. An old backup MUST NOT overwrite effects
accepted after that backup.

Generated OpenAPI SHOULD derive Band requests and shared responses from the
pinned normative schema. OpenAPI 3.0 cannot express tuple positions: duration
tuples retain exact `x-prefixItems`, item alternatives and length, while the
normative schema/compiler enforce ordering. Human contract routes remain outside
the existing public-human allowlist. The reference source and Unicode data are
pinned in `vendor/contract-source.json`; refresh from an immutable source commit
and verify every hash before qualifying the resulting Band build.

Specification revision, engine identifier, wire profiles, source/definition
digests, vendor source commit and deployed application commit are different
identities. Record all relevant ones in integration evidence. A newer document
or schema bundle is not proof that Band has consumed, merged or deployed it.
