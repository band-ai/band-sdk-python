# One session, one initial entry

This extension applies to `compiler/2`, engine `0.2.0-lab.1`. Each session
starts in the required state `new` exactly once. The compiler MUST reject
`new` as a transition target, including a self-transition, a transition from
another state, an `each` transition, and an `anystate` transition. A deadline
or state-entry limit MUST NOT name `new` as its outcome either. The diagnostic
code is `initial_state_target`. Other declared active states can still have
explicit, bounded iteration where the workflow requires it.

A simple question/answer contract transitions `new -> answering -> complete`.
After completion, the next question starts a **new session**, with a new
session identifier and the source selected for that creation. An accepted
operation's exact retry remains a retry of its original session; creating a
new session is not a substitute for recovering a lost receipt.

A request that waits for its answer is written with
[reply alternatives](contract-dsl.md#reply-alternatives), and its answer always
names the request: the answer type declares one `ref` field to the request's
kind, and the compiler supplies the binding and the match when the author
writes neither. The engine never infers the request from the session's state,
even when the session holds exactly one question. Writing `as request` and
`request=$request` explicitly is optional. Use it when the protocol needs to
identify a particular earlier message, such as selecting among several
proposals. An explicitly declared `ref(...)` field still requires its matching
rule and visibility checks.

[The entry-state tests](../contract-lab/test/entry_state_test.exs) exercise
forbidden targets, terminal closure and new session creation.

## A single participant's deadline

```band
"""
An assistant answers one question within five minutes.
"""
contract @30m => expired

"""
You asked the question. Wait for the answer.
"""
role requester

"""
Answer the requester's question within five minutes. Say what you are unsure of.
"""
role assistant

"""
The requester's one question.
"""
type question

"""
The assistant's answer to the requester's question; request names that question.
"""
type answer
  request: ref(question)

"""
The requester asks its one question. The assistant owes an answer to it within
five minutes.
"""
state new
  requester -> assistant: question() awaits answering
    => answer() & complete
     | @5m & stalled

"""
The assistant is preparing the answer.
"""
state answering

"""
The answer arrived. Whether it is correct is still the requester's judgment.
"""
end complete

"""
No answer arrived in time; the requester decides how to recover.
"""
end stalled
```

Read the request as: **the requester sends a question and waits in `answering`;
an answer to that question ends the run `complete`, and with no answer within
five minutes it ends `stalled`.** Once the question is accepted, the assistant
owes an accepted `answer` within five minutes. Acceptance before that absolute
deadline ends the wait; if no answer is accepted by then, the session ends as
`stalled`. Equality is too late. Reads, retries, restart and transport
heartbeats do not reset that deadline. An `answer` still has to pass its normal
rule, payload, actor, reference and state checks; receipt of arbitrary traffic
is not satisfaction.

Write every request that waits for its answer this way, with the answer as an
alternative under the request. A separate answer rule in the request's target
state is refused with `reply_required`.
[A reply wait](timeouts-v2.md#a-reply-wait) defines the wait's clock beside the
two wider timeout scopes. When an owed message should not leave the state, put
the deadline on an event: `event assistant -> requester: answer() @5m => stalled`
derives a single-role duty, and an accepted answer discharges it without
a transition. The answer is owed once per entry into the state, not repeatedly.
A group timed event requires every named role with `each`, as in
`event {a,b} -> lead: review() each @5m => stalled`, and any one role without
it. The earlier `due … with … in` spelling, including `all of` and `any of`, is
a `syntax` error.

The [complete assistant example](../contract-lab/contracts/v2/assistant-answer.band)
shows one question and one answer. The
[reply wait tests](../contract-lab/test/reply_wait_test.exs) check the wait's
timeout and tie order, and the [deadline tests](../contract-lab/test/single_due_test.exs)
check owed work on a timed rule and restart with an outstanding duty.

For groups, follow the [two-reviewer walkthrough](groups-and-events-v2.md).
It combines the success rule and deadline and traces the first reply, second
reply, timeout and optional progress updates. A deadline alone does not make an
ordinary transition wait for everyone; that requires `each` on the message rule.
