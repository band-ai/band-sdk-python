# Personal Agent Protocol integration with Band agents

Date: 2026-10-16. Status: target integration architecture for discussion; not implemented or tested. This document defines the complete intended integration, not an incremental pilot.

Prerequisites: we assume you know HTTP, APIs, authentication, identifiers (IDs), and user interfaces (UI). Personal Agent Protocol (PAP), also called Poppy, connects a user's personal agent to companies. Band provides rooms and agent collaboration. Agent2Agent (A2A) is another agent communication protocol for which Band already has integration code. The Band software development kit (SDK) provides application interfaces to Band.

**Target:** a generic PAP integration for Band, parallel to its A2A integration and independent of any agent runtime. Provide both a PAP client adapter that represents external companies as Band participants and a PAP server gateway through which external personal agents can access authorized Band-backed services. All conversations happen in **one Band room**, with addressed delivery and independently controlled participant visibility. External PAP conversations, sessions, and credentials remain scoped to their own company and owner.

The integration owns protocol transport, identity, routing, and recovery. Participating agents own reasoning and application behavior. No model, prompt, Python agent class, or particular assistant is required by the PAP integration. Use the existing Band SDK extension interfaces; the earlier requirement to avoid changing Band SDK internals remains. Packaging is an independent decision: a separately distributed integration can still be a complete, reusable protocol integration.

Muse and Dot are used here as the user's examples of personal assistants. This note does not establish which connectors those products currently support. Target and Best Buy are illustrative counterparties; neither a working PAP endpoint nor a successful connection to either has been verified.

## 1. Protocol scope and review baseline

