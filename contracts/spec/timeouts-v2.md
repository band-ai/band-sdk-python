# Compact deadlines

The fixed-clock behavior below is the base case. Explicit [renewal effects](renewable-timeouts.md) opt into different clock behavior. For recoverable partial results and runtime rosters, use [collections](coordination.md); strict `each` retains its existing meaning.

This syntax applies to engine `0.2.0-lab.1`. Existing sessions keep their exact
source pins. The earlier `due … with … in` spelling was removed: use a timed
rule, or a timed event when the owed message should not leave the state.

## A message and its deadline

```band
"""
A reviewer owes the lead a review within five minutes.
"""
contract @30m => expired

"""
You asked for the review. Wait for it; the room ends the step if it is late.
"""
role lead

"""
Send your review within five minutes. A late review is not accepted.
"""
role reviewer

"""
Your findings, with the evidence behind them.
"""
type review

"""
The reviewer is reviewing; the review is due five minutes after this state
begins.
"""
state reviewing
  reviewer -> lead: review() @5m => collected | stalled
```

The reviewer owes an accepted review within five minutes of entering the
state. Acceptance strictly before the deadline selects `collected`; absence
at the deadline selects the terminal outcome `stalled`. Rejected messages,
progress events, reads, retries and restart MUST NOT reset the clock. Normal
payload, reference, actor and recipient checks still apply. `review()` keeps
its parentheses even when there are no reference conditions.

The compiler MUST expand the line into the ordinary transition followed by
its matching duty. It assigns the rule's normal source-order ID and then the
duty's next ID. Both retain the declaration's documentation and source line.
The resulting behavior digest equals the explicit rule-then-duty spelling;
the exact source digest differs. Existing completion capacity and full-window
admission checks therefore apply without a second runtime mechanism.

```band
"""
Two ways to wait for two reviewers: the first reply, or a reply from each.
"""
contract @30m => expired

"""
You collect the reviews. In first_reply one review moves on; in every_reply
wait for both.
"""
role lead

"""
You are one of two reviewers. Review independently and reply within five
minutes.
"""
role a

"""
You are one of two reviewers. Review independently and reply within five
minutes.
"""
role b

"""
A reviewer's findings, with the evidence behind them.
"""
type review

%% One accepted reply is enough:
"""
The first review from either reviewer completes this step.
"""
state first_reply
  {a,b} -> lead: review() @5m => collected | stalled

%% One accepted contribution from each sender is required:
"""
This step completes only when both reviewers have sent a review.
"""
state every_reply
  {a,b} -> lead: review() each @5m => collected | stalled
```

A single sender derives an `all` duty; a group with `each` derives `all`; a
group without `each` derives `any`. Selected role order is preserved. `as`,
`each` and `mentions`, when otherwise permitted, precede `@duration`. Group
contributions share one absolute deadline per state entry. An exact retry never
supplies another contribution.

This shorthand MUST only appear on an ordinary transition in a named state. It
is not supported on `anystate` or `room` rules. Durations are positive integers
with `s`, `m`, `h` or `d`; `infinite` and zero are not durations here. Both
branches are required and the timeout branch MUST name a terminal outcome.

When the owed message should not leave the state, put the deadline on an
event. A timed event is allowed only directly in a named state:

```band
"""
A worker acknowledges the job within five minutes and later sends the result.
"""
contract @1h => expired

"""
You hand out the job. An acknowledgement tells you the worker has it; wait for
the result.
"""
role lead

"""
Acknowledge the job within five minutes, then send the result when it is done.
"""
role worker

"""
The worker has the job and is starting on it.
"""
type ack

"""
The finished work.
"""
type result

"""
The job has started. The worker must acknowledge it in time and may then work
until the result is ready.
"""
state new
  event worker -> lead: ack() @5m => stalled
  worker -> lead: result() => done

end done

end stalled
```

The worker must acknowledge within five minutes of entering `new`. The event
derives the same duty as a timed rule, with the same quantifier: `each` makes
every selected sender owe the event, a single sender owes it, and a group or
roster without `each` needs one sender. The event never transitions. The duty is
owed **once per entry into its state**: one accepted event discharges it, it
does not re-arm, and it is not a recurring heartbeat. Repeats are accepted but
owe nothing. A new deadline starts only when the state is entered again. Missing
the deadline ends the session with the single outcome, which MUST be an `end`
terminal. A timed event has exactly one deadline and one outcome, and
`@infinity` is not a duration.

Executable evidence: [inline deadline tests](../contract-lab/test/inline_deadline_test.exs).

### A reply wait

