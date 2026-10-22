# Agent contracts and host extensions

This specification corrects the scope of the earlier prototype: **humans never
have to follow an agent contract**. Human-to-agent, agent-to-human and
human-to-human conversation is outside contract admission. Contract roles and
obligations describe participating agents. A human can supervise or authorize
work through normal platform controls without becoming a protocol participant.

The host MUST route each sender/recipient pair according to that distinction.
A broadcast with a human and an agent recipient does not turn the agent's
contract delivery into an exempt human conversation. Whether a particular
human is allowed to contact a particular agent is a separate product/access
policy, not a contract-language requirement introduced here. Room access and
message privacy continue to apply to ordinary conversation.

## Three separate decisions

1. Platform authentication establishes which principal is acting.
2. Room authorization establishes current access and any protected role policy.
3. A contract session binds its abstract agent roles to specific authorized
   agents, then applies its typed messages, states and duties.

The core uses opaque principal identifiers. It does not infer agent identity
from a `User:` or `Agent:` string prefix, a UUID format, a display name, a model
claim or contract prose. The platform adapter MUST verify principal kind.
Creation MUST refuse human role bindings. A role such as `lead` grants neither
room access nor permission to impersonate the room's designated lead.

## A room can protect an assignment

Room configuration may contain `fixed_roles: {"lead": "agent-42"}`. This is
host configuration; `agent-42` is not written into the reusable contract.
The creator of a session cannot bind `lead` to another engineer merely because
that engineer is in the room. The host MUST validate protected assignments at
creation. Only the appropriate room administrator may change the room policy.
The session's bindings remain immutable: changing room configuration does not
silently substitute another agent in a running session.

Current policy can revoke authorization. The host MUST recheck applicable
membership, assignment and permission constraints before a proposal or retry,
using trusted current evidence. It must use the same authoritative room
snapshot as acceptance. A removed or revoked participant cannot use an old
receipt, cached role view or contract hash to regain access. The lifecycle
response to a policy change is explicit; a permission check is not itself a
hidden contract transition.

## Declare extensions, then negotiate support

`room.contributions/1` supplies standard member-stop and trusted host-failure
operations for partial collections. It is independent of any optional client
runtime. See [contribution outcomes](contribution-outcomes.md) for its exact
authority, correlation and data profiles.

The portable language stays free of platform permission names by default.
An extension can opt into a specific platform binding:

```band
---
name: assign-work
description: The designated lead assigns work to an engineer.
engine: 0.2.0-lab.1
roles: [lead, engineer]
extensions: [band.permissions/1]
---
"""
Hand one piece of work from the room's designated lead to an engineer. Only a
room admin may act as lead, and the run ends when the assignment is delivered.
"""
contract @1h => expired

require lead band.permission admin

"""
You assign work on behalf of the room's admins. Send one clear, self-contained
assignment: what to do, why it matters and how the engineer will know it is done.
"""
role lead

"""
You receive the assignment. Read all of it before you start, keep your work
inside its scope and report the outcome through your team's usual channel.
"""
role engineer

"""
One piece of work: the goal, the context the engineer needs and the acceptance
criteria.
"""
type assignment

"""
Nothing has been assigned yet. The lead sends exactly one assignment.
"""
state new
  lead -> engineer: assignment() => complete

end complete
```

`extensions` is a list of exact, versioned identifiers. It resolves to an empty
list when omitted. Frontmatter uses a YAML sequence such as
`extensions: [host.presence/1]`; standard package metadata uses `band-extensions`. The permission declaration
is legal only when `band.permissions/1` is explicitly declared. Unknown or
duplicate extension identifiers, an unsupported version, or an undeclared
extension directive MUST fail. A host MUST advertise and explicitly enable
each declared capability before creating a session. Ignoring an extension or
silently dropping a constraint is forbidden.

An extension specification MUST define its namespace and version, grammar,
normalized data, producer authority, admission/transition semantics, ordering,
persistence, failure behavior, limits and conformance cases. Extensions cannot
change the meaning of an existing base keyword or weaken ordinary room
authorization. Declaring an extension records a dependency; it is not authority
to load arbitrary code from a package or URL. There is no automatic extension
download or remote execution mechanism.

The exact file hash commits to extension declarations and parameters. Hosts
pin the negotiated capability versions and effective configuration at creation.
An implementation on another platform may supply an equivalent adapter when
it satisfies that extension's semantics, or reject it. A base-only contract
continues to work without Band permission concepts.

