# Explicit clock renewal

An ordinary event does not renew a deadline. Renewal is an explicit effect of
an accepted event. This addition never looks up a clock by message type.
`review`, `answer`, `shipment` and other business names have no special timer
meaning. The following example applies to any kind of work:

```band
"""
A worker does open-ended work in five-minute stretches, renewed by heartbeats,
for at most four hours in all.
"""
contract @80m max @4h => expired

"""
You wait for the result. A heartbeat means the work is still moving.
"""
role lead

"""
Send a heartbeat at least every five minutes while you work, and the result
when you are done.
"""
role worker

"""
A sign of life from the worker: still working, no result yet.
"""
type heartbeat

"""
The finished work.
"""
type result

"""
The worker is working. Each heartbeat restarts this state's five-minute clock.
"""
state working @5m => stalled
  event worker -> lead: heartbeat() => @state
  worker -> lead: result() => complete
```

A state heartbeat accepted at minute four moves the state deadline from minute
five to minute nine. It preserves the actual entry time, visit count, accepted
facts, contributions, independent duties and contract clock.

## Target one exact timed declaration

```band
"""
A worker owes a result within five minutes, renewed each time it sends a
heartbeat.
"""
contract @30m => expired

"""
You wait for the result. A heartbeat buys the worker another five minutes.
"""
role lead

"""
Send a heartbeat whenever you need more time, and the result when you are done.
"""
role worker

"""
A sign of life from the worker: still working, no result yet.
"""
type heartbeat

"""
The finished work.
"""
type result

"""
The worker is working. Each heartbeat re-enters this state and restarts the
five-minute window for the result.
"""
state working
  event worker -> lead: heartbeat() => @self
  worker -> lead: result() @5m => complete | stalled
```

`@self` attaches to the **immediately following executable timed message rule
in the same state**. Documentation, blank lines and comments may separate them.
The compiler resolves it to that declaration's immutable rule and duty IDs. It
never searches for a message type named `result`. Another rule using the same
message type cannot make the target ambiguous. Moving an unrelated declaration
between these lines is a compile error unless it is itself a timed rule; source
review must treat moving a timed declaration as changing the attachment.

One timed event can both acknowledge and renew. The event's own duty and the
`@self` renewal are independent effects of the same accepted message:

```band
"""
An adversary critiques an owner's plan within one round of at most four hours.
"""
contract @12h => expired

"""
You own the plan. Acknowledge the critique when it arrives and fold it into
your next draft.
"""
role owner

"""
Critique the owner's plan. Report progress at least every fifteen minutes and
send the critique within the hour.
"""
role adversary

"""
A short note that the critique is under way.
"""
type progress

"""
Your critique: each weakness, why it matters and what would fix it.
"""
type critique

"""
The first critique round. Progress notes keep it alive; the critique ends it.
"""
state round1 @4h => stalled
  event adversary -> owner: progress() @15m => unacknowledged @self
  adversary -> owner: critique() @1h => folding | stalled
```

The first accepted `progress` discharges the fifteen-minute duty, and every
accepted `progress` renews the critique duty that follows it, so a `progress` at
minute five moves the critique deadline from minute sixty to minute sixty-five.
Silence still ends the session `unacknowledged` at minute fifteen. The `@self`
target is resolved exactly as for `=> @self`: the immediately following timed
rule or `collect`, never the event's own duty. Only `@self` combines with a
timed event; `@state` and `@contract` stay separate untimed events.

`@self` also attaches to an immediately following `collect` statement. The
collection's shared deadline moves, while accepted results and each participant's
offline-grace interval stay unchanged. In an `each` rule, renewal moves its one
shared response clock and retains contributions already received; it does not
create separate per-member timers. Only senders explicitly authorized by the
heartbeat rule can request that renewal, and only while they still wait: a
collection member that has replied or failed, or an `each` sender that has
already contributed, is refused with `already_contributed`, so a settled member
cannot extend the others' wait. Authors should choose those senders according
to who may extend the shared wait.

These are compile errors with code `timer_target`:

- `@self` not directly followed by a timed message rule or `collect` in the
  same state, including a detached one or one before an untimed rule, except
  in a reply wait state, where it renews the wait (below);
- a type-name target such as `@review`;
- any renewal on a `room` or `anystate` rule;
- `@state` in a state without a declared clock;
- `@contract` unless the contract line declares a finite window with `max`, so
  `contract @30m => expired` and `contract @infinity => expired` cannot be
  renewed.

