# Run the PAP integration

The implementation lives in the separate `band-pap` distribution. It uses the
published Band adapter/REST interfaces. The host supplies authenticated identities,
room grants, account consent and business effect handlers; the package does not
invent those authorities or choose a coordinating agent.

During development, install this workspace, then run:

```sh
uv sync --all-packages --extra dev
PAP_APP_FACTORY=myapp.pap:build uv run --all-packages python examples/pap/serve.py
```

`myapp.pap:build` is your application's factory, not a bundled permissive service.
It returns `bandpap.application(...)` or another ASGI application that composes the
same lifespans. The server binds loopback by default. Terminate TLS at your ingress
and preserve the configured public HTTPS URL; the gateway constructs DPoP targets
from that configured origin, not caller-controlled forwarded headers. Access logs
are disabled so authorization codes and private query values are not recorded.

The host factory constructs these objects:

1. A persistent `StateStore` and `SecurityStore` with a separately managed Fernet
   encryption key. Restore that key across restarts. Never generate a replacement
   key while reusing the credential database.
2. An issuer `JWTAuthenticator` with public verification keys, exact audience and
   a mandatory resolver that checks current sessions, account identity, scopes and
   revocation. Configure the existing OAuth service through `GatewayConfig.auth`
   and `oauth_metadata`. The gateway is not an account/login identity provider.
3. `GatewayBridge(outbox=..., authorize=...)`, `PAPGateway(...)`, and
   `PAPGatewayAdapter(...)` instances for independently credentialed inbound
   personal-agent proxies. The host authorizer resolves callers to existing,
   authorized room participants and applies current membership/visibility rules.
4. For each outbound company: verified discovery, an `OAuthClient`, a host
   `Connection.current_session` resolver, a persisted `Grant`, and a `PAPAdapter` /
   `ConversationWorker`. Register each connection in `Connections`. Stable user
   pseudonyms come from `SecurityStore.pairwise_user`, never names or email hashes.
5. One Band `Agent` per proxy using its own credential. The identity passed to
   `RestRoomSender` must match that proxy; the sender verifies `/me` before posting.
   Read platform credentials/URLs from the repository's `.env.test` and existing
   agent configuration when running against a real Band deployment.
6. Return `application(gateway, agents=agents, bridges={proxy_id: bridge, ...},
   cleanup=close_host_resources)`. It starts gateway dispatch, Band participants
   and independent bridge pumps, then cancels pumps before closing resources.
   The cleanup callback closes REST clients with `aclose_rest_client` and then
   database handles. Outbound adapters own their conversation worker lifecycle.

Use `RestRoomSender(..., authorize=...)` and `RoomOutbox(..., authorize=...)` with
the same live host authority. That callback must verify the stored grant, exact
room/proxy/recipients, current membership and bound account. It runs again after
identity lookup, immediately before posting. Do not pass a permissive callback.

Ordinary addressed text needs no PAP-specific syntax. To send structured input,
the host stages `SendMessage` with `Grants.stage`; its ID is
`stable_id(original_band_message_id)`. `PAPAdapter` verifies the authenticated
source and explicit structured mention before consuming it. Multiple threads
for the same source/proxy require this explicit binding; ambiguous input is denied.

Company replies carry an opaque private-record reference in the addressed Band
message. A host capability calls `RoomOutbox.record(reference, room=..., actor=...)`
using authenticated caller context. The reference alone grants no access. The
record contains the PAP conversation ID. Before publishing a Band reply to an
inbound PAP client, stage `ReplyBinding` in `GatewayReplies` with the original
Band message ID, exact source/room/proxy and current authenticated `Access`.
`PAPGatewayAdapter` verifies source provenance and the mention, then publishes to
that thread. This supports several PAP conversations in one room without guessing
correlation from text or copying raw room history.

For verification without credentials or inference, run `pytest tests/pap -q`.
`test_roundtrip.py` exercises real adapter/auth/ASGI logic with deterministic HTTPS
and Band transport doubles. It is not live interoperability evidence. See
[deployment and recovery](../../docs/pap/deployment.md) and the
[implementation ledger](../../docs/pap/implementation-ledger.md) for release gates.
