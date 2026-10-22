# Transport and integration specification

**Client choice:** ordinary authorized agents may discover the protocol and
send proposals directly. [Room/client separation](room-clients-v2.md) is the
current product boundary. Requirements for a local checker, invocation journal,
program sandbox or repair controller apply only to the optional runtime profile;
the room's own durable acceptance and access rules remain mandatory.

Profiles `standalone-http/1` and `band-room/1` transport the same contract decisions without changing actor, clock, visibility, deduplication or commit semantics. Notifications are delivery hints. The durable authority remains the source of accepted facts and receipts.

## Shared transport rules

Remote transport MUST authenticate the service and caller, use TLS with certificate/hostname verification, enforce request/response/deadline/connection bounds, and reject ambiguous JSON or framing. HTTP is permitted only for explicit numeric loopback addresses in local development. Credentials may not appear in URLs, program/model input, journals, traces or application logs. A URL cannot supply username/password, query parameters or fragments as hidden configuration. Redirects are not followed with credentials.

An authenticated actor comes from the transport's validated principal, never from an envelope field. Authorization is specific to service, room membership and operation. A valid token for another room/actor does not authorize a proposal. The contract's role binding is necessary but not a substitute for current Band membership/tenant authorization. Revocation must prevent new access without rewriting previously accepted receipts.

A connection deadline covers DNS, connect, TLS, headers and body, including slow continuous trickling. Idle socket timeouts alone are insufficient. Servers bound concurrent workers and request bytes; clients bound response bytes and total time. JSON object bodies reject duplicate keys, floats/nonfinite or out-of-range numbers, invalid UTF-8 and excessive nesting. Exactly one `Content-Length` is required for POST; `Transfer-Encoding` is unsupported. Responses use one content length, `application/json`, no-store caching and a closed connection in the reference transport.

A transport failure or nonauthoritative server error does not establish rejection. Keep the exact staged effect and reconcile its original ID. Never refresh expected version while retrying the same ID: that changes the fingerprint. A definitive engine rejection is a normal decision and can trigger a new bounded attempt under a new identity.

## Carrier map profile

`band-contract/carrier/1` is a declarative map owned by a transport binding,
not by the contract. It lets the same contract run over HTTP, Band, email or
another declared carrier without duplicating transport-supplied envelope
fields in its JSON body. This section specifies binding adoption; it does not
claim that existing HTTP endpoints or clients already implement map selection,
advertisement or persistence.

A map has exactly `profile`, `name` and `fields`. `profile` is
`band-contract/carrier/1`; `name` is the binding's immutable, versioned map name
(for example `email/1`). `fields` has only the optional keys `id`, `session`,
`kind` and `expected_version`. Each value is `body` or a nonempty source token
declared by that binding; omitted mappings resolve to `body`. `payload` and its
members MUST remain in the body. Actor, clock, role and recipients are not
carrier-map keys. Schemas `carrier` and `carrier_identity` in the
[current bundle](schemas/protocol-v2.schema.json) describe configured maps and
their fully resolved pins, respectively.

For example, an email binding can declare:

```json
{
  "profile": "band-contract/carrier/1",
  "name": "email/1",
  "fields": {
    "id": "header:Message-ID",
    "session": "header:X-Band-Session"
  }
}
```

Here `kind` and `expected_version` default to `body`. Source tokens are opaque
selectors, not executable expressions or a universal JSONPath language. Each
binding MUST declare their extraction/encoding, scalar representation,
ambiguity handling and bounds. Unsupported tokens or map keys MUST fail closed
as `transport_fields`. A binding MUST NOT select its map from an incoming
message. In particular, an arbitrary email subject cannot select a contract
kind unless that binding declares the corresponding `kind` mapping.

The adapter's normalization API takes the trusted configured `carrier` map and
a separate `transport` map keyed by the selected non-body source tokens. For
the example above, the supplied values can be:

```json
{
  "header:Message-ID": "<02b80faf231e4b13ab419b8fca544dec@example.invalid>",
  "header:X-Band-Session": "email-session-example"
}
```

