# Start with one multi-agent skill

An **agent** is a software participant that can send messages and do work.
Here, one agent asks another to review a change. The question is simple:
**when may the requester treat that review as received?**

A **multi-agent skill** includes prompts and guidance about how to do the work,
plus a **contract** the room can enforce. The guidance explains the roles,
evidence and judgment. The contract writes down allowed messages, stages,
outcomes and deadlines. A **session** is one job following that skill. The
**host** checks participants, saves accepted messages and delivers them.
Ordinary human conversation stays outside this contract.

## Tell the story before reading the syntax

1. The lead agent sends a request. The job starts waiting for a review.
2. The reviewer sends a valid review before five minutes have passed. The job
   ends as `collected`.
3. If the review is still missing at five minutes, the job ends as `stalled`.

`collected` means the report arrived through the allowed process. It does not
mean the report is correct, that it approved the change, or that the change
has been deployed. Those need their own checks.

## The whole skill

The [runnable file](../contract-lab/contracts/v2/one-review.band) is deliberately
small. Each `"""` prompt becomes prompt metadata for the item immediately
below. They teach the agents their roles and explain each stage.
The opening block identifies the skill, and the rules constrain coordination.

```band
---
name: one-review
description: One agent asks another to review a change and report evidence.
engine: 0.2.0-lab.1
roles: [lead, reviewer]
---
"""
Coordinate one independent review. Collected means a report arrived, not that
its conclusions are correct or the change is approved.
"""
contract @1h => expired

"""
Give the reviewer a specific change, its context and the question to answer.
"""
role lead

"""
Inspect the supplied change. Explain actionable findings with evidence,
state uncertainty and distinguish untested behavior from verified behavior.
"""
role reviewer

"""
The task and enough context to review it.
"""
type request
  text: text(1..2000)

"""
Findings, evidence, scope checked and any remaining uncertainty, naming the
request they answer.
"""
type review
  request: ref(request)
  text: text(1..4000)

"""
The lead starts this job by giving the reviewer its task.
"""
state new
  """
  Send the change, its context and the question. The reviewer then owes one
  report, and that report decides the outcome.
  """
  lead -> reviewer: request() awaits reviewing
    """
    Submit the report within the response window.
    """
    => review() & collected
     """
     No report arrived within five minutes of the request.
     """
     | @5m & stalled

"""
The reviewer prepares its report; the request's reply window bounds the wait.
"""
state reviewing

"""
The lead has a report to evaluate, not an automatic approval.
"""
end collected

"""
No valid report arrived in time; the lead must decide how to recover.
"""
end stalled
```

Read `lead -> reviewer: request() awaits reviewing` as: **the lead may send a
request to the reviewer; accepting it moves this job to `reviewing`, where it
awaits the reply**. The message's actual text travels in a separate payload. The empty parentheses
mean there is no extra condition on an earlier message; they do not mean that
the request has no content.

A `state` names the job's current stage. `new` is its initial stage and cannot
be revisited. An `end` names a final outcome. A `type` defines the fields of a
message; here, each message carries one text field, and a review also names
the request it answers. `text(1..2000)` requires
between one and two thousand UTF-8 bytes. A byte measures encoded size; it is
not necessarily one displayed character.

Read the two alternatives under the request, `=> review() & collected` and
`| @5m & stalled`, as: **a review accepted within five minutes of the request
finishes the job as collected; otherwise it finishes as stalled**. `&` reads
"leads to", `|` starts the next alternative, and the timeout always comes last.
This is the form of UBF, where each possible reply leads to its own next state.
Time does not restart when someone checks progress, retries delivery or
restarts the application. A reply at the exact deadline is too late. Waiting in
`new` creates no work timeout.

A review counts only for the request it names; the room never guesses from the
order of messages. `review` has one `ref` field to `request`, so the compiler
fills in that check: the review's `request` must be this request's ID. Writing
it out is also legal: `request() as req awaits reviewing` with
`=> review(request=$req) & collected`. A review type with no such field, or
more than one, fails with `reply_correlation` until the reference is written.
`awaits reviewing` names the waiting stage so it can have its own prompt. Leave
`awaits` out and the stage is named after the request, `request_wait`, and the
prompt above the request describes it.

The `contract` line sets the whole-run clock, which every skill declares.
Read `contract @1h => expired` as: **any run still open one hour after it was
created ends as `expired`**. Work that is deliberately open-ended opts out
explicitly with `contract @infinity => expired`. A source without the clock
fails to compile with `contract_clock_required`.

## Let the reply decide the outcome

