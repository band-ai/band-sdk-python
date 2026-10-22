# Recovery defaults in the new profile

Engine `0.2.0-lab.1` adopts the following parts of the
[defaults review](design/recovery-first-defaults.md). These are core
implementation semantics; the separate host-integration requirements still
need host qualification.

## A short discovery card

```yaml
---
name: assistant-answer
description: One agent answers another agent, then the session ends.
engine: 0.2.0-lab.1
version: 0.4.1
roles: [requester, assistant]
---
```

Only `name`, `description`, `engine` and `roles` are required. Author `version`
and `encoding` are optional. No generic lifetime, inactivity limit or mention
cap is silently imposed. `encoding` resolves to `utf-8`. The
frontmatter delimiter is the boundary between discovery and the actual body.
Ordinary lowercase `contract`, `role`, `type`, `state`, `end`, `anystate` and
`room` declarations are the only spelling; plus-prefixed declarations were
removed.

Every contract body MUST declare its clock. `contract @30m => expired` means
at most thirty minutes **from session creation**. A source without a body
`contract @…` line fails with `contract_clock_required`. A bare `contract`
line does not count, and the header `max-duration` and `lifetime` fields were
removed and are rejected as unknown fields. `contract @infinity => expired` is the
explicit opt-out: that session has no age-based expiry. The normalized card
retains the internal `lifetime` member. A session's `max_at` is its current
whole-run expiry: the fixed cap of `contract @d`, the current window of a
renewable `contract @w max @h` (never later than `clocks.hard_at`, its
immutable maximum), or null for `contract @infinity`. The header
`inactivity-timeout` field was removed and is rejected as unknown. The host
need not choose an inactivity-reset policy for this profile, because generic
idle expiry is absent.

Mention caps are optional and apply to one message at a time. With no cap, a
rule may mention every principal it names. `mentions N` on the `contract` line
sets a default cap, and the same option on a rule overrides it for that rule;
`mentions infinity` lifts it. `mention N` is the same option, written for
English agreement:

```band
---
name: review-fanout
description: Brief the reviewers, collect their reviews, then report once.
engine: 0.2.0-lab.1
roles: [author, lead, reviewers]
---
"""
Brief a group of reviewers once, collect a review from each of them, then send
the author one combined report. Each message reaches one person unless its rule
says otherwise.
"""
contract @1h mention 1 => expired

"""
You asked for the review. Wait for the lead's report; you do not talk to the
reviewers directly.
"""
role author

"""
You run the review. Brief every reviewer once, read each review, then send the
author one report that keeps disagreements visible.
"""
role lead

"""
Review from your specialty. Cite evidence and say what you did not check.
"""
type reviewer: agent
  specialty: enum(security|general)

"""
The reviewers for this run; the lead briefs all of them in one message.
"""
reviewers: list(reviewer)

"""
The change to review and the question each reviewer should answer.
"""
type request

"""
One reviewer's findings, with evidence and the limits of what was checked.
"""
type review

"""
The combined result for the author: agreed findings, disagreements and gaps.
"""
type report

"""
The lead briefs all the reviewers at once.
"""
state new
  lead -> reviewers: request() mentions 8 => reviewing

"""
Every reviewer owes one review within thirty minutes.
"""
state reviewing
  reviewers -> lead: review() each @30m => reporting | stalled

"""
All the reviews are in. The lead reports to the author once.
"""
state reporting
  lead -> author: report() => done

end done

end stalled
```

Each message here reaches one principal, except the request, which may reach up
to eight reviewers. A message counts its delivered principals: recipients with
rosters expanded to bound members, deduplicated, sender excluded. The compiler
rejects a fixed-recipient rule over its cap with `mention_limit`. A roster rule
is checked when the session is created: binding nine reviewers here fails with
`mention_limit` and creates no session. Admission checks every message again.
There is no session-wide mention budget, and the header `mentions-per-message`
and `mentions-per-session` fields are rejected as unknown.

The host MUST still supply a finite accepted-message capacity at creation, and
retains an independent envelope byte bound. These operational limits do not
determine how long promised work has to complete. Session configuration exposes
the resolved values.