The timeout alternative `| @d & outcome` that closes a
[reply block](contract-dsl.md#reply-alternatives) bounds a wait, not a state. Its clock starts when a head is accepted and enters
the wait state W, and the head's recipient then owes one reply before `d`. The
wait is its own scope:

- The first accepted alternative ends the wait. Leaving W by any other path
  ends it too, a host transition included: unlike a work duty, a wait never
  carries over.
- Each head that enters W, a re-send included, opens a new wait with a whole
  `d`. On a cycle, W's `max_entries` bounds how many there can be.
- A head whose whole `d` cannot fit before the contract cap is refused with
  `insufficient_time`, except a stop: an `anystate` head whose alternatives,
  timeout and every other way out of W (W's other exits, host triggers that
  apply in W, W's own clocks) all lead to `end` terminals is always admitted,
  and the cap ends its wait if it comes first ([defaults](defaults-v2.md)).
- The outcome must be terminal.
- At `d` the wait fires as a work duty with ID `due-M`, so it ties with other
  timers exactly as a work duty does ([equal-time order](#equal-time-order)).
- Rejected replies, progress events, reads, retries and restart do not move
  it. Only an explicit `@self` in the `state W` section renews it
  ([renewal](renewable-timeouts.md#renew-a-reply-wait)).

## A whole state

```band
"""
A reviewer reviews for the lead, and the whole review phase has five minutes.
"""
contract @30m => expired

"""
You asked for the review. If the phase runs out, the run ends as stalled.
"""
role lead

"""
Send your review before the five-minute phase ends.
"""
role reviewer

"""
Your findings, with the evidence behind them.
"""
type review

"""
The review phase. It ends as stalled five minutes after it begins.
"""
state reviewing @5m => stalled
  reviewer -> lead: review() => collected
```

The state MUST be left strictly before five minutes have elapsed since entry.
Any successful transition out cancels its clock. An ordinary transition back
into that state, including a self-transition, starts a new clock. Accepted
events, retries, reads and restore do not create a new entry. A bounded loop
may combine the clock with an entry limit:

```band
"""
A review phase that may run at most three times, for five minutes each.
"""
contract @30m => expired

"""
The review phase. Each entry has five minutes; a fourth entry ends the run as
exhausted.
"""
state reviewing @5m => stalled max_entries 3 => exhausted

end stalled

end exhausted
```

This is a limit on the phase, not a declaration that every possible outgoing
message is owed. It does not reserve message capacity by itself. Keep an inline
message deadline or a timed event when a specific reply is owed.
A declared duty's full response window MUST fit inside the state cap; a
shorter cap is a compile-time `duration` error. A whole-run cap can still end
a phase sooner, but MUST NOT shorten a newly admitted duty's response window.

The compiled state gains an optional `timeout` object, explicitly pinned to
`band-contract/timeout/1`, containing `after_ms`, `outcome` and `meta`.
Untimed states gain no field and keep their existing behavior digests. The
runtime derives the absolute phase deadline from the saved `entered_at`;
restore MUST NOT use restart time. Expiry has reason `state_timeout` and ID
`state-<name>`. Full and role-specific queries preserve the timeout and docs.

### A cumulative state budget

A per-entry clock restarts on every entry, so a loop through other states can
keep a phase going until the whole-run cap. `total` caps the state's time over
all of its entries:

```band
"""
A member works on a deliverable and may stop to ask the lead questions, within
a six-hour budget for the work.
"""
contract @12h => expired

"""
You assigned the work. Answer the member's questions promptly; time spent
waiting for you does not count against the member's budget.
"""
role lead

"""
Do the work and send the deliverable. Ask the lead when you are blocked rather
than guessing.
"""
role member

"""
The finished work.
"""
type deliverable

"""
A question that blocks the work.
"""
type question

"""
The lead's answer to the member's question; question names the question it
answers.
"""
type answer
  question: ref(question)

"""
The member is working. Each stay has four hours, and all stays together have
six.
"""
state working @4h => stalled total @6h => over_budget
  member -> lead: deliverable() => delivered
  member -> lead: question() awaits asking
    => answer() & working
     | @2h & unanswered

"""
The member is waiting for the lead's answer, which is due within two hours. At
most ten questions are asked.
"""
state asking max_entries 10 => exhausted
```

The session keeps each such state's dwell: the time of every completed entry,
from `entered_at` to the transition that left it. A self-transition ends one
entry and starts the next. Time in other states does not count. While the
state is current, its budget deadline is `entered_at` plus the total minus the
dwell; at that instant the session ends with the outcome, which MUST be an `end`
terminal. Expiry has reason `state_total` and ID `total-<name>`. Above, a member
who asks at 3.5 h and is answered at 5 h has 2.5 h left, so the run ends
`over_budget` at 7.5 h instead of `stalled` at 9 h. The question is a request
with reply alternatives: the lead owes the answer within two hours
([a reply wait](#a-reply-wait)), or the run ends `unanswered`, and that wait
never counts against `working`.

The budget is checked again at each entry, like an entry limit: if the remaining
budget cannot hold a declared duty or collection window of the state, the entry
goes to the total outcome instead, so no promised window is cut short. Renewals
never move the budget, and a rule renewal whose new deadline would pass it is
refused with `insufficient_time`. The total MUST be at least the per-entry clock
and every declared window of the state, otherwise compilation fails with
`duration`. The compiled state gains `total` (`band-contract/total/1`) and the
checkpoint gains `dwell` only when a state declares a total, so other contracts
keep their behavior digests and checkpoint shape. Restore uses the saved
`entered_at` and dwell; downtime is never credited.

Executable evidence: [state total tests](../contract-lab/test/state_total_test.exs).

### Equal-time order

Earliest absolute time wins. At equal times the order is:

1. a work duty (`due-N`), a reply wait's timeout included;
2. a collection deadline (`collection-<name>`);
3. an offline grace (`offline-<name>/<member>`);
4. a state timeout (`state-<name>`) or state budget (`total-<name>`);
5. a host trigger (`host-N`, or `removed:<role>` for the implicit removal of a
   bound agent under `host.membership/1`; `host-…` sorts before `removed:…`);
6. the whole-run cap (`maximum`).

IDs compare lexicographically within a category: `due-10` fires before `due-2`
at the same instant ([V2-TIE-ORDER](requirements-v2.json),
[checkv2](tools/checkv2.py)). A timer fires at its scheduled
`at`, and the remaining due timers are then processed again in this order. A
collection that closes at the same instant as the run cap therefore ends
`expired` only when it moves to a nonterminal state. A terminal collection
outcome stands, because a finished session runs no further timers. All due
timers run before admitting a message; equality is late. No clock consumes a
business-message credit. The
[portable coordination assertions](tools/checkcoordination.py) pin each
adjacent pair and both collection/cap cases.

A nonterminal host transition cancels the old phase clock and starts the
destination's phase clock at the trigger's scheduled time, even when the
trigger is observed later. Every timer-driven entry saves its timer's `at` as
`entered_at`; downtime or scheduler lag never grants a phase extra time. Its
existing rule for preserving outstanding work duties still applies; a reply
wait is not a work duty and ends with its wait state. If a
carried duty cannot fit before the destination phase or whole-run cap, the
session terminates as `insufficient_time`.
Clock arithmetic MUST remain within the JSON safe integer range at creation,
ordinary admission and host transition.

Executable evidence: [state timeout tests](../contract-lab/test/state_timeout_test.exs).

## A whole contract run

```band
"""
A reviewer reviews for the lead, and the whole review phase has five minutes.
"""
contract @30m => expired

"""
You asked for the review. If the phase runs out, the run ends as stalled.
"""
role lead

"""
Send your review before the five-minute phase ends.
"""
role reviewer

"""
Your findings, with the evidence behind them.
"""
type review

"""
The review phase. It ends as stalled five minutes after it begins.
"""
state reviewing @5m => stalled
  reviewer -> lead: review() => collected
```

The contract clock starts when each session is created, including any waiting
in `new`. It MUST NOT restart on a state change, loop, retry or recovery.
Earlier completion cancels pending expiry. The declaration is mandatory: a
source without it fails with `contract_clock_required`, and
`contract @infinity => expired` is the explicit opt-out with no cap. A custom
terminal outcome may replace `expired`. The static contract definition itself
does not expire; each run has its own clock.

This declaration resolves to `card.lifetime` and sets the session's `max_at`,
its current whole-run expiry ([defaults](defaults-v2.md)). A renewable
declaration also records its fixed maximum in `clocks.hard_at`
([renewal](renewable-timeouts.md)). The header `max-duration` and `lifetime`
fields were removed and are rejected as unknown fields. Contract documentation
still attaches to `contract`; there is only one such declaration per file.

A fixed declaration such as `contract @30m => expired` also adds a `timeout`
object with profile `band-contract/timeout/1`, duration, terminal outcome and
metadata. Full and role-specific queries preserve it, and it is included in the
behavior digest. `contract @infinity => expired` adds no `timeout` object.
Expiry has reason `lifetime` and ID `maximum`.

An initial duty longer than a body-declared cap MUST fail compilation with
`duration`. Later admission keeps the existing `insufficient_time` check for
new duties whose full window cannot fit. A phase cap without a duty promises
no minimum window and may be pre-empted by the earlier whole-run cap.

Adding `contract @infinity => expired` to a file that had no clock keeps its
behavior digest; only the source digest changes. Implementations must support
these declarations before installing such a source; existing session
definitions are never replaced.

Executable evidence: [contract timeout tests](../contract-lab/test/contract_timeout_test.exs).
