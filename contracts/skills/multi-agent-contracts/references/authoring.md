# Authoring the current contract profile

For current authoring, begin with [multi-agent skill guidance](../../../spec/multi-agent-skills.md), [runtime collections](../../../spec/coordination.md), [room contribution outcomes](../../../spec/contribution-outcomes.md) and [explicit renewal](../../../spec/renewable-timeouts.md). Use ordinary quoted one-line or multiline strings as prompt metadata for the item directly below. Write regular expressions as `regex: ~r/.../` and raw one-line text as `~s|...|`; keep sigils for cases that need their special behavior. Put business guidance that the room cannot directly enforce in these strings; `/* ... */` comments are author-only.

For new contracts, read the maintained [reviewed profile](../../../spec/profile-v2.md)
and [complete agent-to-agent example](../../../contract-lab/contracts/v2/assistant-answer.band).
Engine `0.2.0-lab.1` changes exact file identity, default clocks and capacity,
text units, entry-state rules and optional host extensions. The links in that
guide are the normative grammar and semantics. Preserve the user's chosen
supported frontmatter or standard-package layout. Select the matching format
explicitly in every helper command below.

For raw text in documentation, read [literal sigils](../../../spec/sigils-v2.md).
Existing sessions do not upgrade in place. No independent
Python, TypeScript or Rust reducer is implied by the Elixir reference.

