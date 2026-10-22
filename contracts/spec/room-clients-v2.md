# Room rules work with any authorized agent

The room authority is the required core. It authenticates participants,
advertises the applicable contracts, checks proposals, and durably retains
accepted state, receipts and deadlines. A client can be an ordinary model-based
agent, a hand-written program, an SDK client, or an experimental hybrid.
The same room rules apply to all of them.

No client journal, SQLite database, snapshot format, local compiler/checker,
replacement program, model provider or repair controller is required to join,
discover rules, or propose messages. The host MUST NOT use possession of those
components as a condition of ordinary participation. Room membership, current
authorization, role assignment and the public message protocol still apply.
Humans and human-agent conversation retain the exemption in
[agent scope](agents-and-extensions.md).

## Discover, understand, propose

An authorized agent MUST be able to learn the applicable protocol through the
room's public interface. It does not need this repository or a particular SDK.
The host MUST support proactive discovery. A rejected out-of-protocol proposal
from an authenticated agent MUST also point the caller to that same discovery
entrypoint; a response to a malformed request SHOULD.
`band-room/1` does not yet satisfy this section: its routes have no contract
catalog or source and definition fetch operation
([Band binding](band-transport.md)). Until they exist, a Band deployment MUST
NOT claim `0.2.0-lab.1` discovery.
Getting an error is a useful recovery path, not a required onboarding ritual.

The room's advertised operation descriptors MUST provide enough information to:

1. List contracts applicable to this room and caller, with a short description,
   engine/capability versions, exact source and compiled-definition identities,
   and the authorized operations for retrieving them.
2. Fetch readable contract documentation and machine-readable message types,
   field constraints and role rules. Include the exact source when authorized;
   do not invent a source URL or return a different revision under its hash.
3. Learn the caller's assigned role and active sessions, or how to request a
   host-authorized assignment/session. Discovering a role does not grant it.
4. Fetch a current role view: session ID, state, version, currently applicable
   rules, relevant deadlines and permitted accepted-message references.
5. Submit a proposal through the documented envelope/tool, inspect acceptance
   or rejection, and retrieve the original outcome of an uncertain submission.
6. For `room.contributions/1`, discover the standard `stop_contribution` operation
   and its bounded cause schema. The current view lists the caller's pending
   contribution scopes. A member can explicitly stop its own contribution;
   terminal host failure reports use a separate trusted control interface.
   See [contribution outcomes](contribution-outcomes.md).

The transport can advertise tools, links, or both. Their descriptors must
explain required arguments and versioned response shapes. An ordinary agent
can read the rules into its context and construct the next message. It is not
required to execute the contract locally. A compatible SDK may automate these
steps, but the public interaction must remain independently implementable.

Admission always rechecks current state and authorization. A discovered rule
is a permitted shape in a particular state, not a promise that every proposal
with that shape will pass payload, reference, contribution or capacity checks.

## Rejections should help an agent recover

After authentication and room-access checks, a recoverable protocol rejection
should carry a stable reason code, a bounded explanation and the operation
descriptor needed to fetch the applicable contract and current view. Where
authorized, include the session, pinned identities, current state/version and
specific expected field/type constraints. A sessionless first attempt should
point to the room catalog rather than guess a session or silently accept it.
The engine's decision supplies the reason code and, for a field's payload
failure, `detail` naming the field and the constraint with its declared bounds
([engine](engine.md)). The host adapter adds the explanation and descriptors
and passes `detail` through unchanged.

For example, an error can say: “A review needs a `text` field. Fetch the current
reviewer view and the pinned review contract here.” The actual tool names and
URLs are supplied by the host adapter. This guide does not claim an existing
Band endpoint already returns that response.

Diagnostics MUST NOT expose another role's private payload, a private room
checkpoint, credentials or inaccessible contract/session identities. Unknown
or unauthorized callers receive the normal access error. Contract prose and
error explanations cannot grant tools or permissions or install client code.
Discovery reads and rejected attempts do not become accepted business facts;
the host still accounts for their operational resource cost.

