# PAP deployment, ownership and recovery

Band remains the collaboration and communication layer for agents. `band-pap`
connects protocol participants to the same Band room; it neither invokes a model
nor decides business policy. It implements PAP draft 0.1 and Operations 1 against
the [pinned contract](contract.md). The package is a development integration;
[the ledger](implementation-ledger.md) records independent reviews and release gates.

## Components and host responsibilities

| Component | Integration behavior | Host supplies |
|---|---|---|
| `PAPAdapter` and `ConversationWorker` | Accept explicit mentions from authorized sources, persist input, settle the SDK turn, send/retry and poll independently per grant | One Band identity per company proxy; verified discovery; connection/account resolver; source and reply grants |
| `PAPGateway` and `GatewayBridge` | Authenticated conversation endpoints, durable messages/events, JSON polling and bounded SSE, handoff/direct/close, dispatch into an addressed Band outbox | Configured company identity; existing OAuth service; current session/grant authorization; allowed participants |
| `PAPGatewayAdapter` and `GatewayReplies` | Publish authenticated Band replies to explicitly correlated external threads | Trusted reply binding from original Band message ID to the PAP conversation; never inferred from text |
| `OAuthClient` and `JWTAuthenticator` | Client/session assertions, DPoP proofs, token/nonce/replay checks, direct/device/mediated sign-in, rotation/revocation, exact MCP Bearer restriction | Persistent signing identity, encryption key, OAuth callback/device UI, authoritative current account/session lookup |
| `Operations` and `OperationsClient` | Revision-bound proposal/confirm/cancel, durable effect identity, unknown-outcome recovery, approval rechecks before dispatch | Current authority/scopes, independent approval evidence, business execution, durable effect deduplication and cancellation fencing |
| API/browser channels | Constrained OpenAPI calls, MCP Streamable HTTP, controlled-browser session assertion | Explicit allowed API servers/operation IDs; permitted tool capabilities; trusted browser owner |
| `application(...)` | One ASGI lifespan for gateway worker, separately credentialed SDK agents and per-proxy room pumps | Built agents/bridges and final REST/database cleanup callback |

The gateway deliberately delegates account authentication and OAuth token issuance
to the configured issuer. Its discovery points to that service's actual endpoints;
it does not implement an identity provider or invent account credentials. The
mandatory resolver checks live ownership, scopes and revocation after cryptographic
validation. Historical `Access` snapshots stored with durable work do not authorize
it by themselves. Host grant callbacks must revalidate current authority.

`Connection.current_session` returns the **current** authenticated PAP `Session`,
including the bound account ID. The worker compares it to its own credential
session and signed-in state and binds local history on account use. After sign-out
or a switch to another account, the old account's history cannot be projected.
There is no cross-user account-sharing authority inferred by the integration.

## One room, independently addressed peers

Provision a distinct Band credential for Target, Best Buy, and every inbound
personal-agent proxy. A source grant selects the acting tenant/user/client,
company issuer, connection, conversation, exact reply recipient and allowed
roster. A structured mention selects delivery; it never grants access to another
user's connection. Display names, textual `@mentions`, reference-kind mentions,
self echoes and unaddressed historical messages cannot select credentials.

Multiple conversations can share a source/proxy pair. In that case the host must
stage the structured command with its original Band message ID so routing is
unambiguous. Direct conversations use a separately authorized human source in the
same room and retain the parent's account ownership. The parent pauses incoming
messages while its direct child is open; close propagates through linked state.

Structured payloads and operations are private records. Ordinary text and an opaque
record reference travel in addressed Band messages, never raw unfiltered event
metadata. `RoomOutbox.record` checks the authenticated actor, room, grant and current
authority before returning the record. `GatewayReplies.stage` is a trusted host
capability, not an endpoint that accepts arbitrary client-supplied identity.

The integration projects roster snapshots and updates through current grants.
It never exposes Band credentials or unrestricted room APIs to PAP peers. This is
an integration-level visibility boundary. Platform enforcement for a remote peer
that holds direct Band credentials requires separate live proof; it is not claimed
by these tests.

## Persistent state and credentials

Use a persistent local SQLite database, with its WAL and backups protected as
private conversation data. Use one serialized handle per event-loop execution
context. Transactions are short and contain no network awaits; network pumps are
independent. SQLite locking coordinates separate handles/processes; it is not a
multi-region database or a license to place the WAL on an unsupported network
filesystem. Move database calls behind an application storage boundary if the host
requires strict event-loop latency guarantees.

`SecurityStore` encrypts private keys, account tokens and temporary sign-in state
with a host-provided Fernet key. Keep that key outside the database and restore it
with the same database. Pairwise user IDs are random, persistent and scoped to
company issuer and client; they are not names or hashes of PII. JWKS expose public
verification material only. No reusable credential appears in a Band record.