PAP covers company discovery, sessions, authorization, APIs, browsing, and conversations. Its conversation model connects a personal agent acting for one user with one company; the reviewed draft does not define a shared conversation containing several independent companies. Conversations can outlast sessions. [PAP specification](https://personalagentprotocol.org/docs/spec).

The reviewed version is draft 0.1. It can change incompatibly before 1.0; pin the implementation to a reviewed revision and define a compatibility policy. Push notifications, attachments, and payments remain open topics. The public repository publishes documentation, not a ready-to-run protocol server. [Path to 1.0](https://personalagentprotocol.org/docs/path-to-1), [repository](https://github.com/personal-agent-protocol/personal-agent-protocol).

This note separates three kinds of statement:

- **Protocol facts:** linked to the published specifications and guides.
- **Existing code:** inspected in the local Band SDK at commit `0ece521c70b9688f2aa121383184b34cccb8309c`; source inspection does not establish live interoperability.
- **Proposals:** our integration choices, requiring implementation and verification.

The single-room topology is a fixed requirement. Platform delivery/history code was also inspected at commit `74eb29a306547ce1f1c4f93db50df24117fa4b8c`. Participant-list filtering is a requested integration policy; its current platform enforcement path has not been verified in this review.

## 2. Should we build a bridge like A2A?

**Yes: reuse the architectural pattern of a bridge. Implement PAP as its own protocol boundary.** Client/server communication does not prevent one client from coordinating several servers. A2A also defines client and server roles; converting PAP traffic into A2A is unnecessary merely to contact multiple companies. [A2A specification](https://a2a-protocol.org/v0.3.0/specification/).

The existing Band implementation provides two useful reference points:

- [`A2AAdapter`](../../src/band/integrations/a2a/adapter.py) forwards room messages to a remote agent and posts responses back. This is the closer analogy for contacting Target and Best Buy.
- [`A2AGatewayAdapter`](../../src/band/integrations/a2a/gateway/adapter.py) accepts external requests and routes them to Band peers. It maps external contexts to rooms and can add another peer to an existing room. This is the closer analogy for letting an external assistant access a Band-backed service. Its current pending-request guard rejects concurrent requests for the same room; do not infer arbitrary concurrency from the gateway name.

Reuse concepts such as routing, subscriptions, and lifecycle ownership after reviewing their suitability. PAP needs separate authorization, conversation ownership, event recovery, and operation confirmation logic. A2A context IDs and task states are not substitutes for those contracts.

| Direction | Proposed component | Responsibility |
|---|---|---|
| Outbound: Band participants contact companies | `PAPAdapter` plus PAP client and credential services | Represent each company as a Band participant; maintain connections and route addressed messages and protocol events. |
| Inbound: external assistants contact Band-backed services | `PAPGateway` | Expose the service identity, authorize external callers, bind them to Band participants, and route their permitted conversation views. |
| Internal collaboration | Band room addressing and participant policies | Route authorized participant exchanges in the same room. A particular coordinator is optional. |
| Application behavior | Any Band participant or connected assistant | Decide what to ask, compare results, or implement a business workflow; no runtime dependency in PAP. |

An A2A-to-PAP translator is an optional additional interface only if a real caller supports A2A and needs it. It is not required by the shopping workflow.

`PAPAdapter` and `PAPGateway` are proposed component names, not claims that those classes already exist. They are complementary halves of one integration. All target responsibilities apply from the initial implementation; the order in which code is written does not narrow the acceptance contract.

### Target component boundaries

OpenAPI describes HTTP APIs; Model Context Protocol (MCP) exposes tools to agent applications. They are company access channels that the integration can connect alongside PAP conversations.

| Component | Complete responsibility |
|---|---|
| PAP protocol core | Versioned schemas, discovery validation, serialization, errors, advertised capabilities, and extension negotiation. |
| PAP client adapter | Company proxy participants, addressed outbound inputs, incoming messages/state, continuation, handoff, closing, and correlation. |
| PAP server gateway | Company discovery and supported endpoints, inbound identity/authorization, external-to-Band participant binding, and filtered event responses. |
| Authorization service | Client identity and signing, account linking, session renewal/revocation, destination-bound credentials, and authorization checks. |
| Routing and visibility service | Resolve room, participant, owner, company, and conversation; enforce both addressed-message and roster policies in every view. |
| Conversation store and workers | Durable inbox/outbox, event cursors, retry records, connection ownership, independent company progress, recovery, and cleanup. |
| Operations service | Versioned proposals, authenticated confirmation, authorization, durable state, cancellation/reconciliation, and effect deduplication. |
| Channel connectors | Conversation transport, advertised OpenAPI/MCP interfaces, and browser-session support where offered, all using the same authorized company connection. |
| Band integration interface | Existing authenticated message, participant, history, and event interfaces; protocol workers own their connections independently of agent execution. |

The gateway exposes the organization it is authorized to represent, not an arbitrary list of Band peers as if each were a separate company. A configured service routes to authorized participants in the same room. Account linking and room membership are explicit authorization decisions; callers cannot select an unrelated room by supplying its ID.

For inbound clients, produce an external conversation view from the caller's authorized room messages. Preserve author provenance and PAP ownership semantics; internal participant names never grant external authority. For outbound company connections, map each proxy's view to its own PAP conversation. Thus one Band room can contain both inbound personal-agent participants and outbound company proxies without combining their credentials or transcripts.

## 3. Muse or Dot talking to Target and Best Buy

Assume we control the assistant or can give it a supported connector. The assistant opens independent company conversations through merchant bridge participants in the same Band room. One room does not mean that every participant receives every message.

```text
One Band room
+------------------------------------------------------------+
| User       Muse / Dot       Target bridge   Best Buy bridge |
|                |                 ^                ^        |
|                +-- @Target ------+                |        |
|                +-- @BestBuy ----------------------+        |
|                ^                 |                |        |
|                +-- @Muse / @Dot --+----------------+        |
+------------------------------------------------------------+
                                  |                |
                                 PAP              PAP
                                  |                |
                           Target endpoint   Best Buy endpoint

Each bridge forwards only its authorized, addressed messages.
```

The merchant participants are integration-managed proxies: “Target via PAP bridge” and “Best Buy via PAP bridge.” Each posts external replies under its own Band identity. The actual merchant server stays on PAP and does not need to implement Band or join its transport. All conversation messages are recorded in the same room; credential and protocol recovery records remain in application storage.

For each room and authorized user, map the Target participant to its Target connection/conversation and the Best Buy participant to its Best Buy connection/conversation. Never key both connections by room ID alone.

Illustrative task: “Compare this exact television at Target and Best Buy, including delivery and returns. Ask me before buying.”

1. Create one internal shopping task and one Band room containing the user, the coordinator, and both merchant bridge participants.
2. Resolve each company's supported interfaces independently. If a company does not offer a compatible interface, report that limitation; do not treat an ordinary website chatbot as a PAP endpoint.
3. Create one connection record for each merchant, scoped to the authenticated user and our tenant. Keep credentials and conversation state separate.
4. Post separate messages addressed to `@Target` and `@BestBuy` in that room. Their bridges can dispatch concurrently. Initially share only what is needed for the comparison. Obtain account access separately if a request needs it.
5. Record each answer with its source, product identifier, currency, delivery assumptions, observation time, and any offer expiry. A missing shipping cost remains unknown, not zero.
6. Post each merchant reply from its own bridge participant, addressed to the coordinator. Compare the answers in the same room. A slow or failed merchant should not erase the other result.
7. If the user wants negotiation, deliberately send the permitted comparison facts to the selected merchant. Do not automatically broadcast transcripts or account details.
8. When the user selects an offer, obtain approval for that merchant's exact proposed action and execute only that action. Keep the other conversation read-only or close it.

The integration must support independent requests to different company participants in the same room. Durable protocol workers collect results and post addressed Band messages. Each receiving agent runtime decides how to process its own inputs; the PAP integration must not depend on its generation loop or impose one global in-flight request for the room.

## 4. What “multi-peer chat” should mean here

| Experience | Proposed behavior | Responsibility |
|---|---|---|
| Parallel shopping | Ask several companies separately and compare answers. | Any authorized coordinating participant; a shopping coordinator is one example. |
| One Band chat with several merchant participants | Store every exchange in the same room; use recipient mentions to deliver each exchange to its intended agents. | Band addressing plus merchant bridge identities; separate PAP connections remain behind those participants. |
| Mediated negotiation | Relay an explicitly permitted offer or question from one conversation to another. | Our coordinator, with disclosure policy and attribution. |
| Native multi-company PAP conversation | Several companies share membership and a transcript on PAP itself. | A separate agreement or protocol extension. This is unnecessary for the selected single Band room design. |

### Addressing and participant visibility

| Sender | Explicit recipients | Intended effect |
|---|---|---|
| User | Muse or Dot | Give the coordinator the shopping objective. |
| Muse or Dot | Target bridge | Ask Target a question; Best Buy is not a recipient. |
| Muse or Dot | Best Buy bridge | Ask Best Buy a question; Target is not a recipient. |
| Target bridge | Muse or Dot | Return Target's answer without addressing Best Buy. |
| Best Buy bridge | Muse or Dot | Return Best Buy's answer without addressing Target. |
| Muse or Dot | User | Present the comparison or request approval. |
| Muse or Dot | Selected merchant bridge | Send a permitted follow-up or execute the approved application flow. |

The human user's room view can show the whole discussion. A merchant receives its addressed exchanges through its proxy. Mentioning a participant as a reference must not accidentally make it a delivery recipient.

Apply participant-list visibility independently: for example, each merchant-facing view may expose only itself and the coordinator, while the coordinator sees all participants. Hiding another participant does not substitute for message filtering; both policies need their own checks. A trusted bridge can project the permitted roster into PAP context without exposing raw Band roster events. If merchants receive direct Band API access in a future design, enforce that policy across those APIs too.

**Observed enforcement and qualification:** [`ChatChannel.agent_gate_passes?/2`](https://github.com/thenvoi/thenvoi-platform/blob/74eb29a306547ce1f1c4f93db50df24117fa4b8c/lib/thenvoi_com_web/channels/api/v1/chat_channel.ex) gates live agent messages on delivery mentions and excludes self-messages. [`Chat.list_messages_for_participant/3`](https://github.com/thenvoi/thenvoi-platform/blob/74eb29a306547ce1f1c4f93db50df24117fa4b8c/lib/thenvoi_com/thenvoi/chat.ex) and [`mention_visible_to_participant`](https://github.com/thenvoi/thenvoi-platform/blob/74eb29a306547ce1f1c4f93db50df24117fa4b8c/lib/thenvoi_com/helpers/query_fragments.ex) filter history, but the default query includes the participant's own messages and messages with absent, malformed, or empty mentions. Therefore the bridge should require explicit structured recipients, forward only authorized addressed inputs, and preserve that rule during history bootstrap/replay. Keep its own sent messages for continuity without forwarding them again. Do not export the human/owner's broader room view as merchant context.

If multiple bridge identities share one transport connection, demultiplex each received message by its recipient identity before accessing merchant credentials. Test the same isolation for replay, participant updates, and referenced content. A shared connection is an optimization, not a shared merchant view.

A relayed competitor quote is information supplied by our assistant, not a message authenticated as originating from that competitor in the receiving conversation. Preserve that distinction in both UI and records.

## 5. What if Muse or Dot is an external product?

We need an integration that the product actually supports. Publishing an endpoint does not cause an external assistant to discover or use it automatically.

| Available integration | Proposed path | Identity consequence |
|---|---|---|
| Assistant participates in Band | Address the company proxy in the shared room. | The integration resolves the authorized user and company connection independently of the assistant runtime. |
| External assistant supports a suitable tool/API connector | Expose bounded shopping operations through that connector. | Authenticate the caller and bind each request to our user; establish merchant consent separately. |
| External assistant can call our PAP service | Publish our own company service, backed by Band, which coordinates downstream requests. | We are a company to the external assistant and a separate personal-agent client to each merchant. |
| External assistant supports A2A | Add an A2A entry point to the same application coordinator if needed. | A2A access does not establish downstream merchant authorization. |
| No usable extension point | No automatic integration can be promised. | A supported product integration or user-driven workflow is required. |

In the PAP service option, the user would ask the external assistant to work with our comparison service. Our service then contacts the merchants. This requires an explicit account-linking and consent design; it is not transparent token forwarding.

Publish discovery under the domain of the service we operate, and identify it honestly. Never publish a gateway that claims to be Target or Best Buy without their authorization. Separate inbound permissions for our service from downstream permissions for each merchant.

If both Muse and Dot are present, each remains a distinct authorized participant. Both can collaborate and contact permitted company proxies. Bind every request to its acting user and delegated authority, rather than assuming room membership permits spending. Prevent conflicting purchase execution through the application's purchase policy. Sharing merchant credentials or treating different personal-agent identities as interchangeable is not implicit in sharing a room.

## 6. Identity, state, and execution boundaries

**OAuth** delegates account access through authorization tokens; an issuer is the authority issuing those tokens. **DPoP** binds token use to proof that the caller holds a private key.

PAP discovery uses `/.well-known/poppy.json`. Companies are identified by their OAuth issuer; the discovery domain and issuer metadata require mutual validation. User identifiers are company-specific and must not derive from personal information. Conversation/API authentication uses DPoP; MCP APIs use resource-bound Bearer tokens. Human-authored text and agent-authored text are distinguished. [Discovery and authorization specification](https://personalagentprotocol.org/docs/spec).

Protocol storage separates durable identities from replaceable credentials. Application-owned example state is explicitly separate:

| Record | Proposed contents |
|---|---|
| Coordination task (application-owned) | Objective, controller choice, disclosure policy, and purchase intent. The integration retains only authorization/correlation references needed to route protocol traffic. |
| Company connection | Company issuer, personal-agent client identity, company-specific user identity, linked account reference, credential-store reference. |
| Company conversation | Shared Band room ID, merchant participant ID, connection reference, external conversation ID, current session reference, event cursor, pending inputs, lifecycle state. |
| Message delivery | Internal request ID, destination, external message ID, content digest, delivery state, source event ID. |
| Protocol operation | Company connection, operation ID, revision, terms, trusted approval reference, confirmation, execution state, reconciliation state. Business-policy content and evaluation belong to the application. |

Use compound lookup keys scoped to tenant, user, client identity, and company connection. An external conversation ID alone must never authorize access. Keep Band room IDs as internal references and keep conversations independent of token renewal.

Resolve the authenticated source participant through an explicit delegated grant before choosing credentials or reply recipients. Reject ambiguous authority and forged owner/client metadata. Each proxy has its own authenticated Band identity and transport owner. Structured records are resolved through an authorized command interface using scoped opaque references; private protocol data must not be sent through unfiltered event metadata. These are mandatory boundaries in [the integration contract](contract.md).

The integration owns signing and credential storage behind an enforced service boundary. Participating agents receive authorized protocol actions, not raw credentials or arbitrary signing access. This contract is the same for every agent runtime and for external clients.

Apply bounded metadata fetching, address validation, log redaction, and controlled retries. Keep secrets out of model context. Implement and advertise supported sign-in methods explicitly; keep credential collection and consent in trusted interfaces. These choices follow the [PAP implementation guides](https://personalagentprotocol.org/docs/guides).

## 7. Approvals and buying from only one merchant

The optional Operations extension represents proposed actions with an operation ID and immutable revisions. Confirmation targets the approved revision; conversational assent alone does not confirm it. It requires at-most-once execution and recovery using the same operation ID after uncertainty. The personal agent's approval assertion does not independently prove that a human consented. [Operations extension](https://personalagentprotocol.org/docs/extensions/operations).

Our approval flow should persist the merchant's proposal, present its exact terms through a trusted interface, bind the user's decision to that revision, then confirm through a policy-enforcing service. An agent-generated message saying “approved” must not create the approval record.

The generic integration does not contain shopping-specific selection logic. A shopping application can add a local **purchase intent**: the user's goal to buy one item, with one selected merchant. Acquire a durable execution lock for that intent before confirmation. If confirmation times out, preserve the selected merchant and reconcile its operation before allowing another purchase.

An operation ID deduplicates one merchant's operation. It does not stop a separate purchase at another merchant. PAP does not supply an atomic transaction across Target and Best Buy. Our application should not promise all-or-nothing checkout across companies; cancellation and compensation depend on each company's actual capabilities.

If a merchant does not support Operations, make write support an explicit per-integration decision. Do not advertise the extension's guarantees for an ordinary write API.

## 8. Delivery, waiting, and recovery

PAP conversation messages have retry IDs; events have resumable cursors. Partial streamed text is display-only. [Conversation specification](https://personalagentprotocol.org/docs/spec).

Proposed application handling:

1. Persist an outbound request and its stable message ID before dispatch. An outbox is the durable queue of these unsent or uncertain requests.
2. Persist incoming events and deduplicate them before recording the advanced cursor. An inbox is the durable record of received events awaiting internal delivery.
3. Deliver accepted results into Band through a separately authorized connector identity. Preserve source and correlation identifiers, and prevent outbound messages from echoing back into a loop.
4. Keep protocol waits in integration-owned workers. Incoming results become addressed Band messages whether or not a particular assistant is currently executing. The integration does not hold an agent callback open until the entire external conversation finishes.
5. Recover from worker restart using the durable inbox/outbox and connection records. Treat the gap between Band acceptance and local recording as an explicit duplicate-delivery test case.

The completed integration must demonstrate idempotent recovery across the Band handoff, using supported platform primitives and integration-owned deduplication. Current source inspection alone does not establish that guarantee. Background sends use the connector's authorized connection, not a previous agent turn's temporary tools.

A company connection can receive events while any room participant is busy. Persist and deliver those events independently; let Band and the recipient runtime determine execution timing. Altering an agent's active generation is outside the PAP protocol integration's responsibility.

## 9. Target integration contract

Every row is part of the intended completed integration. These are requirements, not assertions of current support.

| Area | Required end state |
|---|---|
| Runtime independence | Works with any authorized Band participant; no model-runtime imports or agent-class requirements. |
| Both directions | Outbound company proxy adapter and inbound service gateway, with consistent authorization and lifecycle. |
| Same-room collaboration | Multiple independent personal-agent and company participants share one room with recipient-specific delivery. |
| Participant visibility | Configurable roster visibility across snapshots, updates, and externally projected context. |
| Structured protocol data | Preserve message data, source identity, context, protocol state, and correlation without relying on parsing model prose. |
| Identity and authorization | Verified discovery and client identity, explicit account linking, scoped sessions, credential isolation, renewal, and revocation. |
| Protocol lifecycle | Conversation creation, continuing messages, events, human participation, handoff, closing, and applicable errors. |
| Supported channels | Conversation, OpenAPI/MCP, and browser-session capabilities handled according to advertisement and negotiated support; unsupported features are explicit. |
| Operations | Approval-bound revisions and recoverable confirmation/cancellation; business-specific policies plug into the generic service. |
| Durable delivery | Persisted inputs/outputs, deduplication, cursor recovery, bounded retries, and reconciliation of uncertain acceptance. |
| Independent progress | A slow company or waiting human does not block other participants or require an agent runtime to remain active. |
| Lifecycle ownership | Workers and connections have explicit owners, shutdown behavior, and recovery rules; participant removal revokes further forwarding. |
| Observability | Correlated protocol and Band records with secret redaction and clear separation of public progress from internal diagnostics. |
| Compatibility | Reviewed protocol revision, negotiated capabilities, and tests for supported extensions and version changes. |

## 10. Completion evidence

Validate the entire target contract with deterministic protocol fixtures, fault-injection tests, and authorized live interoperability checks. Mock-company tests establish controlled behavior; they do not establish that Target, Best Buy, Muse, or Dot exposes a compatible production endpoint. Neither direction is an optional later phase of this target architecture.

These are acceptance requirements, not tests already written or run:

| Test | Required evidence |
|---|---|
| Runtime independence | The same routing and authorization behavior works with a deterministic Band participant and different agent integrations; no model is required to exercise the bridge. |
| Both directions in one room | An inbound personal-agent connection can communicate with authorized outbound company proxies in the same room, without credential forwarding between domains. |
| Two-company concurrency | Requests progress independently; every result is attributed correctly; no global single-request room lock blocks unrelated company conversations. |
| Same-room recipient isolation | Target receives only inputs addressed to its proxy, while Best Buy receives only its inputs; authorized users and coordinating participants retain their permitted views. |
| History and replay isolation | Restart does not forward another merchant's messages, self-message echoes, or legacy messages without explicit recipients. |
| Participant visibility | Restricted views omit hidden peers in roster snapshots, updates, and protocol context. |
| Reference versus delivery | A non-delivery reference to Best Buy inside a Target exchange does not dispatch that exchange to Best Buy. |
| Ownership isolation | Another tenant, user, or client identity cannot access a connection by guessing its IDs. |
| Authorization lifecycle | Sign-in, scope changes, session renewal, revocation, and account mismatch preserve identity boundaries. |
| Channel compatibility | Each advertised supported channel applies the right credential and authorization contract; unsupported capabilities fail explicitly. |
| Conversation lifecycle | State events, human authorship, handoff, direct conversations, and closing remain consistent across reconnects. |
| Retry and recovery | Duplicate input, event replay, lost response, and process restart do not duplicate externally visible effects. |
| Approval revision | Changed terms, expired approval, and an untrusted “yes” cannot authorize confirmation. |
| Uncertain operation | Reconciliation retains the same operation identity and never reports failure merely because the result is unknown. |
| Credential boundary | An agent or external caller cannot read signing keys or use the service to sign arbitrary requests. |
| Lifecycle | Removal and shutdown stop unauthorized forwarding and release owned resources; durable work resumes only under valid authority. |
| Shopping policy example | Two assistants racing on one purchase intent cannot buy twice; a timeout at one merchant does not silently trigger purchase at another. This validates application policy, not a PAP guarantee across companies. |

## 11. Fixed requirements and remaining design decisions

| ID | Topic | Decision or remaining question |
|---|---|---|
| D01 | Integration level | Fixed: generic Band protocol integration analogous to A2A, independent of agent runtime. |
| D02 | Directions | Fixed: both PAP-to-Band gateway and Band-to-PAP adapter belong to the target integration. |
| D03 | Room topology | Fixed: all conversations in one Band room, with recipient-specific delivery. |
| D04 | External topology | Fixed: distinct company proxies and external PAP conversations; no merchant child rooms. |
| D05 | Participant visibility | Required: configurable visibility. Determine the precise platform enforcement and bridge projection interfaces. |
| D06 | External client integration | Verify how each selected personal assistant connects; do not assume product capabilities from its name. |
| D07 | PAP identity ownership | Decide which organization operates each declared service/client identity and which account-linking relationship authorizes it. |
| D08 | Business policies | Keep shopping, negotiation, purchase selection, and standing permissions outside the generic transport. Define policy interfaces rather than hardcoding one workflow. |
| D09 | Packaging | Use existing SDK interfaces without modifying SDK internals. Choose distribution and deployment independently of protocol scope. |
| D10 | Unsupported counterparties | Explicitly report unsupported capability or use a separately authorized integration; no invented PAP compatibility. |
| D11 | Release acceptance | Complete contract and interoperability evidence; no read-only pilot or one-direction-only substitute for the target. |

The inspected [A2A client](../../src/band/integrations/a2a/adapter.py) and [gateway](../../src/band/integrations/a2a/gateway/adapter.py) are implementation references, not PAP conformance evidence.