`expired` is an available reserved terminal outcome without a boilerplate
declaration, so the contract clock always has a valid result. Authors
may explicitly document it. It cannot also name an active state.

## Recover work, not silence

An agent-to-agent request that waits for its answer is written with
[reply alternatives](contract-dsl.md#reply-alternatives), such as
`requester -> assistant: question() awaits answering` followed by
`=> answer() & complete` and `| @5m & stalled`. The wait starts when the
question is accepted, and at five minutes it fires as a work duty
([a reply wait](timeouts-v2.md#a-reply-wait)). A separate answer rule in the
request's target state is refused with `reply_required`. When an owed message
should not leave the state, the timed event
`event assistant -> requester: answer() @5m => stalled` derives a duty on state
entry. No heartbeat, read, retry or restart extends either. Quiet initial waiting creates
no work timeout. Human conversation remains outside contract sessions; a
requesting agent may initiate this workflow on a human's behalf, but the human
does not acquire a contract role or obligation.

Before admitting a message, the engine MUST check the duties that would remain
after it. Entering another state MUST be refused with `insufficient_time` if
any newly created duty's full response window cannot fit before `max_at`,
the current expiry; a renewal that has not happened yet does not count.
The cap is never silently used to shorten the advertised window. At equal
timestamps this profile selects the specific duty before the general cap
([equal-time order](timeouts-v2.md#equal-time-order)); equality remains too
late for admission. A stop is always admitted: a stop is an `anystate` head whose
alternatives, timeout and every other way out of its wait state (that state's other exits,
host triggers that apply there and its own clocks) all lead to `end` terminals, the reply
wait it opens is exempt from this check, and if that wait runs past `max_at` the cap ends
the run with its own outcome.
The stop's other checks, such as reply capacity, still apply, and the wait of any other head,
`anystate` included, must fit.
An optional state cap uses `state answering @5m => stalled`. It is a phase
limit and does not itself declare which reply is owed or reserve reply
capacity. [Timeout scopes](timeouts-v2.md) specifies phase clocks, versioned
compiled data and the full ordering when state and host timers coincide.

## Reserve known response capacity

The engine MUST retain capacity for declared remaining duties while deciding
each new message. For each `all` duty, reserve each remaining role/kind pair;
for each `any` duty, use the first remaining participant as a deterministic
completion witness. Deduplicate those pairs across duties. Count one message
per pair. Add that reserve to the proposed message's charge before comparing
with the remaining host message capacity. Mention caps are per message, so they
reserve nothing.

Each remaining duty also reserves its **follow-on**: what its reply's own
admission will reserve, so the engine never accepts a duty whose reply it would
then refuse. A reply that enters another state reserves that state's declared
duties the same way, follow-ons included. A collection reply does this for the
collection's target; with a [quorum](coordination.md) it takes the worse of the
two targets, because the members' results, not a sender, decide which one the
close enters. A timed event reply stays in its state, so it reserves that
state's closing path (below) with the other duties still covered. A reply that
ends the session reserves nothing more. Count each duty's follow-on once, however
many of its pairs remain. A follow-on that leads back into the same cycle of
owed replies counts zero: two states are on one cycle when each can reach the
other through owed replies, and a reply that re-enters its own state is a cycle
too. A follow-on that leaves the cycle still counts.

A [reply wait](contract-dsl.md#reply-alternatives) owes one reply, whichever
alternative it turns out to be, so it reserves **`1 + max R(target)`** messages
over its alternatives' targets, not one per alternative. `R(t)` is the reserve
that entering `t` would create under these rules, follow-ons included: 0 for a
terminal, and 0 for a target on the same cycle of owed replies. Alternatives of
the same kind count separately in the maximum. The reserve is charged once per
wait and participant, not once per kind. The wait state's other duties, and an
event's closing path, are reserved as above. Like the rest of this section, it
reserves message count only: it guarantees neither the largest payload nor that
the run can always complete.

Outside such cycles, every owed reply then fits when it is the next message: the
reserve held when its duty was created is at least the reply plus what the reply
must itself keep, and nothing else admitted in between can spend it. Inside a
cycle each turn reserves only the replies it owes, so near the cap the last owed
reply of a cycle can still be refused. A finite budget cannot promise an
unbounded chain of replies.

An event does not leave its state, so it also reserves the **closing path**:
the fewest accepted messages that can still reach any terminal from the
current state. A message rule on that path counts one message per required
sender (`each` counts every sender), the rule of a duty already reserved above
counts none, and a collection close or a timer into another state counts none.
A collection close with a quorum costs the worse of its two targets.
A timer into a terminal is a failure path, not a closing path, and host
triggers are not guaranteed to fire, so neither shortens it. With no message
path to a terminal the closing reserve is zero. A collection reply answers a
reserved duty and does not add it.

Refuse with `completion_capacity` if the charge and reserves cannot fit. This
stops unrelated events, progress updates and heartbeats included, from spending
a promised reply's last credit or the untimed move that ends the session. The
final valid answer can consume the last available unit.

For example, a teammate who has sent many progress notes asks a human a
question. The question owes a relayed answer, and the answer re-enters work that
owes a delivery, so the question reserves 2. If only the answer fits, the
question is refused `completion_capacity`, and the teammate can still deliver.
Without the follow-on the question would be accepted and the owed answer
refused.

The two refusals stay distinct. `message_budget` ([engine step 9](engine.md))
means the accepted-fact count already equals `max_messages`; it is checked
first. `completion_capacity` (step 10) means there is room for this message but
not for it together with the reserves. Only events reserve the closing path, so
a message rule that moves the session may spend the last unit. After that, while
the session stays active, every proposal that passes steps 1-8 and step 9's
recipient and mention checks is refused with `message_budget`. An event never
spends the last unit while a message path from its state still reaches a
terminal. With no such path its closing reserve is zero, so events can spend
the last unit too and later proposals reach `message_budget`.

This is a conservative reservation for declared responsibilities and the
cheapest closing path, not a proof of the resource cost of arbitrary future
branches; the engine cannot tell which terminal the authors consider success. A different satisfying
`any` participant can have a different fan-out: its own proposal is checked
against the actual remaining capacity. Templates with more complex completion
costs need a host reservation plan. Deadline failure remains possible even if
no business-message credit remains; timer advancement does not consume one.

The reserve counts messages, not bytes, so creation MUST also refuse with
`completion_size` when `max_bytes` cannot carry a promised reply. For each
reply kind that a duty or a collection in any state requires, and for every
reply alternative, compute the smallest canonical envelope: a 1-byte ID, the session's own ID, the kind, no
`expected_version`, and each payload field at its shortest value. Text takes the
larger of its byte and grapheme minimums as bytes, an integer its minimum, a
boolean `true`, an enum its shortest value and a reference 1 byte; a field an
alternative fixes with a literal takes that literal. If any such envelope
exceeds `max_bytes`, no reply of that kind (or that alternative) can be
admitted, so the duty could only end at its deadline. Checking each alternative
stops a byte cap from quietly removing `ok` and leaving only `error`. This is a lower bound, and an envelope of
exactly `max_bytes` fits.

Hosts MUST retain bounded storage and an authorized control path for durable
failure and cancellation, separate from business-message admission. A reducer
decision is not evidence that a database has reserved that storage. Hosts also
validate support for the contract's permitted fan-out at creation, preserve
absolute clocks, and reauthorize actors. Those obligations are part of host
qualification, not a hidden default in the discovery card.

[Tests](../contract-lab/test/recovery_defaults_test.exs) show quiet waiting,
restart, duty failure, insufficient answer capacity, the cap boundary, equal
timers and equivalent declaration spellings. The requested defaults first
produced six failures against the preceding implementation.
[Follow-on tests](../contract-lab/test/follow_on_reserve_test.exs) cover the
room whose owed human answer was refused, a chain into three owed reviews, a timed
event's closing move, cycles of owed replies and a collection's quorum target.