The JSON body can then omit those supplied fields:

```json
{"kind": "ping", "payload": {}, "expected_version": 0}
```

The result is the complete normalized envelope with those exact `id` and
`session` values added. The supplied values are not authenticated authority:
the binding authenticates the sender separately and resolves the trusted actor.
An email header, route, notification or body MUST NOT choose the actor or clock.
The binding MUST reject ambiguous repeated headers and other multivalued
sources rather than silently selecting one. Supplied values MUST retain the
body field's type and bounds; `expected_version` is an integer, not an HTTP
header string coerced by the generic normalizer. A binding declaring a header
for it must specify an unambiguous bounded conversion before normalization.

A binding mapping `id` to a carrier source MUST ensure that other session
members cannot observe or predict that source's value before proposal admission.
An email `Message-ID` exposed to another member through CC, a list or a relay
copy does not meet this requirement: that member could pre-claim the shared ID
and cause `id_conflict` for the real proposal. In that case the binding MUST
keep a separate unpredictable proposal ID in the body or derive a distinct ID
with a host-private key, and keep that proposal ID unobservable to other members
before admission. The email map above is a local normalization illustration,
not qualification of a public `Message-ID` as a safe proposal ID. Clients MUST
durably stage the chosen proposal ID and preserve it across retries.

Only selected source tokens may appear in `transport`; extra tokens fail as
`transport_fields`. A supplied value conflicting with a body field is
`transport_conflict`, never carrier-wins or body-wins. A mapped source with no
supplied value leaves any body value intact. Missing required normalized fields
are `envelope`; a missing optional `expected_version` stays absent.

If a binding's access control depends on a mapped carrier source (for example
a session-scoped route token), the binding MUST reject a missing source as
`envelope` before normalization, not fall back to a body value.

All normal kind, payload, session and version checks still run after merging. Encoding may
omit only matching fields actually supplied by a non-body source.
`decode(encode(message, carrier, transport), carrier, transport)` MUST reproduce
the same normalized message, including optional-field presence. Raw input and
canonical normalized-envelope byte limits both apply; eliding fields does not
reduce the canonical message charge.

The non-body mappings themselves declare the allowed supplied tokens; there is
no separate `sources` option. Several fields may name one token, but its value
MUST pass each field's checks. Supplying an optional field absent from the
normalized input to an encoder is `transport_conflict`, not permission to add
that field. The reference `Wire.decode(bytes, opts)` and `Wire.encode(message,
opts)` take `carrier` and `transport` options without changing their success
shapes. Without an explicit `carrier` option, the earlier all-body and
field-keyed `id`/`session` transport API stays compatible; explicit `carrier:
nil` is malformed, not an instruction to select that legacy path.

| Normalization failure | Error |
|---|---|
| Malformed map, unsupported profile, unknown mapped field, undeclared supplied token or non-map `transport` | `transport_fields` |
| Unequal supplied/body values, or an encoder's supplied value absent from its input message | `transport_conflict` |
| Missing required field after merging, wrong field type or unknown envelope field | `envelope` |
| Floating-point or unsafe integer in body or carrier values | `unsupported_number` |
| Duplicate JSON object member at any body depth | `duplicate_key` |
| Malformed JSON/UTF-8 or trailing input | `json` |
| Raw decoder input, the canonical merged envelope or emitted wire bytes exceed the configured positive byte bound | `message_size` |

In the legacy field-keyed transport API, a key other than `id`/`session`, or a
value that is not a nonempty string, keeps its earlier `transport_fields`
error; a string value that is not valid UTF-8 is `json`. An envelope failure
at this boundary is distinct from session-only requirements in the reducer: the
shared wire codec also supports sessionless room events, which still MUST NOT
gain a session field.

### Deployment and session pinning

The deployment fixes one map for new sessions, not a choice each session's
caller makes. Before session creation, resolve all four field mappings. Persist
the complete `{profile,name,fields}` as `carrier_identity` alongside the
binding's durable session creation identity, atomically with creation. It is
binding metadata, not a new compiler input, contract digest member, reducer
configuration field or portable reducer checkpoint member.