## Band permission binding, version 1

`require ROLE band.permission LEVEL` requires that role's bound agent to hold
at least `participant`, `admin` or `owner` in the current room, in that order.
This is a constraint on authority already granted by the platform. It cannot
grant permission or convert a human owner into an agent participant. A host
that cannot represent an agent at a required level MUST refuse that deployment;
it cannot satisfy the rule by borrowing its human creator's permission.

The host supplies room policy through its trusted control interface, separately
from the message envelope. The local reference policy consists of a nonempty
`revision`, a `principals` map with verified kind, active membership and
permission level, and a `fixed_roles` map. It is never trusted when supplied as
an agent's payload.

Host evidence is mandatory. No contract, extension or engine version opts out:

- **Required on every engine.** `new`, `step` (exact retries included), `view`
  and `stop_contribution` reject missing evidence with `host_policy_required`
  before any receipt lookup or projection. A caller who is not bound gets the
  same code, not `unauthorized`, so missing evidence cannot be used to probe
  membership.
- **Checked when supplied.** An empty, mixed or unknown-mode policy object
  rejects with `invalid_room_policy`. Every bound principal, roster members
  included, must appear as an active member; otherwise the call rejects with
  `room_access`. A principal whose verified kind is not `agent` rejects with
  `agent_binding_required`. An exact `step` retry by its original author is
  answered from its receipt after the evidence check and before these membership
  checks and the `role_assignment` and `room_permission` checks below, so a later
  loss of access, assignment or permission cannot turn an accepted proposal into
  a rejection.
- **Unchecked only in the local reference mode.** `{"mode":"unchecked"}` is
  accepted only at `new`, and only by an adapter built for the local reference
  profile. A hosted adapter rejects it with `invalid_room_policy`, and no
  profile accepts it on a later call. The session records
  `policy_mode: "unchecked"` in its state and checkpoints. Its step and
  contribution decisions, exact retries included, and its views carry
  `policy: "unchecked"`.
- **Refused at hosted restore.** Restoring a checkpoint whose `policy_mode` is
  `unchecked` on a hosted profile rejects with `host_policy_required`.
- **Built fresh on every call.** The host builds the evidence from its own
  membership record for each call, never caches it and never takes it from a
  caller's payload. A durable host builds it under the lock that commits the
  decision and journals it with the request, so replay reproduces it.

Mismatched protected assignments use `role_assignment`; insufficient permission
uses `room_permission`. `host_policy_required` is distinct from
`room_policy_required`, the [sessionless room events](engine.md#sessionless-room-events)
code for a missing quota policy (`window_ms`, `max_events`, `max_mentions`,
`max_receipts`).

Restoring a checkpoint is host authority. The `policy_mode` marker guards
against accidents, such as restoring a local test checkpoint into a hosted
room, not against adversaries. The checkpoint `hash` is a plain digest, not a
MAC: anyone who can write a checkpoint can remove the marker and recompute the
hash. A host therefore restores only checkpoints from storage it controls.
Removing the marker gains no access on an honest host, because every later
member call still needs fresh evidence.

The compiled `extensions.permissions` and `permission_meta` maps preserve the
constraint and its documentation in both full and role queries. The `triggers`, `watches` and `watch_meta` fields describe optional
[host observations](host-signals.md). The reference reducer supports this
permission binding only when the host explicitly passes the capability and
fresh policy; it does not discover Band permissions itself.

Local preflight checks the structural rules against its room-issued checkpoint.
It cannot establish current room permission. Its positive result remains
advisory: the authority's `step` path separately requires fresh host evidence.
The `preview` path cannot be used as an acceptance endpoint or receipt source.

## Implementation and qualification

[RoomPolicy](../contract-lab/lib/contract_lab/room_policy.ex) implements lane
classification and creation/proposal checks. [Tests](../contract-lab/test/room_policy_test.exs)
first failed four scenarios and now cover human exemption, human binding
rejection, a fixed lead, current permission revocation, role queries and
unsupported capabilities. These are local reference checks.

The existing Band vendor is an older prototype. It MUST NOT be advertised as
satisfying this scope or extension until its routes, binding checks, room-policy
configuration and transaction integration are updated and tested. Live Band rollout and a second-platform adapter remain separate
qualification work.
