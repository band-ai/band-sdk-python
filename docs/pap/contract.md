# PAP integration contract

## Product boundary

Band supplies collaboration and communication between independently implemented agents. The PAP integration adds protocol participants and authorized delivery. It does not select a coordinator, invoke a model, compare products, decide purchases, or infer approval from generated text.

The complete target remains P01–P07 in [the implementation ledger](implementation-ledger.md). Partial implementation on this draft branch is not a supported integration release.

## Package and extension boundaries

- Distribution: `packages/band-pap`, import namespace `bandpap`, independently installable against `band-sdk>=5.1.0,<6`. Tests live in `tests/pap` under normal collection. Register its source root with shared guards and type checking; add dedicated import-boundary tests and installed-wheel checks. Existing `src/band`-only conformance discovery is not proof that this package conforms; P04 must register or explicitly exercise its adapter shape.
- `bandpap` owns PAP wire data, discovery, authentication integration, scoped protocol storage, workers, and the adapter/gateway. No imports from model-framework packages.
- `PAPAdapter` is the `SimpleAdapter` extension for one company proxy identity; it receives structured `PlatformMessage` inputs and creates durable outbound work. Different proxies in the same room have independent work queues. Settlement uses the SDK's existing turn contract after durable acceptance; acceptance is not business completion.
- `PAPGateway` exposes a configured company/service identity, not arbitrary peers discovered by an unauthenticated caller. Authenticated grants resolve caller identity to an authorized room and proxy participant. Raw caller-supplied room IDs do not grant membership.
- Integration-owned background workers use explicit `AsyncRestClient`/streaming clients under their proxy identity. They do not retain `AgentTools` from an expired turn. The concrete SDK handoff, settlement call, restart behavior, and adapter constructor shape must be proven by P04 tests before P04 completes.
- Each company proxy and inbound personal-agent proxy is an independently authenticated Band identity with separately provisioned or explicitly granted credentials. One transport owner holds each identity's connection. A gateway's single key cannot impersonate all participants. Multiplexing is accepted only through a public SDK interface with proven identity separation; it is not assumed. Removal/revocation stops that proxy's forwarding and releases its transport.
- An authenticated source participant is resolved through a host-managed grant to the acting user, PAP client identity, permitted company connection, reply recipient, and allowed operations. Caller-supplied owner/client/conversation metadata never creates a grant. Missing, conflicting, expired, or revoked authority fails before credential lookup, including replay and background work. Bind reply routing to that grant; do not infer the recipient from whichever participant last spoke. Concurrent independent owners require distinct conversation bindings even when they address the same company proxy.
- Structured protocol records use an authorized integration command interface and durable storage. Band messages may carry opaque correlation references, but possession of a reference is not authority: resolve it against the source participant and grant. Do not publish private action payloads through unfiltered `send_event` metadata. Authorize the external projection of every record; retain provenance through replay. Ordinary text remains in the same room. P04/P05 must prove this interface against actual SDK inputs and test forged and cross-owner references.
- Host-supplied authorization and execution interfaces supply verified caller identity, account consent, approval evidence, and business effects. Those inputs cannot be self-certified through protocol message content. Generic operations code manages revisions and recovery; applications own the effect and its deduplication contract.
- Roster visibility is projected by the integration on every external view. No new Band platform visibility capability is assumed. If direct Band credentials are exposed to a remote principal, the platform must independently enforce the same policy; that deployment is not accepted without evidence.

## Protocol baseline

Reviewed PAP draft: 0.1; Operations extension: 1. Upstream repository revision: [`e910c866f4c7ea45f169b435a470027d39b9c075`](https://github.com/personal-agent-protocol/personal-agent-protocol/tree/e910c866f4c7ea45f169b435a470027d39b9c075), retrieved 2026-10-16. This records the source revision; it does not claim any upstream conformance certification.

Implement the published unknown-field, version, and extension-negotiation rules. A future draft revision requires review against this baseline, recorded compatibility tests, and explicit capability advertisement. Unsupported capabilities must fail explicitly rather than appear successful. Each channel in P06 must have its own credential and lifecycle tests; a generic HTTP pass-through is not completion.

## Same-room invariants

1. One room may contain multiple inbound clients, coordinating participants, and outbound company proxies. External connections are keyed by tenant, user, client identity, company issuer, proxy, and conversation—not room alone.
2. Only explicit delivery recipients cause outbound forwarding. References, legacy unaddressed history, another proxy's messages, and self-message echoes do not. Human authorship is taken from authenticated provenance, not model-provided claims.
3. Inbound and outbound records retain distinct authority. Signing in to our gateway does not authorize our downstream company account access. Tokens never cross those boundaries.
4. Persist accepted protocol inputs before acknowledging them. Persist incoming external events before advancing their cursor. Retry uncertain delivery with stable identity; reconcile uncertain business effects instead of declaring failure or creating replacements.
5. Conversation/operation ownership follows the protocol's account-transition rules, not a permanently anonymous user key. Session renewal cannot accidentally change ownership or discard conversation history.
6. A concurrent request to one company must not lock all participants in the room. Ordering is scoped to the conversation/operation that needs it.
7. Removal or revocation prevents subsequent forwarding. The policy and membership checks must cover background workers and replay, not just initial connection establishment.
8. A lost first-message response is retried with the same PAP message ID so the existing conversation is recovered. PAP message deduplication is scoped to the user across conversations. Signed-out operation ownership is session-scoped; account-bound conversation ownership and continuation must be tested separately.

## Evidence required

The ledger separates unit protocol validation, authenticated gateway/adapter integration, persistence fault tests, and authorized live interoperability. Lack of an available public counterparty is an explicit P07 release blocker for that claim, not permission to fabricate a pass. Tests should use deterministic agents and protocol servers; no paid inference is needed to establish the transport contract.