The resolved map's name and version MUST identify immutable contents. A changed
source mapping is a new binding/map version, never an in-place edit. Restore,
reconciliation and exact creation retries MUST use the original complete pin;
they MUST NOT substitute a current deployment default or reset session time,
versions or receipts. If the pinned binding cannot be recovered and validated,
the authority MUST fail closed before readiness/admission. A deployment rollout
may retain a qualified old binding to serve its existing sessions, but MUST NOT
repin them to the new map.

An adopting binding advertises the fully resolved map in a top-level `carrier`
member of its identity response. This is separate from the nested compiler
`identity` (`definition`, `source`, `ready`). The named schemas
`carrier_http_identity` and `band_carrier_http_identity` require this member;
the existing identity schemas still accept their earlier shapes, and make no
carrier-adoption claim when it is absent. A client using elision MUST pin and
verify the advertised map before sending. Absence is not permission to guess
how carrier fields are reconstructed.

Fingerprints and receipts still depend only on the normalized message and
authenticated actor. Equivalent messages decoded by different carrier codecs
therefore have equal fingerprints, and an identical retry preserves its
original `expected_version`. This property does not authorize changing a
session's pinned map or bypassing a binding's authentication. Multiple carriers
for one session require an explicitly specified, versioned composite binding;
they are not selected opportunistically in carrier/1.

### Binding-adoption entry conditions

Before the first live binding enables carrier elision, its owner MUST qualify
the exact binding version and deployment configuration, not just the shared
normalizer. Record the named implementation/operations owner, source/build
identities, supported carrier tokens and the evidence for these entry gates:

1. **S1, merged-envelope bounds.** Exercise raw input, normalized canonical
   envelope and encoded output at and above their configured byte limits. A
   small body plus large supplied fields MUST NOT bypass `message_size`.
2. **S2, pre-admission ID safety.** Demonstrate that a different authenticated
   member cannot observe or predict the chosen proposal ID before admission.
   Public/list/relay-visible email `Message-ID` is not qualified merely because
   it normalizes successfully. Stage a distinct private unpredictable ID when
   needed and prove an attacker cannot preclaim it; preserve that exact ID and
   envelope on retry. Existing client randomness rules still apply.
3. **Deployment pin and advertisement.** Create and persist the fully resolved
   map atomically with session identity, advertise it, and have a real client
   verify it before elision. An incoming request MUST NOT select a map. Reject
   undeclared tokens, conflicting values, ambiguous/multivalued sources and
   missing access-control sources without a body fallback. Authentication and
   the trusted clock remain outside carrier values.
4. **Live authorization and concurrency.** Use the binding's actual
   authentication and current room/tenant membership checks, including revocation
   and wrong-session credentials. Serialize authorization, normalization,
   receipt lookup and commit under the authority's admission lock. Exercise
   concurrent writers and duplicate submissions through the real binding;
   equal fingerprints alone do not prove one durable admission/effect.
5. **Restore and reconciliation.** Restart with accepted receipts and a staged
   unresolved proposal; prove original-map recovery and exact retry without
   duplicate effects. Missing, changed or unsupported pins MUST fail closed
   before readiness/admission, never adopt the current default. Preserve
   expected-version presence/value and distinguish rejection from unknown
   acceptance after transport failure.

Shared codec/vector success is necessary but does not establish these live
binding gates. Until all are evidenced, continue using the existing full-body
binding and do not advertise carrier adoption. Multiple carriers for one
session remain a separately specified and qualified composite binding; these
entry conditions do not enable opportunistic map switching.

## Standalone room API

One service instance exposes one pinned session in this profile. Operators can run isolated instances for multiple sessions; a multi-session implementation must additionally scope every authorization and storage key by session. Tokens are high-entropy host-configured secrets (reference syntax: 32–1,024 ASCII characters in `[A-Za-z0-9_~+/.=-]`). Each maps to exactly `{actor,permissions}`, where permissions are drawn from `room`, `events`, `controls`, `observe`. The credential itself is not durable room configuration.