A renewal does not enter the state again or consume another entry allowance.

## Renew a reply wait

In the `state W` section of a [reply block](contract-dsl.md#reply-alternatives)'s
wait state, `@self` renews the wait: `event deployer -> lead: progress() => @self`,
or a timed event's trailing `@self`. W cannot hold a timed rule or `collect`, so
the wait is the only clock `@self` can name there. An accepted renewal gives
the replier a whole new timeout window from the renewing event. Its reset is
`{profile:"band-contract/renewal/1", scope:"wait", duty:"due-M"}`, naming the
wait's immutable ID.

The replier chooses which alternative it sends, so before accepting a renewal
the engine checks that **every** alternative's target still fits before
`max_at`, as a quorum close checks both of its targets; the rules for what
"fits" are those in [admission](#admission-boundaries-and-recovery). Otherwise
the renewal is refused with `insufficient_time`. Renewing the wait never moves
the contract clock or a state clock.

## Choose a contract window and maximum deliberately

| Declaration | Meaning |
|---|---|
| `contract @30m => expired` | Fixed creation-to-expiry clock; cannot be renewed |
| `contract @80m max @4h => expired` | Renewable eighty-minute window, never later than four hours after creation |
| `contract @30m max infinity => expired` | Renewable thirty-minute window, with no absolute maximum |
| `contract @infinity => expired` | Explicit opt-out: no age-based expiry |
| `contract @infinity max @4h => expired` | Compile error: a finite maximum is shorter than an infinite initial window |

`event worker -> lead: heartbeat() => @contract` renews a finite contract window
only when its declaration explicitly supplies `max`. Each accepted renewal sets
the session's `max_at`, its current expiry, to the earlier of acceptance time
plus the window and `clocks.hard_at`, the immutable maximum. The maximum MUST be
at least the initial window, including infinity.
An infinite window with a finite maximum MUST fail compilation. Write
`contract @4h => expired` for a fixed four-hour cap; it needs no renewable
window or `max` clause. A hard maximum always
uses session creation time; no heartbeat, retry, state transition or restart
moves it. The same declared outcome handles window or maximum expiry.

A finite maximum is the recommended default when a job should eventually stop.
`max infinity` is an explicit author choice for a service that may continue while
activity arrives. It is not the default, a guarantee of service availability, or
an exemption from finite message/control/storage budgets. `@infinity` alone has
no silence watchdog and cannot be a renewal target. Every contract declares
one of these lines; a source without one fails with `contract_clock_required`.

## Admission, boundaries and recovery

Renewal happens only after a fresh event passes identity, state, type, payload,
reference, capacity and clock checks. Exact retries, rejected proposals, reads
and restoration do not renew. Expiry is processed first; equality is too late,
and a heartbeat cannot resurrect a completed job.

A rule or collection renewal must fit its entire newly promised response window
inside the remaining state and contract clocks, or it is rejected with
`insufficient_time`. The renewed reply or collection close may come as late as
the new deadline, so the state it leads to must still be able to give each of
its declared duties and its collection a whole window before `max_at`; a
renewal that would leave less is also rejected with `insufficient_time`. A next
state with no declared duty or collection still gets a whole window: its state
clock if it has one, otherwise the renewed timer's own window. In the review
pattern, `collect … @10m` followed by an untimed
`lead -> reviewers: summary() => complete` cannot be renewed past
`max_at` minus twenty minutes, so the summary always keeps ten. A timed duty,
for example `summary() @10m => complete | unsummarized`, states the
synthesis window explicitly. A state clock promises no specific reply and may
still be pre-empted by its enclosing contract clock. A contract renewal never moves its
maximum and may therefore supply less than a whole window near that ceiling.
No effect silently renews parent or sibling clocks.

The reference persists absolute renewed deadlines separately from state-entry
time. Checkpoint recovery reconstructs the same deadlines and processes any
elapsed timers before accepting further messages. Old sources retain their
compiled digests and state profile. Opt-in renewals use
`band-contract/renewal/1`, contract timeout data `band-contract/timeout/2`, and
`band-contract/state/3` (state/4 with participant types, or state/5 with room contribution controls); older readers must fail closed rather than ignore these
fields. See [portable coordination](coordination-profile.md).

Executable evidence: [renewal tests](../contract-lab/test/renewal_test.exs).
