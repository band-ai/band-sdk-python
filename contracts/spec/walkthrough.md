# A worked contract session

This explanatory example follows two checked replays of the
[hybrid assistant](../contract-lab/contracts/v2/hybrid-assistant.band):
[the main path](../contract-lab/scenarios/v2/hybrid-assistant-walkthrough.json)
and [the deadline branch](../contract-lab/scenarios/v2/hybrid-assistant-deadline.json).
Use those files' complete requests and expected results when implementing; the
tables below show the decisions that are easiest to misinterpret.

The contract binds two agent roles, `requester` and `assistant`. A requester asks
one question, and an assistant answers that exact accepted question. The requester
may then ask again or stop. The contract controls who may speak, the payload shape,
the reference and the deadline. It does not decide whether “pong” is a useful
answer to “ping”.

## From source to a session

The frontmatter selects engine `0.2.0-lab.1` and the two roles. The body caps
the whole run at thirty minutes from creation. There is no idle timeout and no
[mention cap](contract-dsl.md#mention-caps); each rule here names a single
recipient. The body's declarations are below; attached documentation is omitted
from this excerpt. Use the linked file's exact bytes to reproduce its digests.

```band
"""
Answer a requester's questions one at a time until the requester stops. Each
answer is due within five minutes, and the whole conversation has thirty.
"""
contract @30m => expired

"""
You ask the questions. Ask one clear question at a time, read each answer
before you ask the next, and send stop once you have what you need.
"""
role requester

"""
You answer the requester. Answer the question that was asked, say what you are
unsure of, and reply within five minutes.
"""
role assistant

"""
One question, with any context the assistant needs to answer it.
"""
type question
  text: text(1..2000)

"""
The answer to one specific question; request names the question it answers.
"""
type answer
  request: ref(question)
  text: text(1..4000)

"""
The requester has no further questions and ends the conversation.
"""
type stop

"""
Nothing has been asked yet. The requester opens with the first question.
"""
state new
  requester -> assistant: question() as request awaits answering
    => answer(request=$request) & answered
     | @5m & stalled

"""
A question is open. The assistant owes an answer to exactly that question
within five minutes. At most ten questions are asked.
"""
state answering max_entries 10 => exhausted

"""
The last question has its answer. The requester asks another or stops.
"""
state answered
  requester -> assistant: question() as request => answering
  requester -> assistant: stop() => complete

end complete

end stalled

end exhausted
```

`new` is entry-only, so after an answer the session waits in `answered` rather
than returning to `new`. The question is a request with
[reply alternatives](contract-dsl.md#reply-alternatives): it awaits `answering`,
an answer leads to `answered`, and with no answer within five minutes the run
ends `stalled`. The five-minute wait starts when the question is accepted.
Asking again from `answered` re-sends the same request, so it reuses the block
and rebinds `request`: an answer must cite the latest question. Because the
conversation can loop back to `answering`, that state caps its entries at ten,
and an eleventh question ends the run `exhausted`.

Compilation produces documented IR and two identities. `digest` identifies
the normalized behavior; `source_digest` is the exact file hash, which also
covers documentation. The authority pins both. A participant cannot submit a
replacement contract in a message.

Initialize session `s` with the exact bindings
`{"requester":"requester","assistant":"assistant"}`, trusted time 0,
`max_messages:100` and `max_bytes:16384`. The replay files also name an
inactivity policy because the replay runner requires one; this contract has no
idle timer, so the policy has no effect. Role names and principal IDs happen to
be equal in this fixture; real bindings can map them to different opaque IDs.
Band principals use its typed identities. The initial version is 0, state is
`new`, and no fact is accepted yet. The thirty-minute cap sets `max_at` to
1,800,000 ms.

## Decisions and the state they leave

The IDs `q`, `a`, `bad`, `other` and `stop` below are illustrative fixture
labels, not production ID-generation guidance. A live client must use
[unpredictable, payload-independent IDs](room-clients-v2.md#proposal-ids-private-randomness-shared-namespace)
and preserve them on exact retries.

The first normalized envelope is:

```json
{"id":"q","session":"s","kind":"question","payload":{"text":"ping"},"expected_version":0}
```

The test supplies trusted actor `requester` and time 0 separately. A deployed
transport obtains those values from its authenticated principal and authority
clock. They are not additional envelope fields.

| Trusted time (ms) | Proposal | Response | Current state/version afterward |
|---|---|---|---|
| 0 | `q`: requester asks “ping”, expected version 0 | Accepted; binds `request` to ID `q` and addresses the assistant | `answering`, 1 |
| 1,000 | `bad`: assistant answers with `request:"other"`, expected version 1 | Rejected: `reference` | `answering`, 1 |
| 2,000 | `a`: assistant answers “pong” with `request:"q"`, expected version 1 | Accepted; addresses the requester | `answered`, 2 |
| 3,000 | `stop`: requester sends the empty `stop` payload, expected version 2 | Accepted; outcome `complete` | `complete`, 3 |
| 4,000 | Exact original `q`, including expected version 0 | Duplicate; contains the original acceptance at version 1 in state `answering` | `complete`, 3 |
| 5,000 | ID `q` with expected version changed to 3 | Rejected: `id_conflict` | `complete`, 3 |

The rejected answer does not satisfy a duty, change the binding or consume
message capacity. Its trusted clock is still observed. A type-valid reference is
insufficient: `request` must equal the currently bound accepted ID. Sending
another accepted question ID would also fail if it is not the current binding.

The duplicate's receipt describes a historical fact. Its old version does not
rewind the session, and its old state does not reopen the answer turn. The
changed-version resend is a different fingerprint under an occupied ID, so
conflict wins before terminal or stale-version checks. Reconciliation must
retain the exact original proposal.

## The deadline branch

Start the same session again and accept only `q` at time 0. The assistant's
reply wait falls due at 300,000 ms; a tick at 299,999 leaves the session in `answering`.
An answer sent at exactly 300,000 is too late because clock advancement precedes
admission: the wait fires first, the session ends as `stalled` at version 2, and
the answer is rejected with `terminal`. Observing the session later, at 400,000,
still records `expired_at:300000` and version 2.

No heartbeat, read, retry or restart extends the wait. The thirty-minute cap is
a separate, later clock; at an equal timestamp the wait, which fires as a duty,
would be selected before the cap ([recovery defaults](defaults-v2.md)). Changing the
source also changes the pinned file hash and requires a new session or explicit
migration.

## Where durability and the agent begin

The pure reducer computes `(decision,next_state)`. A durable authority must
commit the next state, accepted receipt and required chat record before
returning acceptance. In Band those writes share a database transaction. A
notification is a hint to read durable facts; it is not the accepted fact itself.

An ordinary agent reads its role view and proposes directly; it needs no extra
runtime. The optional remote runner adds its own durability: it receives and
verifies `q`, journals its exact callback input, then invokes a qualified
Python handler or model route. It records the bounded
result before preparing the answer proposal. It refreshes the role projection,
checks the proposal locally, and commits an outbox record before sending it.
The room makes the final admission decision even after a successful local check.

If the room accepts the answer and its reply is lost, the host retains the same
outbox envelope and candidate state. It reconciles that envelope and receives
the original acceptance as a duplicate. Only then does the host atomically
commit its own completed input, state, receipt and outbox clearing. These are
two separate durable owners, with no transaction spanning both databases.

The [remote runtime recovery table](remote-runtime.md#commit-boundaries-and-recovery)
specifies the other crash positions. A recorded callback output, a held input,
a room acceptance and a locally completed turn are different milestones.

## Reproducing and extending the example

After the repository's documented setup, replay both paths with the reference
engine:

```sh
cd contract-lab
mix contract.replay --format frontmatter contracts/v2/hybrid-assistant.band scenarios/v2/hybrid-assistant-walkthrough.json
mix contract.replay --format frontmatter contracts/v2/hybrid-assistant.band scenarios/v2/hybrid-assistant-deadline.json
```

Each step states its expected status, reason, state, outcome and version, and a
replay fails on the first mismatch. The [profile checks](profile-v2.md#machine-interface-and-checks)
cover the wider `0.2.0-lab.1` behavior. For another implementation, read the
[adapter protocol](conformance/README.md) before interpreting checker output.
Passing these checks demonstrates the tested behavior; persistence, sandbox,
transport, live-provider quality and workload qualification need their own
requirement evidence.