Often the lead needs to know more than whether a review arrived. It needs to
know what the review decided. The alternatives can tell replies of one kind
apart by a field's value, one alternative per outcome.

```band
---
name: one-review-decision
description: One agent asks another to review a change; the decision in the review picks the outcome.
engine: 0.2.0-lab.1
roles: [lead, reviewer]
---
"""
Coordinate one independent review. The reviewer's decision picks the outcome;
it records the reviewer's judgment, not a deployment approval.
"""
contract @1h => expired

"""
Give the reviewer a specific change, its context and the question to answer.
"""
role lead

"""
Inspect the supplied change and decide: approve it, or ask for changes and say
which ones, with evidence.
"""
role reviewer

"""
The task and enough context to review it.
"""
type request
  text: text(1..2000)

"""
Your decision on that request, with the findings and evidence behind it.
"""
type review
  request: ref(request)
  decision: enum(approve|revise)
  text: text(1..4000)

"""
The lead starts this job by giving the reviewer its task. The reviewer owes one
review within five minutes, and its decision picks the outcome.
"""
state new
  """
  Send the change and the question. While you wait, the reviewer owes one
  review of this request within five minutes.
  """
  lead -> reviewer: request()
    => review(decision=approve) & approved
     | review(decision=revise) & changes_requested
     | @5m & stalled

"""
The reviewer approved the change. This is the reviewer's judgment, not proof
that the change is correct.
"""
end approved

"""
The reviewer asked for changes; the review says which.
"""
end changes_requested

"""
No valid review arrived in time; the lead must decide how to recover.
"""
end stalled
```

Read the request and its three alternatives as: **send the request, then
await one review. A review that approves ends the job as `approved`, and a
review that asks for changes ends it as `changes_requested`. With no valid
review by five minutes, the job ends as `stalled`.**

`decision=approve` matches only reviews whose `decision` is `approve`. Together,
the alternatives must cover every review the reviewer could send: leaving out
`revise` is a compile error (`reply_not_exhaustive`), and so is listing a
decision twice (`reply_overlap`). The reviewer never names a state; the review
it sends decides. This request has no `awaits`, so while it waits the run is in
`request_wait`, and the prompt above the request documents that stage. A
request that can be sent again, such as a retry, works the same way: the
binding moves to the new request, so a late reply to the old one is refused.
The [reply alternatives](contract-dsl.md#reply-alternatives) section shows a
retry loop.

## Add syntax only for a real requirement

- Need both reviewers? A request with alternatives has one recipient, so use a
  timed rule with `each`, as the
  [two-reviewer walkthrough](groups-and-events-v2.md) shows with its complete
  trace, or a [collection](coordination.md).
- Need a different whole-run limit? Change `@1h`, or let a heartbeat renew it
  up to a maximum. Bound one phase with `state reviewing @5m => stalled`; see
  [timeout scopes](timeouts-v2.md).
- Need progress without finishing? Add an `event` rule.
- Need a field's value to pick the next state? Give each alternative a
  literal, as `decision=approve` does above.
- Need a later message to point at an earlier one? Add an `as` binding and a
  `ref` field, as a review's `request` field does.
- Need a bounded format check? Add a [restricted pattern](safe-patterns.md)
  written with its `~r` sigil, such as `regex: ~r/[A-Z]+/`.
- Need a prompt that contains backslashes or `#{`? Write `\\` and `\#{` inside
  the `"""` block; the [sigil guide](sigils-v2.md#raw-documentation-s) shows one.

Prefer `5m`, a single role name, `enum(approve|revise)` and a short `{a,b}`
group when they already say what you mean. Do not mechanically wrap values in
sigils. There are three supported sigil families, each with one purpose; this
profile does not add a custom sigil language for workflow operations.

## What the application must still do

The contract checker decides whether a proposed message follows the rules.
The host must verify the sender, check current access, save acceptance durably,
and make the accepted message available to its intended recipients. A saved
**receipt** records that decision so an exact retry can recover it without
doing the transition twice. Sending a network request alone does not prove
acceptance, and acceptance alone does not prove an external action happened.

Use the [reviewed profile](profile-v2.md) as the complete specification index.
The syntax, local reference implementation, host integration and production
deployment are separate deliverables. Passing the local examples establishes
only the behavior of that tested implementation.

For runtime participant lists and useful partial outcomes, see the [multi-agent skill guidance](multi-agent-skills.md), [participant types and collections](coordination.md) and [contribution outcomes](contribution-outcomes.md). For explicitly renewable clocks, see [renewal](renewable-timeouts.md).