Principal kind on `durable-room/1` and `standalone-http/1` is an operator
attestation. The pinned standalone configuration has no principal kind, so the
reference room builds its [host evidence](agents-and-extensions.md#band-permission-binding-version-1)
from the pinned bindings and records every bound actor as an active `agent`
with `participant` permission. The room cannot verify that claim, so these
profiles do not enforce the rule that humans are never bound to a role. An
operator who binds a human principal breaks that rule. On `band-room/1` the
kind comes from Band's typed principal instead.

| Method/path | Permission | Body/result |
|---|---|---|
| `GET /health/live` | None | `{live:true}`; does not expose state or identities |
| `GET /health/ready` | Valid credential | `{ready:boolean}`, 200 or 503 |
| `GET /metrics` | `observe` | Bounded aggregate operational counts; no payloads or credentials |
| `GET /v1/identity` | `room` and bound actor | Capability object below |
| `GET /v1/definition` | `room` and bound actor | Immutable role definition pinned to the authenticated session and actor |
| `POST /v1/view` | `room` and bound actor | Exactly `{}`; returns that actor's compact projection |
| `POST /v1/proposals` | `room` and bound actor | Exact normalized session envelope; returns engine decision |

The standalone compact-view extension omits only `view.definition`; state, legal
rules, obligations, visible facts and the issued preflight checkpoint remain.
Its raw response follows `http_compact_projection`; the one-time definition
response follows `http_definition` in the current-profile schema registry.
Fetching the definition does not advance room time or alter the journal. Cache it
only for the pinned session, actor and contract identity. `HTTPRoom` hydrates the
full internal projection for runner/LocalGate consumers; the full engine
projection schema is unchanged. These routes do not qualify a Band deployment.

The identity response has the following shape. These example digests are
placeholders; actual values come from the pinned compiled source:

```json
{
  "profile": "contract-room/http/1",
  "session": "host-selected-session",
  "identity": {
    "definition": "0000000000000000000000000000000000000000000000000000000000000000",
    "source": "1111111111111111111111111111111111111111111111111111111111111111",
    "ready": true
  },
  "actor": "authenticated-bound-principal",
  "idempotent": true,
  "inbox_profile": "contract-room/inbox/1"
}
```

The client pins both contract identities, session and actor before serving turns. A mismatch with its local checker is a startup failure. `idempotent:true` means original receipts survive authority restart for the retained scope; it is not inferred from HTTP method or a successful health check. Unsupported/missing capabilities are rejected.

The existing standalone routes carry the complete proposal body. A standalone
binding adopting carrier/1 uses the map named `standalone-http/1` with all four
fields resolved to `body`, persists that binding pin and advertises it as
described above. The identity example without `carrier` remains a valid existing
profile response; it is not evidence of this additional binding adoption.

The `inbox_profile` member is present only when the authority offers the polling
extension. The nested `ready:true` is part of the pinned engine identity, not a
substitute for the changing `/health/ready` result. The identity schema requires
it even for an authority that omits inbox polling.

Engine accepted, duplicate and rejected decisions all use HTTP 200 because the request reached the authority and produced a decision. Authentication failure is 401, insufficient permission/membership 403, unknown endpoint 404, malformed request 400, request timeout 408, oversized body 413, agent input conflict 409, unavailable/recovering storage or engine 503, and unexpected internal failure 500. Error bodies contain a bounded `error` string without stack traces or private state. A body may also carry a stable machine-readable `code` string. One code is defined, `cursor_ahead`, and it belongs to the shared `contract-room/inbox/1` obligation: every binding that offers inbox polling, Band included, returns it on an ahead-of-head cursor ([inbox cursor domain](#inbox-cursor-domain), [Band binding](band-transport.md#inbox-and-reconnect)). Callers branch on HTTP status, a defined `code` and engine `status/reason`, never on error prose.

After authentication, unsupported method/path pairs return 404 before room
permission or readiness checks. A supported `POST /v1/view` requires an empty
object body; a nonempty object is malformed (400), not a missing route (404).
Supported routes still require their normal permission and membership grants.

An HTTP body rejected before admission has no engine decision. A 413 request
size failure, a 503 checkpoint/storage failure and an engine
`{status:"rejected",reason:"message_size"}` are different boundaries. Never
convert a generic 5xx or malformed response into an authoritative rejection.
The [Band binding](band-transport.md#bounds-failures-and-operation) has its own
status mapping under existing platform authorization; engine decision objects
and exact-resend semantics remain the same.

An exact proposal resend performs receipt reconciliation. It may return the original accepted decision as `duplicate`, including after the session terminated. A future explicit read-only receipt endpoint must preserve the same authorization and ID-conflict guarantees; it cannot weaken the exact resend semantics.

### Inbox cursor domain

`POST /v1/inbox` requires the same bound-participant `room` permission. Its exact
body is `{after,limit}`: `after` is a nonnegative safe integer cursor and `limit`
is an integer from 1 to 128 (number of journal records scanned). The shared `contract-room/inbox/1` response is exactly `{cursor,events}`;
Band MUST NOT add a `head` field. The standalone HTTP binding instead requires
`standalone_inbox_response`: `{cursor,events,head}`, where `head` is its
authoritative journal `{sequence,hash}` at page construction, not merely the
last returned event. Its `standalone_inbox_event` additionally permits `at`,
the UTC millisecond acceptance time from the committed authority request.
The reference durable authority supplies it for committed proposal records;
older standalone peers may omit it. It matches the corresponding role-visible
fact's `at`, never the inbox read time. This binding-only metadata does not
alter the original engine decision, shared inbox schema, private checkpoint,
or recipient filtering.
An `after` cursor greater than `head.sequence` returns 409 with the body
`{error, code:"cursor_ahead"}` rather than an empty successful page. Only that
code, with a string `error`, tells a host to hold and reconcile a
restored/truncated authority; it must not silently skip new facts or
automatically reset its persisted cursor. Any other 409, including one without
that code, is an ordinary request failure and never creates the hold.
Head and page are read consistently with durable room operations. This detects
an ahead-of-head cursor; it does not prove identical histories across every
backup/fork at lower cursors. Events are original accepted decisions addressed to that actor,
in durable acceptance order. Rejected/duplicate attempts, other recipients' facts,
and host records never become events. Cursor gaps and empty pages are normal;
persist an advancing cursor even when the page is empty. The journal is retained,
so polling the same cursor after restart repeats the same accepted IDs.

The cursor is an opaque per-session authority position, not an engine version,
storage offset, record count or visible-event count. 0 is the position before
the first record. Clients MUST scope saved positions to the pinned session/actor,
use only 0 or a successfully returned cursor, and never increment or calculate
one. Gaps are permitted; `limit` counts scanned records, including hidden ones,
not the numerical difference between positions.

The authority MUST maintain a durable high-watermark (initially 0) and check it in the
same serialized snapshot as the page capture. After authorization and input
validation, a request with `after > high_watermark` MUST return HTTP 409 with
`code: "cursor_ahead"` and without acknowledging any cursor or events; accepting/clamping a future cursor could
silently skip a later receipt.
Malformed or unsafe cursors and invalid page limits remain HTTP 400, including
when the supplied position is also ahead of the high-watermark.
At the high-watermark, return `{cursor:after,events:[]}`. Scan only positions strictly
greater than `after`. Restart, checkpoint restore, compaction or archival MUST
retain the original positions, high-watermark and receipts, including equivalent authorized
reads for old cursors. Recovery/capacity failure MUST fail closed, never reset or
successfully skip retained history. Explicit scope deletion ends retention;
physical archival does not. These rules are shared `contract-room/inbox/1`
obligations, not a claim that a deployment has qualified compaction or archival.

An authority offering this extension advertises
`inbox_profile:"contract-room/inbox/1"` in its identity response. Polling clients
MUST require this exact capability before starting; an older authority without
it can still serve nonpolling proposal/view clients. The reference identity's
nested `identity` carries `definition`, `source` and constant `ready:true`, matching
the private engine's startup identity. Readiness polling uses the health endpoint;
that constant is not a changing service-health signal.

The server caps the sum of individual visible accepted decisions' canonical
protocol JSON UTF-8 byte lengths at 2 MiB (2,097,152 bytes), excluding outer
response/array punctuation. Exact equality fits; hidden records consume no event
bytes. The server stops before advancing past an event it cannot include, keeping
the last scanned position including any intervening hidden records. If the first
visible event cannot fit, it returns 503 without a cursor or events rather than
silently skipping it or acknowledging the hidden prefix. The separate response
cap still applies. The host records each event durably before advancing
its own cursor. A crash between these commits redelivers the same ID through
normal controller deduplication; it cannot create a second program turn. A 202
held result is retained input, while a thrown error is not acknowledged by cursor
advancement. Polling is recovery from missed notifications, not permission to
accept unauthenticated events or skip current room verification.

The [portable inbox vectors](conformance/README.md#inbox-paging-vectors) are a
separate paging gate. Passing the semantic oracle is not native HTTP/storage or
Band deployment qualification.

## Standalone agent API

| Method/path | Permission | Body/result |
|---|---|---|
| `POST /v1/events` | `events` | Room-confirmed accepted-event notification; host independently verifies it with the authority and wakes ordered inbox polling |
| `POST /v1/controls` | `controls` plus actor's action grant | Exact `{id,action,reason}` control |

Both expose the shared health/observer endpoints. A completed or already-completed operation returns 200. A durably retained/held operation returns 202 with `status:"held"` and a bounded reason. HTTP 202 does not claim a room reply was accepted. Event delivery permission cannot issue controls; control endpoint permission does not by itself grant every action.

Push ingress (`/v1/events`, or reference `/events`) MUST use an explicitly wired
notification consumer, never call controller `receive`/`recover` or run a turn
on the HTTP handler. A missing consumer returns 503 without a direct-admission
fallback. A verified addressed notification returns 200 with
`{status:"notified"}` and wakes the ordered inbox consumer. A verified
self-authored notification returns 200 with
`{status:"ignored",reason:"self_authored"}` and performs no wake or state
mutation. These results acknowledge verification/notification, not controller
admission, a producer result or a room reply. Duplicate/lost pushes create no
turns: the durable inbox remains the sole ordered admission path, and periodic
polling recovers a lost wake.

The event body is not trusted merely because a delivery token is valid. The controller fetches an authenticated role view and checks that the fact exists, has matching ID/session/kind/payload/author role/actor, and was authored by or addressed to this participant. It stores a canonical normalized event. Conflicting duplicate event/control IDs are rejected, while identical completed duplicates return their original result.

Accepted-event bodies require nonempty string `id`, `session`, `kind`, `actor`
and `role` fields plus a canonical JSON `payload`; additional original decision
metadata may accompany them. Malformed bodies return 400 before authority
lookup. A well-formed but missing, mismatched or nonvisible fact is a definitive
409, not a retryable 503. Payload matching preserves canonical JSON types,
including the distinction between booleans and integer numbers; host-language
equality is not sufficient proof of an identical fact. Only genuine authority/view/storage unavailability
uses 503. A self-authored claim MUST pass the same room verification before
the 200 ignored result; forging the body actor cannot obtain that no-op.
Verified self-authored facts also remain no-ops at controller admission,
without input records, queue/state mutation, program calls or model calls.

The reference defaults are 64 KiB request bodies, 4 MiB client/response bodies, 8 simultaneous connections, 5 seconds for ingress and 10 seconds for a remote room request. Configured bounds are validated; the reference caps are 1 MiB request body, 128 connections and 120 seconds. Header parsing is bounded separately. A slow TLS peer must occupy one bounded worker, not block acceptance of every other connection. Shutdown drains admitted handlers before closing their backend store.

Ingress deadline setup failure (including descriptor or timer-thread exhaustion)
MUST close the accepted connection, close any duplicated descriptor and return
its connection slot. A transient setup failure must not permanently consume
ingress capacity or leave an active-connection counter behind.

## Band room profile

Band integration preserves the separation between the room authority and the agent's own client, including the optional remote host. Programs, their model credentials and repair execution stay outside Band. Band stores contract sessions, state, receipts and accepted chat messages under its existing authentication, tenant and room-participant model.

The API namespace is room-scoped and exposes create, identity, role view, proposal and inbox operations for a contract session. The [Band transport binding](band-transport.md) defines the concrete routes and authority rules; generated OpenAPI must agree with it. Named `band_*` schemas add the required UUID/typed-principal constraints to shared shapes and define the Band creation request. Current wire decisions have no extra chat-message ID field. Any future transport receipt extension requires an explicitly negotiated capability or profile and must retain the unchanged engine decision for replay. Ordinary noncontract chat may coexist, but cannot become an accepted contract fact through caller-supplied metadata. The [integration guide](band-integration.md) connects the language, room lifecycle and host recovery.

Creation requires room-authorized configuration authority. It validates the bounded exact source file ([file identity](file-identity.md)), both compiled identities, role bindings to existing agent participants (humans are never bound; see [agents and extensions](agents-and-extensions.md)), the explicit quota policy and unique session identity. Runtime source is pinned; a room message or generated program cannot replace it. Contract source and private checkpoints are not exposed to actors outside the authorized room. Private accepted payloads must also retain the engine's role-recipient visibility when returned by contract APIs.

For every request, use the actual acting identity. An agent API key or validated acting-agent context binds the agent, not the human who owns it. Principal namespaces must distinguish user and agent identities. Existing tenant restrictions and participant checks must be applied to create, identity, view, proposal, receipt lookup and replay/export operations consistently. A forged body actor or an agent ID without permission must not select another participant's role.

A transaction locks the session, reads its latest durable state and clock, advances timers, admits the proposal, creates any resulting Band chat message, and stores the updated state/receipt. It commits all or none. The published chat message must be linked to the stable accepted contract ID and decision; duplicates return the prior link without creating a second message. Storage failures cannot leave a visible message without the associated accepted state, or accepted state without its required durable message.

Socket/PubSub/database notifications occur from the committed message path. Notification loss or reconnect must not lose accepted facts: adapters recover from durable room history/contract views, deduplicate event IDs and reconcile outbox receipts before producing replacement output. A notification alone never proves acceptance or permission. Concurrent requests handled by separate Band instances must serialize through the database, with one accepted version order and no double admission.

The remote adapter pins contract capabilities and validates incoming message metadata against the authoritative contract API before passing a normalized fact to the program. It does not trust free-form chat text as protocol control. Explicit agent controls use a separate authenticated host channel/action policy; a conversation message cannot grant itself a wake, takeover, repair or resume capability.

## Required integration fault cases

Qualification must cover wrong actor, removed participant, wrong tenant/room/session, source mismatch, duplicate ID with identical/conflicting content, stale version, concurrent proposals, deadline expiry during network latency, commit failure, commit-before-reply crash, lost notifications, reconnect/history replay, bad certificates, redirect attempts, slow ingress/response, body/output capacity, server shutdown and destination restart. Band qualification additionally checks that one accepted decision yields exactly one ordinary durable chat message and that existing room authorization/notification behavior remains intact.

The installed HTTP client accepts a fixed authentication scheme: `bearer` for the standalone service, or `band-api-key` for Band. The latter sends only `X-API-Key`; it never also sends the credential as a Bearer token. Both preserve the same TLS, redirect-refusal, response framing and whole-request deadline checks. Configure an agent with `agent.room.authentication` and the Band session base URL; its actor is the typed identity returned by Band.
