# Host presence and membership extensions

Engine `0.2.0-lab.1` supports exact capabilities `host.presence/1` and
`host.membership/1`. They are opt-in and MUST appear in the discovery card's
`extensions` list and the host's enabled capabilities. They do not make humans
contract participants or turn presence into evidence that work was completed.

```band
---
extensions: [host.presence/1, host.membership/1]
---
```

The following body fragments illustrate the grammar. Targets MUST be declared.

```band
"""
An assistant does long-running work that pauses when it or its supervisor goes
away and ends if the assistant leaves the room.
"""
contract @1h => expired

"""
You do the work. Keep going while you and your supervisor are present; when
the room pauses you, resume where you stopped once you are back online.
"""
role assistant

watch supervisor

"""
The assistant is working and is expected to stay online.
"""
state working
  on assistant offline for 5m => paused
  on watch.supervisor offline for 5m => paused

"""
Work is paused because someone went offline. It resumes when the assistant
comes back.
"""
state paused
  on assistant online => working

anystate
  on assistant removed => left

end left
```

`on SUBJECT online|offline [for DURATION] => TARGET` observes continuous
presence. Omitting `for` means immediate. `on SUBJECT removed => TARGET` observes
room membership removal and MUST NOT specify a delay. A role subject identifies
its immutable bound agent. `watch NAME` declares an environment alias;
`watch.NAME` may identify a supervising human without giving that human duties.
Its concrete identity is supplied by the host's immutable `watch_bindings`, not
embedded in reusable contract text. Watch aliases MUST be declared and exactly
bound at creation. The reference limit is 32 host triggers per contract.