When a role sends a request and waits for its answer, write the request with
[reply alternatives](../../../spec/contract-dsl.md#reply-alternatives):

```text
contract @30m => expired
type request
type review
  request: ref(request)
  decision: enum(approve|revise)
state new
  lead -> reviewer: request() awaits reviewing
    => review(decision=approve) & approved
     | review(decision=revise) & revising
     | @5m & stalled
```

Read it as: the lead sends a request and waits in `reviewing`; an approving
review leads to `approved`, a review asking for changes to `revising`, and with
no review within five minutes the run ends `stalled`. The first alternative
starts with `=>` and each later one with `|`; the timeout comes last, exactly
once, and names an `end` terminal. The alternatives cover every reply the
recipient may validly send. `decision=approve` matches an enum member or a
boolean literal. The reply type declares one `ref` field to the request's kind;
the compiler binds the request and matches that field, so you write neither.
Another rule `lead -> reviewer: request() => reviewing` re-sends the request
and reuses the block. If `reviewing` can be entered again, give it
`state reviewing max_entries 3 => exhausted`. A `state reviewing` section is
optional and may hold events and the requester's own rules, such as a cancel,
but never a rule from the reviewer that moves the state.

Never write a request as a plain transition into a state and then its answer as
a separate rule from the recipient in that state. The compiler refuses that
pair with `reply_required` at the request's line; write the answer as an
alternative under the request instead. A reply head has one sender role and
one recipient role, with no `each`, roster or group. A request to a group or a
roster has no reply form yet and keeps the plain form until the group design
lands; see the [group offer](#rules-and-outcomes) below.

A timed rule such as `worker -> lead: deliverable() @1h => delivered | stalled`
remains for an owed report that answers no request, for example a deliverable
owed after the worker's own acknowledgement.

`@duration` on a message starts with state entry; a state header such as
`state deciding @2m => undecided` bounds that visit; the contract header bounds
each whole run from session creation. Keep `review()` parentheses. Use
`each @5m` for all selected senders, or just `@5m` when one reply suffices.
Only owed messages reserve reply capacity; a phase cap alone does not.
An owed event that does not leave the state takes its own deadline, as in
`event worker -> lead: ack() @5m => stalled`. It is owed once per entry into
the state: one accepted event discharges it, it does not re-arm, and it is not
a recurring heartbeat. Repeats are accepted but owe nothing. See the complete
[timeout specification](../../../spec/timeouts-v2.md) and
[scoped review example](../../../contract-lab/contracts/v2/scoped-review.band).

Use lowercase body keywords. Ordinary agents can use public room discovery directly; the client journal and program replacement are optional experiments.

## Discovery and documentation

New contracts use YAML frontmatter; a standard skill package carries the same
fields as metadata ([discovery rules](../../../spec/contract-dsl.md#package-and-discovery)).
Native directive headers are not accepted by this profile. Preserve an existing
session's exact source rather than converting its header in place:

```yaml
---
name: sample
description: Coordinate two participants to complete a bounded exchange.
engine: 0.2.0-lab.1
roles: [requester, worker]
---
```

Only `name`, `description`, `engine` and `roles` are required in the header. The body must declare the whole-run clock on its `contract` line, for example `contract @1h => expired`; write `contract @infinity => expired` when a run is deliberately open-ended. A source without the clock fails with `contract_clock_required`. There is no idle timeout. The host supplies an accepted-message bound at creation. An example value is not a production default or the user's final choice.

Documentation is a preceding string: a one-line `"..."` or a multiline literal using Elixir indentation and final-newline behavior:

```text
contract @1h => expired
"""
The worker acknowledges the exact offer before starting.
"""
type accept
  "Reference to the accepted offer, not a free-text task label."
  task: ref(offer)
```

Do not add `@doc` or a backslash to continue ordinary documentation lines. The opening and closing triple-double-quotes are on their own lines. Interpolation is rejected. Blank lines and comments may separate the literal from its declaration. Docs attach to contract, role, type, field, state, rule, duty, room/any-state scope, or terminal declarations. Queries preserve `meta.doc` and one-based body `meta.line`.

## Rules and outcomes

`contract` declares the mandatory whole-run clock and attaches contract documentation; roles originate in the header, and optional `role name` declarations document them. Types contain named fields using `text(...)`, `integer(min..max)`, `boolean`, `enum(a|b)`, or `ref(kind|kind)`. Text bounds count UTF-8 bytes: `text(1..2000)`, `text(64, regex: ~r/[A-Z]+/)` or `text(bytes: 256, graphemes: 80)`. Runtime refs identify accepted messages of the declared kind.

A state starts with `state name`; the first required state is `new`, which is entry-only and can never be a target. A transition has this shape:

```text
contract @3h => expired
state new
  lead -> worker: offer() as task awaits offered
    => accept(task=$task) & working
     | refuse(task=$task) & refused
     | @2m & unclaimed
state working
  worker -> lead: deliverable(task=$task) @1h => delivered | stalled
```

`as task` stores this message's identity. Matching `task=$task` prevents accepting an unrelated offer; the compiler writes both for you when you omit them. The offer is a request with reply alternatives, so the worker's `accept` or `refuse` picks the next state. The deliverable is an owed report in `working`: it answers no request, so it is an ordinary timed rule.

An offer to a group is a group request. It has no reply form yet, so it keeps the plain form until the group design lands:

```text
contract @1h => expired
state new
  lead -> {worker,reviewer}: offer() as task => offered
state offered
  worker -> lead: accept(task=$task) => working
```

Selectors can be one role, `{a,b}`, or `*`. `each` requires one accepted contribution from every selected sender before the transition happens. Distinct roles bind to distinct actors in this runtime. A sender is excluded from its own recipient set.

`event sender -> receiver: kind()` leaves the current state unchanged. `anystate` contains rules valid in every active session state; it does not reopen terminal sessions. `room` contains sessionless events with independent host quotas.

Write an answer to a request as a reply alternative under that request, as above. Use a timed rule such as `worker -> lead: deliverable() @1h => delivered | stalled` for an owed report that answers no request. When the owed message should not leave the state, put the deadline on an event inside the state:

```text
event {alpha,beta} -> lead: ack() each @5m => stalled
event alpha -> lead: submit() @10m => stalled
event {alpha,beta} -> lead: answer() @2m => unanswered
```

With `each`, every selected role must send a valid matching event; without it, one suffices. Each event is owed once per entry into the state: one accepted event discharges it, it does not re-arm, and it is not a recurring heartbeat. Repeats are accepted but owe nothing. The outcome must be an `end` terminal. A different accepted transition leaves the state and its old duties. Deadlines are checked at the boundary before admitting a new message. An event can discharge its duty without moving state; repeated delivery does not count again or reset time. A whole-run cap never extends unless it is declared renewable, as in `contract @80m max @4h => expired`. At an equal timestamp, a specific duty is selected before the whole-run cap.

`state reviewing max_entries 3 => exhausted` bounds re-entry to that state. Declare each outcome with `end name`; `expired` is reserved and needs no declaration. Names describe observable results: complete, refused, unclaimed, stalled, cancelled, effects_unknown. Generic `DONE` is not an acceptance proof.

## Checks and scenarios

From the master skill directory, or using its absolute installed path:

```bash
python3 scripts/contract check --format frontmatter /absolute/path/example.band
python3 scripts/contract query --format frontmatter /absolute/path/example.band --role worker
python3 scripts/contract query --format frontmatter /absolute/path/example.band --type accept
python3 scripts/contract replay --format frontmatter /absolute/path/example.band /absolute/path/scenario.json
```

Paths passed to the wrapper are resolved from the caller's directory. For a standard package, replace `--format frontmatter` with `--format standard` and pass its `SKILL.md` inside the directory matching its declared name. Without `--format`, the helper reads YAML frontmatter. It accepts only `frontmatter` and `standard`; any other value, including the retired `native`, fails with `invalid_options`.

A scenario is host-owned test data, not live participant authorization. It requires exact role bindings, an initial time in milliseconds, session ID, accepted-message cap, and a nonempty step list. The `inactivity` field is optional; if supplied it must be `accepted`, `transition` or `marked`. Omission resolves to `accepted`, but none of these policies has an effect for this profile, which has no generic idle timer. This harness field is not an authoring header or a public authority grant. Local replay runs in explicit unchecked mode and does not qualify hosted authorization. Every step has assertions in default asserted mode:

```json
{
  "bindings": {"requester":"alice", "worker":"bob"},
  "session":"s1", "now":0, "inactivity":"transition", "max_messages":20,
  "steps":[
    {"actor":"alice", "now":0,
     "message":{"id":"offer-1", "session":"s1", "kind":"offer", "payload":{"task":"Draft a note"}},
     "expect":{"status":"accepted", "state":"offered"}},
    {"tick":120000, "expect":{"outcome":"unclaimed"}}
  ]
}
```

Assertions can check `status`, `state`, `outcome`, `reason`, `version`, and `mentions_used`. A mismatch identifies the step and actual result and produces a nonzero command exit. Rejected messages are useful successful test steps when rejection is the expected behavior. The IDs in this synthetic scenario are labels, not production ID-generation guidance; live clients use [unpredictable IDs](../../../spec/room-clients-v2.md#proposal-ids-private-randomness-shared-namespace) and preserve them on exact retries. See the [current walkthrough and its complete replay files](../../../spec/walkthrough.md) for a runnable example targeting this profile.

For shadow observation, pass `--observe` to replay. This mode records every decision
in the result's `steps` array, permits missing assertions, ignores supplied `expect`
values, and continues after rejected messages. Invalid configuration and malformed
steps still fail; observation remains unchecked and does not qualify host policy.

Replay enables the contract's declared extensions locally by default. Optional
`capabilities` overrides that list, including an empty list to test refusal.
Host presence/membership contracts default to a bounded `max_controls` of 128;
other contracts default to zero. Optional `max_controls`, `watch_bindings`, and
`initial_presence` are validated by the same engine as hosted creation.

Host-owned fixtures can include `{"host_signal": SIGNAL, "now": TIME}`,
`{"fail_contribution": REPORT, "now": TIME}`, or
`{"stop_contribution": REPORT, "actor": ACTOR, "now": TIME}` steps, with
`expect` as usual in asserted mode. The first two are trusted host controls;
the last retains the member-ownership check. The SIGNAL and REPORT shapes are
the engine's normal host-signal and contribution shapes. These synthetic controls do not
attest any live host observation or grant participant authority. Ordinary
message steps cannot carry them, and replay remains explicitly unchecked.

The behavior digest omits documentation; the source digest changes with docs. Local session snapshots pin both, so even a docs-only revision is a new source identity. Preserve running-session identity and describe migration separately.
