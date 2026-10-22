# Collecting replies and setting a deadline

The fixed-clock behavior below is the base case. Explicit [renewal effects](renewable-timeouts.md) opt into different clock behavior. For recoverable partial results and runtime rosters, use [collections](coordination.md); strict `each` retains its existing meaning.

Imagine a lead **agent** asks reviewer agents A and B (roles `a` and `b`)
to examine a proposal. The
workflow reaches a stage called `reviewing`. Decide two things: how many reports
let it finish, and how long it may wait. A report counts only after the engine
accepts its sender, message type and payload.

## One review is enough

```band
"""
Ask two reviewers for a review; the first accepted review closes the step.
"""
contract @30m => expired

"""
You asked for the review. Read whichever review arrives first.
"""
role lead

"""
You are one of two reviewers. Send your review within five minutes; a review
from the other reviewer may already have closed the step.
"""
role a

"""
You are one of two reviewers. Send your review within five minutes; a review
from the other reviewer may already have closed the step.
"""
role b

"""
A reviewer's findings, with the evidence behind them.
"""
type review

"""
One review from either reviewer is enough to move on.
"""
state reviewing
  {a,b} -> lead: review() @5m => collected | stalled
```

Read the message rule as: **either reviewer may send a review to the lead;
the first accepted review finishes collection**. A sender list offers a choice
of sender. It does not itself mean "wait for all of them".

Read the deadline as: **at least one reviewer must supply an accepted review
before five minutes have elapsed, or the session ends as `stalled`**.
If A replies after one minute, the session ends as `collected`; it does not
wait for B. `collected` means a report arrived, not that its conclusions are true
or that anyone approved the proposal.

## Both reviews are required

```band
"""
Ask two reviewers for a review; the step closes when both have reviewed.
"""
contract @30m => expired

"""
You asked for the reviews. Wait until both have arrived before you act on them.
"""
role lead

"""
You are one of two reviewers. Review independently; the step waits for your
review as well as the other reviewer's.
"""
role a

"""
You are one of two reviewers. Review independently; the step waits for your
review as well as the other reviewer's.
"""
role b

"""
A reviewer's findings, with the evidence behind them.
"""
type review

"""
Each reviewer owes one review within five minutes.
"""
state reviewing
  {a,b} -> lead: review() each @5m => collected | stalled
```

`each` changes the message rule to: **accept one review from A and one from B;
finish collection only after both have arrived**. The first reply is saved while
the workflow stays in `reviewing`. A second new review from A cannot substitute
for B; it is rejected as `already_contributed`. An exact retry of A's original
message returns its original receipt without counting again.

`@5m` with `each` adds: **if either report is still missing when five minutes have
elapsed, end as `stalled`**. The clock starts when the session enters `reviewing`.
One review does not give the other reviewer a fresh five minutes.

| Time since review started | Accepted input | Result |
|---|---|---|
| Start | Lead's request | Wait for A and B; deadline is five minutes |
| One minute | A's review | Save A's report; still wait for B |
| Three minutes, success branch | B's review | End as `collected` |
| Five minutes, alternative branch where B never replied | No review from B | End as `stalled`, identifying B as missing |

The last two rows are alternative outcomes. A review arriving exactly at the
deadline is too late: elapsed deadlines are processed before new proposals.

`each` sets the completion checklist; `@5m` sets its deadline. The compiler
derives an `all` duty from `each`, or an `any` duty without it. **A deadline
does not turn a message rule into a wait-for-everyone rule**: without `each`,
the first review takes the ordinary transition; leaving the state removes its
old duties. Conversely, `each` alone supplies no deadline. Each later entry to
the same stage starts a fresh contribution checklist; earlier accepted reports
are retained as history and do not count as new contributions.

The [complete example](../contract-lab/contracts/v2/two-reviewers.band) declares
the roles, message fields, starting request and terminal outcomes. Its host must
provide finite message capacity for the request, both reports and any permitted
progress updates. The engine reserves the remaining duty cost and, for an
event, the cheapest closing path ([reserve](defaults-v2.md#reserve-known-response-capacity)).

## A progress update need not finish the review

Add the `event` rule inside `state reviewing`:

```band
"""
Ask two reviewers for a review and let them report progress while they work.
"""
contract @30m => expired

"""
You asked for the reviews. Progress notes tell you work is moving; only the two
reviews complete the step.
"""
role lead

"""
You are one of two reviewers. Send short progress notes while you work, and
your review within five minutes.
"""
role a

"""
You are one of two reviewers. Send short progress notes while you work, and
your review within five minutes.
"""
role b

"""
A reviewer's findings, with the evidence behind them.
"""
type review

"""
A short note on where the review stands. It never replaces the review.
"""
type progress

"""
Each reviewer owes one review; progress notes are optional along the way.
"""
state reviewing
  {a,b} -> lead: review() each @5m => collected | stalled
  event {a,b} -> lead: progress()
```

For example, B may send "Still checking the tests." The accepted update is
recorded and routed to the lead, while the workflow stays in `reviewing`.
`event` means **accept this message without changing the workflow stage**;
there is no `=>` destination. It still uses ordinary message and recipient
capacity, and it is refused with `completion_capacity` rather than spend the
reviews still owed or the lead's closing message. Because `progress` is a different message type from `review`, it
does not count as a review or satisfy that duty, and it does not renew the deadline.

An event *can* satisfy a duty for its own message type. Satisfying a duty only
removes that deadline obligation; it does not itself choose the next stage.

## A room notice is outside a particular session

This is a separate optional host feature, unrelated to collecting both reviews:

```band
"""
A run in which one participant may tell the lead about events at any time.
"""
contract @30m => expired

"""
You receive notices. Read each one and decide whether it changes your work.
"""
role lead

"""
Tell the lead when something it should know about happens, whatever stage the
run is in.
"""
role a

"""
A short report of something that happened and why it matters to the lead.
"""
type notice

room
  event a -> lead: notice()
```

For example, a reviewer agent can tell the lead agent "The daily digest is ready"
without starting a review session. `room` groups rules for these messages.
Their envelope has no session ID; they neither create nor advance a session.
They go only to their declared recipients, not automatically to the whole room.
The host supplies role bindings, authorization and separate room-event quotas.

A session `event` belongs to one session and can affect its facts and matching
duties. A `room` event belongs to no session. Neither keyword creates a Band room.
The reference room-event handler is separate from the session handler; the
current Band session routes do not automatically expose this optional feature.
Human conversation remains outside agent contracts in both cases.

The compact spelling is specified in [timeout scopes](timeouts-v2.md) and
expands to the existing message rule and duty behavior.
The reference implementation is in `Engine.commit/7`, `Engine.enter/3`,
`Engine.discharge/3`, and `RoomEvents.step/3`. The preserved base requirements
are in [accepted transitions](engine.md#accepted-transition) and
[room events](engine.md#sessionless-room-events).
