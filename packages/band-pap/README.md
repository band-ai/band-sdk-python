# Band PAP integration

`band-pap` connects Personal Agent Protocol participants to Band's collaboration
and communication layer. It provides outbound company adapters and an inbound
company gateway, independent participants in one room, explicit addressed routing,
private structured records and durable protocol work. It has no model-framework
dependency and does not choose a coordinator or business policy.

Implemented interfaces include `PAPAdapter`, `PAPGateway`, `PAPGatewayAdapter`,
OAuth/DPoP and account-linking clients, Operations, constrained OpenAPI/MCP/browser
channels, SQLite state, independently credentialed Band outboxes and an ASGI
lifespan composition helper. No existing Band SDK runtime internals are modified.

The host supplies actual account authentication/token issuance, current
identity/grant/visibility checks, independent proxy credentials and trusted
approval/business execution interfaces. These are required application boundaries,
not permission inferred from a message or room ID. A fresh install does not expose
an anonymously authorized room or a default account identity provider.

This development version targets PAP draft 0.1 / Operations 1 at the pinned
revision in `docs/pap/contract.md`. The implementation ledger distinguishes local
verification from live release checks. No live interoperability certification is
claimed.

From the repository, install with `uv sync --all-packages --extra dev` and run
`uv run --all-packages pytest tests/pap -q`. A self-contained offline example runs from the checkout with
`uv run --no-project --with . --with ./packages/band-pap python -m examples.pap.demo`.
It needs no credentials or model and exercises two peers in one simulated room.
The deployment composition guide is `examples/pap/README.md`, the worked
host-factory tutorial is `examples/pap/tutorial.md`, and operational requirements
and explicit recovery rules are in `docs/pap/deployment.md`.

Persist the database and a separately managed credential encryption key. The
short synchronous SQLite transactions never span network awaits. Protect the WAL
and backups as private data. Restoring the database with a different encryption
key does not restore its credentials.

PAP retries preserve message identity. Band POSTs with uncertain acceptance are
fenced for authenticated reconciliation because the current Band API does not
expose message idempotency. An accepted message or settled SDK turn is not evidence
that a business action completed. Unknown effects retain their original operation
identity and require host recovery.
