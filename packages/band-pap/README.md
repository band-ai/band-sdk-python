# Band PAP integration

Development package for a generic Personal Agent Protocol bridge into Band's
agent collaboration and communication layer. The target includes outbound
company participants and an inbound gateway, with independent connections in
one Band room. It has no agent-framework dependency or business-policy engine.

This is not a usable PAP adapter or server yet. Current primitives include conversation/event/Operations wire models,
capability selection, bounded HTTPS discovery with domain/issuer verification,
and SQLite connection, conversation, message, event, cursor and operation state.
Authentication, forwarding, gateway endpoints and operation execution remain
unimplemented. Discovery and storage inputs do not grant authorization.

The discovery client connects only to validated public numeric addresses and
verifies TLS against the requested hostname. Redirects are revalidated; no
ambient proxy, cookies or credentials are used. Compressed discovery responses
are rejected, and caching is not yet implemented. An authenticated endpoint
client must enforce its own destination and credential rules in later phases.

The store is synchronous: keep its short local transactions off latency-sensitive
async execution paths, and never hold a transaction across network I/O. Use one
handle per serialized execution context; independent handles/processes coordinate
through SQLite. Protect the database and its WAL/backups as private data. It holds
protocol content and grant references, not tokens. Durable inbox/outbox state
alone does not establish exactly-once delivery into Band.

Scope comprises tenant, client, company issuer, user and direction. A room is
routing data, never the owner or deduplication boundary. Hosts must resolve these
inputs from authenticated grants, revalidate authority before network dispatch,
and verify the original binding before exposing a deduplicated response to a
participant. A revocation recorded in this store fences subsequent claims and
settlements; it cannot recall a request already in flight.

`settle` accepts only valid 201/202 message acknowledgments. Use `RetryFailure`
with the resolved retry time for a known retryable refusal; use settlement without
a response for uncertain acceptance. Both retain the same message ID. Workers
must supply a consistent wall-clock time across processes and act only while
their lease is current. Operation snapshots are authenticated observations;
recording one never authorizes or executes an effect.

The maintained contract and completion ledger are in the repository's
`docs/pap/contract.md` and `docs/pap/implementation-ledger.md`. Both independent
Sol reviews must be resolved for every ledger item before it is complete.