With `host.membership/1` enabled, removal of a bound agent MUST terminate the
session. An explicit rule for such removal MUST target a terminal; without one,
`participant_removed` is selected. Without that extension the reducer accepts no
membership event (`unsupported_extension`). The host's fresh
[host evidence](agents-and-extensions.md#band-permission-binding-version-1)
then refuses every member operation by the removed agent with `room_access`,
and the session ends through its declared clocks.
A watched supervisor's removal can instead select another active state. Neither
removal nor any other transition may return to `new`. Rejoining the room does
not resurrect a terminal session or replace its bindings.

## Authority, ordering and continuity

These observations use a separate trusted host interface, never a business
message, an agent payload, a human chat message, or an unverified notification.
The adapter MUST authenticate and order the authoritative observation stream.
The exact event fields are `id`, `sequence`, `subject`, `principal`, `kind`,
`value`, and `at`. Kind is `presence` or `membership`. Presence is `online`,
`offline`, or `unknown`; membership supports `removed`. The principal MUST match
the session's bound role or watch. The host stamps `at` with trusted acceptance
time equal to the supplied reducer time. Backdated reports MUST NOT invent a
period of continuous absence. The only backdated input is the trusted creation
snapshot `initial_presence` ([presence](coordination.md#presence-uses-a-declared-grace-not-a-guess)).

Sequence numbers MUST strictly increase across this session's host stream.
An exact same-ID retry returns `duplicate_host_event` without changing state or
spending capacity. A changed same-ID event fails `id_conflict`; old sequences
fail `stale_host_event`. Replay/restart retains receipts and absolute clocks.
The state field `signal_revision` holds the highest accepted sequence. When the
host supplies `initial_presence` at creation, that snapshot takes sequence 1 and
one control record, so the first producer event MUST use sequence 2 or greater.
Without a snapshot the stored revision is 0 and sequence 1 is valid.
The reducer checks a host event in this order; the first failure decides:

1. Host authority, else `host_authority_required`.
2. Shape: exactly the seven fields, string `id`/`subject`/`principal`, a
   positive safe-integer `sequence`, a nonnegative safe-integer `at`, and a known
   kind and value; else `invalid_host_event`. The extension is checked after the
   kind and before the value: a known kind whose extension the contract does not
   declare is `unsupported_extension`, even when its value is also invalid.
3. Subject: `principal` matches the bound role or watch, else `subject_binding`.
4. Receipt lookup by `id`: the same fingerprint returns `duplicate_host_event`;
   a different one is `id_conflict`.
5. A presence event for a removed subject is `participant_removed`.
6. A sequence not above the stored revision, `at` unequal to the reducer time,
   or a reducer time before the stored `now`, is `stale_host_event`.
7. A trigger deadline beyond the safe-integer range is `invalid_time_range`.
8. Elapsed timers run; a session that is now terminal rejects with `terminal`.
9. Control capacity, below.

Because the receipt lookup comes before the sequence and time checks, an exact
retry after a restart, at a later time or after a newer sequence still returns
`duplicate_host_event`. The [portable coordination
assertions](tools/checkcoordination.py) pin the snapshot's sequence 1, the
retry after a newer sequence, the clock check and the extension order.

Repeated offline observations preserve the first offline time. `online` cancels
pending offline dwell. `unknown` interrupts continuous evidence, so a later
offline observation starts a new period. A provider outage MUST NOT be silently
translated into a verified participant absence.

All elapsed timers run before an arriving event or business proposal. The
oldest time wins; equal times follow the
[equal-time order](timeouts-v2.md#equal-time-order): work duty, collection
deadline, offline grace, state timeout, host trigger, then the whole-run cap,
with IDs compared lexicographically within a category. Thus a
reconnect strictly before the boundary cancels dwell; a reconnect at the boundary
arrives after the offline transition. It can then trigger an explicit online
transition in the destination state. A trigger fires at most once for its
observation epoch. Entering another matching state does not restart its dwell.

## Work, capacity and persistence

A nonterminal host transition preserves outstanding duties with their original
absolute deadlines. It enters the destination at the trigger's scheduled time,
so the destination's own clocks count from then even when the trigger is
observed later. Re-entering a state does not renew an existing duty with the
same ID. Newly introduced duties must still fit before the current `max_at`
([defaults](defaults-v2.md)), the remaining message capacity with their
[follow-on reserve](defaults-v2.md#reserve-known-response-capacity), and legal
reply rules.
Otherwise the transition records
`insufficient_time`, `completion_capacity`, or `unserviceable_obligation` as an
explicit terminal outcome. Terminal transitions clear outstanding work; they do
not assert that external side effects have been rolled back.

Presence and membership observations, and the transitions they fire, consume no
business message capacity and mention no one. An enabled signal extension
requires a separate finite
`max_controls` from 2 through 1024; a value outside 0–1024, or below 2 with a
signal extension enabled, fails creation with `control_budget_required`.
Accepted observations and fired transitions
each consume a control record. A final slot remains for an explicit terminal
outcome. Bound agent removal may use that slot to record its actual removal
outcome; any other event that finds only that slot left is rejected with
`control_capacity` and records `control_capacity_exhausted`. Retries do not
consume another slot. This is a bounded reducer accounting rule; the host must
separately reserve durable storage and atomically persist state, control
records, receipts and outcomes before acknowledgement. This control accounting
covers only presence and membership host events. Contribution controls
(`stop_contribution`, `fail_contribution`) never consume `max_controls`; they
have their own bound and no reserved slot
([contribution outcomes](contribution-outcomes.md)).

State profile `band-contract/state/2` persists observations, continuity epochs,
sequence, event fingerprints, fired triggers, control log/limit and watch
bindings. The host MUST schedule checks while agents are quiet and restore
before serving proposals. Timers cannot depend on another chat message arriving.

## Reference evidence and Band acceptance

The local reference tests cover immediate and dwell transitions, continuous
absence, exact-boundary reconnect, restart, original duty preservation, human
watch aliases, spoofing, stale events, removal, final-slot behavior, and capacity
checks. These are reducer results, not a deployed Band producer or measured
cross-node latency claim.

Before Band enables these capabilities its adapter MUST qualify the following:

| Case | Required evidence |
|---|---|
| Three real nodes; viewer on another node | Authoritative observations reach the owning session exactly once in durable order |
| Reconnect before/at/after dwell | Old socket teardown cannot override a newer active connection; timer ordering matches the profile |
| Producer partition or uncertain presence | `unknown` breaks dwell continuity; recovery cannot invent absence |
| Restart during offline dwell | Original absolute deadline, sequence and receipt survive |
| Removal with full business/control budgets | Durable membership change produces the terminal result using reserved capacity |
| Lost/delayed PubSub or EctoWatch notification | Durable reconciliation recovers the change; notifications are hints, not the only authority |
| Concurrent proposal and membership removal | One authoritative transaction order; removed principals cannot reuse a receipt for access |
| Watched human; ordinary human conversation | Presence can drive the declared transition while human conversation remains exempt |

The real three-node presence lab is required when connecting the Band producer.
A developer accepting that integration remains to be named; Eric is the consumer
of this specification and reference implementation. No downstream handoff or
live deployment is claimed by these local checks.
