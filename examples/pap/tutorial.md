# A worked PAP host factory

The runnable example connects one personal client to two company peers through
one Band room. The client uses separate authorized sessions and proxy identities
for its company connections; both belong to the same demo owner. Each downstream
company receives a different pairwise user identity. The PAP
integration transports and authenticates communication. A small example host
chooses the initial recipients and supplies deterministic company replies; those
choices are not adapter policy.

## Run it

From the checkout root:

```sh
uv run --no-project --with . --with ./packages/band-pap python -m examples.pap.demo
```

The command uses the local packages. It needs no configured account, Band key,
model or running server. Dependency installation may access package indexes;
the demonstration itself opens no network connection. Running it again creates
an independent temporary state directory. Both successful and failed runs close
clients/databases and remove that directory.

The output is JSON with a room transcript, a result for each company, denial
checks and cleanup evidence. The transcript contains addressed display text;
private structured records and reusable credentials are excluded. Each company
gets its own request and returns its own correlated reply. Neither company is an
implicit observer of the other's traffic.

## Read the composition

Start at [demo.py](demo.py), then [host.py](host.py) and
[simulation.py](simulation.py). The example has no dependency on test fixtures.

`build()` is the included synchronous ASGI factory. It returns an application
whose lifespan constructs the demo host and releases its resources. The scenario
driver enters that lifespan and calls the host, which makes authenticated requests
through the composed gateways. The factory also exposes `/demo/run` for the same
offline scenario and mounts the authenticated central PAP gateway at `/`. Importing the module does not start a server or create credentials.

The host composes the same protocol components a deployment uses:

1. Create SQLite state and encrypted credential stores, with keys scoped to the
   temporary demo lifetime. Register exact demo identities and grants.
2. Create an inbound `PAPGateway` with a `JWTAuthenticator`. The resolver accepts
   only registered sessions. `GatewayBridge` forwards each authorized request
   into `RoomOutbox` for its exact room, proxy and recipients.
3. Create one outbound `PAPAdapter` and `ConversationWorker` per company, with
   separate company-scoped OAuth credentials and grants. The simulated room hands
   an addressed input only to the selected company proxy.
4. Read the private incoming record using the recipient's authenticated context
   and stage the structured input through `Grants.stage`, retaining the original
   Band message identity. The adapter does not infer credentials from chat text.
5. Send the request through `OAuthClient` to that company's PAP gateway. JWT and
   DPoP verification execute normally even though HTTP runs in process. The
   example company generates its deterministic response.
6. Project the response back into the same room. The host reads the authorized
   private record and stages `ReplyBinding` for the original inbound conversation.
   `PAPGatewayAdapter` verifies source and mention before publishing the reply.
7. Read the resulting PAP events as the original external caller. The two
   company connections retain separate conversations, credentials and recipients.

The demo explicitly seeds its fixed discovery documents and short-lived access
tokens. It exercises token/proof verification, but does not run OAuth token
issuance, account UI, refresh or network discovery. Those paths have separate
protocol tests and require actual issuer configuration for live acceptance.

The demo uses explicit bounded pumping to make the transcript reproducible. A
production host uses continuous workers and authenticated Band event delivery;
[deployment.md](../../docs/pap/deployment.md) describes `application(...)` and the
resource ownership rules.

## What is real, and what to replace

| In the example | Production host responsibility |
|---|---|
| Real PAP models, gateway routes, adapters, outbox, SQLite and JWT/DPoP validation | Retain these integration components and the pinned protocol contract |
| In-process HTTP transport with fixed example metadata | Verified discovery, HTTPS and the actual company issuer/client metadata |
| Ephemeral demonstration token issuance and exact registered-session resolver | Existing identity provider, live session/account/revocation lookup, consent and account-linking UI |
| Simulated Band room with independently registered identities and explicit delivery | Separate real Band credentials and SDK agents; load platform configuration from `.env.test` / `agent_config.yaml` |
| Fixed room membership, grants and record readers | Current application-authorized room membership, roster/history visibility and source/reply grants |
| Explicit staging from received message IDs | Trusted host capabilities for structured input and reply correlation; never accept caller-supplied actor identity as authority |
| Deterministic company response | The company's own agent or service; reasoning, approval policy and business effects remain outside the adapter |
| Temporary databases and keys removed together | Persistent protected databases and separately managed keys restored together, plus retention and backups |
| Bounded scenario driver | `application(...)` lifecycle, independent pumps, monitoring and explicit uncertainty recovery |

The example factory is an offline teaching host. It is not the deployment factory
for real credentials. The supplied `serve.py` launcher accepts a production host's
factory through `PAP_APP_FACTORY`; that factory constructs the real clients and
returns `bandpap.application(...)`. The [composition guide](README.md#compose-a-deployment)
lists each required object, and the [deployment guide](../../docs/pap/deployment.md)
explains account, key, visibility and recovery obligations.

## Verification boundaries

The example checks both request/reply paths and rejects forged identities,
unregistered sessions, cross-peer delivery and private-record access. Its
automated acceptance tests are in `tests/pap/test_demo.py`; the broader protocol
and failure suite is in `tests/pap`.

Simulated delivery cannot establish live platform history or roster privacy.
The example does not contact Target, Best Buy or another public counterparty,
exercise OAuth account UI, approve purchases or certify production throughput.
Live acceptance and the private-core coverage workflow remain recorded in the
[implementation ledger](../../docs/pap/implementation-ledger.md).