After a definitive rejection, the client can fetch fresh guidance and make a
corrected proposal with a new ID. After a lost response, it must first reconcile
the original proposal with its unchanged ID and contents. A timeout is not a
definitive rejection. Keeping enough information for automatic crash recovery
is a client capability choice; a particular storage engine is not mandated.
A client that loses that information may need host-assisted recovery. Its loss
does not undo accepted room history or weaken the room's admission rules.

## Proposal IDs: private randomness, shared namespace

Clients MUST choose proposal IDs that other participants cannot predict, using
at least 128 bits of OS-CSPRNG output or a keyed derivation with at least 128
bits of retained output and a host-private random key. Counters, timestamps,
actor/session names and unkeyed hashes of public inputs are not sufficient.
For an ordinary Python client, a fresh ID can be generated with:

```python
import secrets

proposal_id = secrets.token_hex(16)  # 128 bits; no message content in the ID
```

Generate once for a new proposal, then retain its ID and exact envelope until
the outcome is known. An exact retry MUST reuse both. A new ID after a timeout
could create a second accepted effect; reconcile first, as described above.
This does not require a particular client database or the optional runtime.

IDs MUST NOT be derived from payload content, including a payload hash or a
keyed payload digest. Band never copies the raw ID into shared system chat
records; it copies the opaque `receipt_handle = SHA-256(session|id)`
([Band transport](band-transport.md#authority-and-visibility)). That handle is
still a fixed function of the ID, so a payload-derived ID would give members
who cannot read the payload a verifier for low-entropy private content.

Message IDs occupy one session-wide namespace, not a namespace per actor.
If B has an accepted proposal with ID X, A's proposal with X is `id_conflict`,
even if its kind and payload match B's. Only the original actor's unchanged
envelope can be an exact duplicate. See the
[engine admission rules](engine.md#admission-algorithm-and-precedence).

Short IDs such as `q`, `a`, `request-1` and X in worked examples or test vectors
are illustrative labels only, never production ID-generation guidance.
The client rule does not make the room enforce unpredictability: a careless
client can still be pre-claimed. Room-side namespace/prefix hardening is a
separate, unresolved follow-up, not implemented by the H7 runner change.
The local H7-F01 follow-up is owned by Rowan, with Ada as the scoping consumer;
it does not claim an external ticket or authorize a wire change.

## The optional client experiment

The [remote program/LLM runtime](remote-runtime.md) is an optional experiment.
It explores whether a program can handle useful portions of an agent's work,
with model delegation, a journal, snapshots and repair around it. It might
become valuable product functionality, remain a niche tool, or be retired.
Room enforcement and public discovery MUST remain useful in every outcome.

Choosing that runtime opts into its recovery and isolation requirements.
Its `llm_only` mode still uses that runtime's machinery; it is not the only way
an ordinary model-based agent can participate. An ordinary agent can connect
directly. A local checker, if present, is advisory and never replaces admission.

| Capability | Required core | Optional client experiment |
|---|---|---|
| Authentication, access and role assignment | Room host | Uses public authorization |
| Rule discovery and useful rejection guidance | Room host | May cache or interpret it |
| Authoritative validation and accepted history | Room host | May preflight a proposal |
| Deadline scheduling and durable room recovery | Room host | No particular local storage required |
| Program replacement, model routing and repair | No dependency | Experimental |
| Client journal, snapshots and local checker | No dependency | Optional recovery implementation |

## Evidence and remaining integration

The current reference `Engine.view/2` returns the role definition, legal rule
shapes, types, docs, current state/version, visible facts and relevant duties.
`Engine.step/3` admits proposals without any client-runtime object. The
[direct agent example](../contract-lab/examples/direct_agent.exs) exercises
rejection, rule discovery, correction, acceptance and exact retry using those
interfaces, with no Python SDK or client journal.

That is an in-process host-interface demonstration, not public network or
Band discovery qualification. Room-wide contract catalogs, advertised fetch
operations and discovery pointers in ordinary-chat rejection responses are
explicit host integration requirements here. They are not implemented by the
existing trusted JSON-lines pipe, and the older Band routes do not establish
support for this new profile. A rollout must demonstrate an independent client
joining, discovering and completing a contract without the optional runtime.
