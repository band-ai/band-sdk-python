# Runtime rosters and partial collection

These opt-in features are implemented in the local reference engine. They are
part of a [multi-agent skill](multi-agent-skills.md), alongside retained guidance.
The existing `each` transition remains strict: one accepted reply from every
selected member. A `collect` statement explicitly chooses recoverable partial
results instead.

## Declare a reusable participant type

```band
---
roles: [lead, reviewers]
---
```

In the body, before any states:

```band
"""
Run independent reviews by reviewers who each bring one specialty.
"""
contract @30m => expired

"""
Review independently. Cite evidence, state uncertainty and name missing coverage.
"""
type reviewer: agent
  """
  The assigned perspective, not a permission.
  """
  specialty: enum(devops|security|frontend|general)

"""
Coordinate this run's independent reviewers.
"""
reviewers: list(reviewer)
```

`type reviewer: agent` declares a reusable **participant type**, distinct from a
message type such as `type review`. A type's ordinary prompt applies to every
member; a prompt before `reviewers` applies to this roster. Header roles name
the roster (`reviewers`), not the type (`reviewer`). The same type may be used by
several rosters. Participant and message types share a name namespace, so a name
cannot ambiguously mean both.

Attributes use the existing bounded `text(...)`, `integer(...)`, `boolean` and
`enum(...)` field schemas, including field prompts and text format constraints.
Every declared attribute is required; extra attributes are rejected. Message
`ref(...)` fields, recursive participant fields and executable defaults are not
participant schemas. A participant type cannot be sent as a message or used as
a message reference. Type lookup returns either kind with an explicit
`band-contract/participant/1` profile on participant types.

`new.config.bindings` is trusted host input. Single roles retain their string
binding. A collection binds a nonempty list of named agents:

```json
{
  "lead": "lead-agent",
  "reviewers": [
    {"name": "security", "actor": "agent-a", "attributes": {"specialty": "security"}, "guidance": "Trace trust boundaries and permissions."},
    {"name": "frontend", "actor": "agent-b", "attributes": {"specialty": "frontend"}, "guidance": "Review user-visible states and recovery."},
    {"name": "general_model_b", "actor": "agent-c", "attributes": {"specialty": "general"}, "guidance": "Independently review the whole change."}
  ]
}
```

The room validates `attributes` against the named participant type before any
member can participate. Their canonical JSON is bounded to 16,384 UTF-8 bytes
per member, in addition to each field's bounds. An empty participant schema may
omit `attributes`; it receives `{}`.

The roster is immutable for that run. A new run may bind a different size.
Member names are unique lowercase identifiers of at most 64 characters; actor
IDs MUST be unique across all roles and groups. Empty collections are rejected,
so an accidentally empty list cannot count as a completed review. The local
profile bounds total participants at 256. Guidance is optional UTF-8 text,
bounded to 16,384 bytes, and cannot grant permissions. The canonical JSON of
the whole `bindings` object is bounded to 262,144 bytes, a quarter of the
redacted checkpoint, because every agent projection carries the roster
configuration. A roster failure is `invalid_bindings` at creation and
`invalid_checkpoint` at restore. Concrete member names in
views are `reviewers/security`, for example. They are not new authored roles.

A selector `reviewers` expands to its members on either side of an arrow.
`reviewers -> lead: review() each @5m => collected | stalled` waits for every
member. Without `each`, any one accepted reply takes the transition. A timed
event on a roster, `event reviewers -> lead: ack() each @5m => stalled`, owes
one event per entry into the state from every member with `each`, and from any
one member without it. The role view retains the named participant schema, type
prompt, roster prompt, validated member attributes and host-supplied assignment.
These describe the work; none grants permissions or certifies expertise. Roster
configuration is shared session data, not a store for private participant
secrets.