Host deployment must set retention, access controls, backups and key rotation.
Keep the pinned protocol/library lock and deploy package/SDK versions together.
Draft capability changes need explicit negotiation; an unknown extension never
silently enables a channel.

## Recovery ledger

| Observed condition | Durable behavior | Recovery |
|---|---|---|
| Lost PAP first-message response | Immutable message ID remains uncertain; retry recovers the company's original conversation | Automatic PAP retry with the same ID |
| PAP 429 / transient refusal | Message retained with `Retry-After`; company connections remain independent | Resume when retry time is reached |
| Permanent PAP message refusal | Connection pump blocks, records error and queues an addressed notice | Host resolves the prerequisite and calls `ConversationWorker.retry(grant_id)`; do not change an accepted message's content |
| Cursor expires | Gap recorded; read restarts from available history; user gets a missing-history notice | Deduplicate retained event IDs; never invent missing events |
| Event persisted before Band projection | Cursor/events survive restart; replay accepts the same outbox identity once | Automatic local projection replay |
| Band POST times out or process dies around send | Outbox remains `uncertain`; later deliveries for that grant wait | Host supplies authenticated remote acceptance ID or confirmed absence to `RoomOutbox.reconcile`; then resume |
| Token refresh may have rotated remotely | Credential claim is fenced; ambiguous refresh disables reuse | Host resolves/reconnects explicitly; never race or blindly reuse the old refresh token |
| Account revocation fails or its response is lost | Disabled credential record retains encrypted revocation material across restart | Retry `OAuthClient.revoke`; constructor cannot revive access |
| Gateway durable handoff loses current session authority | Work stays blocked without stopping other owners | After authenticated renewal, call `PAPGateway.resume(fresh_access, conversation_id)`; revoked grants remain denied |
| Room removal / interruption | Adapter pauses forwarding; queues stay durable | Host explicitly resumes the grant after checking membership |
| Business effect outcome unknown | Operation stays `in_progress` with stable effect key | Host executor `recover` reconciles that key; never create a replacement effect |

The released Band message API has no supported caller-supplied message ID or
idempotency key. This integration therefore cannot prove exactly-once remote Band
posting after an ambiguous result. It provides durable acceptance, duplicate
suppression before sending, explicit uncertainty and authenticated reconciliation.
Retries are disabled on that REST POST. Do not treat “no response” as confirmed
absence. PAP acknowledgment and SDK turn settlement mean accepted communication,
not delivery completion, approval or business success.

Revocation fences checks before dispatch, including after token refresh and Band
identity lookup. It cannot recall an external request that was already in flight.
The host executor must implement the same rule for business effects and honor its
stable idempotency key across execute/recover/cancel.

## Supported channel boundaries

- Conversations use bounded JSON polling outbound and JSON/SSE inbound. SSE deltas
  are never executable input. The gateway bounds body sizes, wait and stream time
  and rechecks authority while waiting. Streaming is optional in PAP.
- OpenAPI supports declared JSON operations and explicit host-approved servers and
  operation IDs. Unresolved references, unsupported parameter/body formats and
  server variables fail explicitly. It is not a general credentialed HTTP proxy.
- MCP implements the pinned `2025-06-18` Streamable HTTP handshake, tools list/call,
  JSON and bounded SSE responses, session/version headers and close. Bearer tokens
  are restricted to the exact advertised MCP endpoint. It does not replay tool
  calls after uncertain responses or automatically recover a lost tool session.
- Controlled browsing emits a short-lived form assertion to a trusted browser
  owner. The host drives the browser; the integration does not automate user
  sign-in or reveal assertions in agent-visible results.
- Incoming operation HTTP endpoints are available only when an Operations engine
  and approval-evidence interface are configured. Proposals remain application
  decisions; generated text and `approved_by` claims alone never authorize them.

## Deployment and evidence

See [the launcher and composition guide](../../examples/pap/README.md). Configure
HTTPS ingress with the same public origin used for DPoP. Keep secrets, approval
evidence and private request bodies out of access logs and metrics. Observe
`ConversationWorker.status`, `RoomOutbox.health`, operation states and gateway
blocked work through host-authorized monitoring; those counters contain no payloads.

The offline suite exercises real cryptography, generated REST request encoding,
ASGI endpoints and SQLite reopen/failure behavior using deterministic transports.
It does not certify a public company's PAP service, DNS/TLS deployment, live Band
participant visibility or production throughput. The implementation worktree has
no `.env.test` or `agent_config.yaml`, and no authorized live PAP counterparty was
configured. Those release checks remain explicitly blocked by deployment inputs.