Message and recipient bounds still apply to the actual expanded roster. This
example sets no [mention cap](contract-dsl.md#mention-caps), so a message to
`reviewers` reaches every bound reviewer. Under
`contract @30m mentions 8 => expired`, a rule addressed to `reviewers` makes
session creation fail with `mention_limit` once more than eight reviewers are
bound. The host must supply enough accepted-message capacity for the request and
all promised responses. The reference engine conservatively reserves those
responses at admission, including members that will immediately settle from an
initial offline observation. Source identity remains the exact skill-file hash;
runtime bindings are separately preserved and verified at restore.

## Collect results, then continue

Declare `room.contributions/1` in the header's `extensions` list. The host must
enable that capability. Add `host.presence/1` when using an offline grace.

```band
"""
Gather one review from each reviewer within five minutes, then summarize. A
reviewer who goes offline for thirty seconds is settled as offline.
"""
contract @30m => expired

"""
You receive the reviews. Wait for the collection to close, then summarize what
arrived and name each reviewer who did not report.
"""
role lead

"""
Review independently and report what you found, with the evidence for each
finding.
"""
type reviewer: agent

"""
The reviewers for this run. Each one sends exactly one review before the
deadline.
"""
reviewers: list(reviewer)

"""
One reviewer's findings, with the evidence behind each one.
"""
type review

"""
The reviewers work in parallel; the room collects one review from each.
"""
state reviewing
  collect reports from reviewers -> lead @5m => summarizing
    reply review()
    offline @30s
```

The statement names a collection, its members, recipients, maximum collection
window and next state. It has one required `reply` pattern and an optional
offline grace. The reply uses a declared message type, payload validation and
reference conditions. Put these option lines
together. Documentation belongs before `collect` and before the referenced types
and fields. One collection per state keeps the initial language unambiguous;
its name is unique within the skill. Short role selectors work as usual.

A member settles once:

| Status | Evidence |
|---|---|
| `ok` | An accepted message matching `reply` |
| `failed` | The member explicitly stopped its contribution, or a trusted host reported terminal failure for it |
| `offline` | Trusted current presence remained offline for the declared grace |
| `timed_out` | The collection deadline arrived while the member was pending |
| `cancelled` | Another state transition or whole-session termination ended collection |

`pending` is visible while the collection remains open. An exact message retry
returns its original receipt; a second distinct contribution from an already
settled member is rejected. Malformed replies are rejected and remain pending.
Successful messages keep their original accepted fact and payload.

Failures use [room contribution controls](contribution-outcomes.md). They do
not require an agent to send a particular business message after failing. Each
control identifies the session, collection, round and member, and retains a
bounded cause with its own type and optional original-event reference. The room
does not infer terminal failure from an arbitrary error message, a retried tool
call or the end of a model turn. A review saying “reject this change” is still a
delivered review. Its business judgment belongs in the reply's payload.

The collection closes at the first of (a) every member settling or (b) its
deadline. It then moves to `summarizing`, which can be a normal active state.
The next state is entered at the time of the event that closed the collection:
the settling message, or the deadline or offline grace itself when a timer
closes it. A deadline observed late therefore does not give `summarizing` a
fresh clock.
A close enters the next state the way an accepted message does, not as a
nonterminal host transition: every outstanding duty ends, including one carried
in by an earlier host transition, and the next state starts with only its own
declared duties. Those duties still need their whole windows before `max_at`
and their reserved replies, each with its follow-on, must fit the remaining
message budget ([reserve](defaults-v2.md#reserve-known-response-capacity)); if a window cannot
fit, the session ends as `insufficient_time`, and if the reserve cannot fit, as
`completion_capacity`.
Partial collection does not by itself fail the session. Zero successful results
is also a representable collection result; guidance or a subsequent decision
must determine whether to retry, request help or end the job.

When some members must succeed, add a `quorum` line:

```band
"""
Run every lane's check, and go on only if each required lane settled ok.
"""
contract @30m => expired

"""
You receive the lane reports. Verify them after the collection closes; if a
required lane did not settle ok, the run goes to short instead.
"""
role lead

"""
Run your lane's check and report what you found. A required lane blocks the
run unless it settles ok.
"""
type lane: agent
  """
  Floor lane: the run cannot go on unless it settled ok.
  """
  required: boolean

"""
The lanes of this run, each reporting once.
"""
lanes: list(lane)

"""
One lane's result: what was checked, what passed and what failed.
"""
type lane_report

"""
The lanes report in parallel; required lanes decide whether the run can go on.
"""
state reviewing
  collect reports from lanes -> lead @10m => verifying
    reply lane_report()
    quorum required => short
```

`quorum all => x` requires every member to settle `ok`; `quorum <attribute> => x`
requires it of every member whose boolean participant attribute is `true`, and
every role the collection reads from must be a roster whose participant type
declares that attribute. The quorum is evaluated once, when the collection closes
as `settled` or `deadline`: if a quorum member's status is not `ok`, the close
enters `x` instead of the collection's target. `x` follows the target's rules and
checks, and `completion_capacity` still takes precedence. A quorum member that
fails early does not close the collection sooner. The reply that settles the last
member is admitted against the target the close would actually enter, and a
renewal of the collection must leave room for both targets. Until the close, the
[reserve](defaults-v2.md#reserve-known-response-capacity) and the closing path
count the worse of the two targets, because nobody chooses which one the close
enters. A roster with no quorum member meets the quorum. A
collection without the line compiles and closes exactly as before.

The result records each member's status, accepted message ID when present,
settlement time, state visit, opening/closing times and reason (`settled`,
`deadline`, or `cancelled`). Receivers can inspect the whole collection and their
accepted facts. A contributor's role view shows only its own status and facts.
Closed results remain available across later states and portable recovery. A
reconnect does not reopen them. Repeated visits create new results; previous
reports do not count again. A round cancelled before any member replied or
failed creates no result; its log rows alone record that visit. A kept
cancelled result therefore always holds a reply or a failure, restore rejects
one that does not, and no reply message appears in more than one round.
Failed members retain their control ID and cause separately from accepted
message IDs. The member and collection receivers can see the cause.
The [agent projection](engine.md#views-and-preflight) checkpoint that another
contributor uses for local preflight is not a view. It keeps every member's
status, settlement time and report message ID, because these are protocol data
needed to reproduce admission. Another member's failure in a collection that
contributor does not receive is private: its failure control ID becomes an
opaque placeholder, `redacted:` and 32 hexadecimal digits derived only from the
collection name, entry and member, in the member row, the contribution receipt
and the collection log alike. Its cause `type` and `message` become `"redacted"`,
its `reference` becomes `null`, and the receipt fingerprint becomes 64 zeroes. The checkpoint never carries another member's
report payload or failure cause. The collection's receivers see the real IDs.

All timers are processed before a new proposal. At the exact deadline, collection
closes first, so a late reply cannot change its result. Equal-time ties follow
the [equal-time order](timeouts-v2.md#equal-time-order): a work duty first, then
the collection deadline, then offline grace, then the state timeout, host
trigger and whole-run cap. A collection window cannot exceed its state cap, so
an offline grace that ties the state timeout also ties the collection
deadline, which closes the collection first.
Known response capacity and full-window admission apply to the next state too.
Closed rounds are bounded by the session's `max_messages`, so a message-free
timer loop cannot create unlimited results. Every round that closes as
`settled` or `deadline` counts, across all collections and visits; cancelled
rounds never count. The close that brings the count to `max_messages` does not
enter its target: the session ends with the built-in terminal outcome
`completion_capacity` instead, even when the target is itself terminal.

## Presence uses a declared grace, not a guess

`offline @30s` means a continuous offline interval of thirty seconds. At collection
entry, use the current trusted status and its original `since` timestamp. An
agent offline for two days is already beyond the grace; it settles immediately.
An agent offline for two seconds gets the remaining twenty-eight seconds.
Online or unknown status cancels an unexpired offline interval; a later offline
observation starts a new interval. Unknown presence never implies failure.

The host MUST opt into `host.presence/1`, authenticate its observations and use a
coherent current presence snapshot. Its trusted creation configuration may carry:

```json
{"initial_presence":{"reviewers/security":{"value":"offline","since":2000}}}
```

Times are host clock milliseconds, in the same nondecreasing time domain as
`new.now`. `since` may precede session creation but MUST NOT be in the future.
This creation snapshot is the only presence input that may be backdated; every
later report is stamped with the reducer time ([host signals](host-signals.md)).
Its `since` seeds both a collection's offline grace and any `on … offline for`
dwell, so a member already offline beyond either interval settles, or fires the
trigger, as soon as its state is entered.
The bounded initial snapshot consumes one host-control record and revision;
later host signals use increasing revisions. Repeated offline observations
preserve continuity. The room must re-query current presence if its feed can
miss transitions; the engine cannot infer an unseen reconnect.

Presence is availability, not authorization. With `host.membership/1`, the
existing membership removal rules still terminate the session and preserve the
collected partial data; they are not silently downgraded to an offline result. The lead must not turn an agent's
unverified statement about someone else into a trusted presence observation.

## Removed forms

These earlier forms are `syntax` errors whose detail names the replacement:

- `role reviewers: list(reviewer)`: write `reviewers: list(reviewer)`.
- The builtin `list(agent)` roster: declare `type reviewer: agent`, then
  `reviewers: list(reviewer)`.
- Collect `failure type()`: use the room contribution operations described
  above.

Executable evidence: [contribution tests](../contract-lab/test/contribution_test.exs),
[quorum tests](../contract-lab/test/collection_quorum_test.exs),
[collection compatibility tests](../contract-lab/test/collection_test.exs),
[roster tests](../contract-lab/test/roster_test.exs),
[participant-type tests](../contract-lab/test/participant_type_test.exs) and
[portable assertions](tools/checkcoordination.py).
