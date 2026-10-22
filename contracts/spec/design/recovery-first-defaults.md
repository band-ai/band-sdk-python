# Recovery first, predictable defaults

**Historical design review:** its observations describe the original engine. The accepted implementation and per-request dispositions are now in [profile-v2](../profile-v2.md). In particular, humans are outside contracts, `new` is entry-only, the simple question/answer session terminates, and host presence/membership are opt-in extensions. Broader durable storage reservation and deployment qualification remain host work.


Status: **design proposal following Eric's slide 6 review, 12 October 2026**.
The requested priority is accepted: recovery first, then minimum surprise.
Engine `0.2.0-lab.1` adopted parts of this proposal; [recovery defaults](../defaults-v2.md)
lists what was adopted. Current behavior is specified by [the DSL](../contract-dsl.md)
and [the engine](../engine.md).

The central change is to put recovery deadlines on outstanding responsibilities.
An idle conversation is not necessarily failed work. Session expiration,
resource limits, transport liveness, and data retention answer different
questions and should not share a generic inactivity timer.

## What the current example actually does

The `30m`, `5m`, `1`, and `100` values are required choices in the existing
example, not engine defaults. Requiring every author to choose them still
encourages copying unexplained numbers. Their placement in a discovery card
makes operational choices look like intrinsic properties of the workflow.

Nine executable probes recorded these observations on the original
`0.1.0-lab.1` engine through its native header format. Wave 2 retired that
format, so the probes, their recorded result and their frontmatter fixture were
retired with it. The last column names what now checks each design consequence
against the current profile: a [v2 conformance check](../tools/checkv2.py) or
[portable coordination assertion](../tools/checkcoordination.py) in quotes, or
a test in [`recovery_defaults_test.exs`](../../contract-lab/test/recovery_defaults_test.exs).

| Case | Observed behavior | Design consequence | Now checked by |
|---|---|---|---|
| YAML roles as a sequence | Complete frontmatter fixture preserves both normalized identities | Cleaner discovery syntax is already supported | Nothing to compare: frontmatter is the only header form |
| No question has been asked | `new` expires after five quiet minutes | Silence is treated as failure even when nobody owes work | `no-idle-default`; "the new profile has no idle timer field" |
| Unanswered question, read at three minutes, exact retry at four | Neither resets activity; at five minutes inactivity wins the duty tie, producing `expired` | A generic timer masks the responsibility diagnosis; recovery traffic is not a keepalive | `inline-duty-wins-phase-tie`; "the specific duty wins a tie with the overall cap" |
| Inactivity disabled; question accepted at 29m50s | The 30-minute creation-based cap ends it ten seconds later | A declared five-minute response window can be cut short | `whole-run-keeps-full-response-window`; "a maximum duration cannot silently shorten a newly accepted duty" |
| Both session timers explicitly `infinite` | Quiet waiting remains active after a day | The current engine can already separate silence from owed work | "the default card has no arbitrary lifetime, idle timeout or mention cap" (a day of quiet stays active) |
| Same variant, then a question | The five-minute duty still selects `stalled`; equality is too late | Removing idle expiry does not remove work recovery | `original-duty-still-terminates`; `deadline-before-exact-reply` |
| Restart after that duty is overdue | Original scheduled deadline is preserved | Restart must not grant a fresh response window | `state-clock-survives-restart`; `original-duty-preserved` |
| Fifty question/answer pairs | The next `stop` is rejected by the 100-mention budget | Resource exhaustion can block even normal closure | No session mention budget (D-3): `mention-default-compiled`; `mention-rule-override` |
| Host message cap of one | Question accepted, answer rejected for capacity | Admission can create an obligation the host cannot accommodate | `refuse-work-without-answer-capacity`; "new work is refused when its required answer cannot fit the remaining capacity" |

The example's current form is `contract-lab/contracts/v2/hybrid-assistant.band` on engine
`0.2.0-lab.1`; it shows the adopted design rather than the behavior in the table. These
were design observations, not failing tests of the old specification and not live Band
qualification.

## Recommended defaults and ownership

| Concern | Recommended default | Explicit override and owner |
|---|---|---|
| Waiting for the next human request | No protocol timeout | Workflow author may declare a meaningful abandonment deadline |
| Work a participant owes | Finite, named obligation deadline | Author or versioned workflow template selects duration and failure outcome |
| Whole-session age | No intrinsic duration limit | Optional `max_duration`, measured from authoritative session creation; explicit host cap appears in effective policy |
| Generic global inactivity | Absent | Use a deadline for a particular waiting state, with a named outcome and reset rule |
| Recipients of a message | Determined by the contract rule | Host validates its fan-out limit against all reachable rules before creation |
| Cumulative delivery/work capacity | Host policy, visible and pinned at creation | Explicit workflow budget where it has business meaning; templates may propose capacity |
| Retries and recovery | Stable operation identities, durable receipts, bounded attempts | Host selects backoff and execution policy without extending contract deadlines |
| Dormant storage and retention | No inference from conversational silence | Host publishes separate retention and recovery guarantees |

There is deliberately no universal five-minute work deadline or 100-message
budget. A human approval, an API call and a generated report need different
windows. A supplied template can select concrete values, but must state its
assumptions and version. The compiler resolves semantic defaults; the host
resolves operational policy. Both resolved results are inspectable and pinned.

An author should see only fields that express this workflow's meaning. A host
can refuse a deployment it cannot sustain. It must not silently substitute a
shorter deadline, add idle expiry, or lower capacity after session creation.
An explicit administrative cancellation remains possible under its own
authorization and audit trail; that is not a hidden configuration mutation.

## Recovery follows owed work

In the assistant workflow, `new` means waiting for the requester. That wait
needs no failure timer. Accepting a question creates a specific responsibility:
the assistant owes an answer referring to that question by a stated deadline.
The actor implementing that role can restart, poll for accepted facts or use a
different program without resetting the responsibility.

```text
waiting for requester -- accepted question --> assistant owes answer
        ^                                    /                  |
        |                         accepted answer          deadline reached
        +-----------------------------------+                  |
                                                            stalled
```

A missed deadline is a durable diagnosis and recovery trigger, not proof that
an external effect never occurred. The authority persists the outcome and
publishes a recoverable notification. A host can then apply an explicitly
configured retry, reassignment or operator-intervention policy. Terminal
sessions are not silently reopened, and a new attempt must not repeat an
unreconciled non-idempotent effect. Local crash recovery remains a separate
mechanism: restore state and the invocation/outbox journals, reconcile uncertain
effects, and resend only with their original operation identities.

For a future profile, distinguish states that deliberately wait for external
input from states that promise progress. A work state needs finite recovery
coverage for every required contribution. A waiting-state annotation needs a
reason and a named actor/event that can resume it. Do not silently classify a
state with no duties as safe waiting. Automated cycles need a declared attempt,
entry, budget or overall deadline bound; entering a new state must not renew
work forever. This is static validation of the promised workflow, not a global
idle heuristic. The exact annotation spelling belongs to the next grammar.
If events discharge every duty without leaving a work state, that path also
needs an explicit transition to waiting/completion or a remaining recovery
obligation; declaration-time presence of one duty is not sufficient.

A timeout on a particular waiting state is useful when silence has business
meaning: an unanswered offer becomes `unclaimed`, for example. Start it on
entry. If it can renew, name the accepted progress events that renew it and
bound the total extension. An unrelated message, read, duplicate, provider
token stream or socket heartbeat must not extend it. Work deadlines remain
absolute unless the protocol explicitly authorizes a bounded extension.

Transport heartbeats may detect a broken connection or support a worker lease.
They do not belong in contract history, consume conversational budget, discharge
work, or prove progress. Lease expiry may authorize another executor only with
the host's fencing and effect-reconciliation safeguards. No heartbeat is needed
to keep a legitimate quiet conversation alive.

## Make a session cap honest

If a workflow has a meaningful overall deadline, keep it. Call a relative limit
`max_duration` and show its resolved `expires_at`, measured from creation. A
room's lifetime, a participant's membership, an invocation timeout, and receipt
retention are independent. A session cap never extends when somebody talks.

Before accepting new work, compare its required response window with the
remaining session time. Default recommendation: reject that new work with an
explicit insufficient-time reason if its promised window will not fit.
Workflows that intentionally allow a shorter final window must declare that
behavior and show the actual deadline. Do not accept a five-minute promise with
ten seconds left and present it as a normal five-minute obligation.

Preserve earliest-deadline admission and the rule that equality is too late.
For the future profile, record all causes due at the earliest timestamp; prefer
the specific obligation outcome over a generic session cap for presentation.
Use a stable declaration order to choose among equal duty outcomes, and retain
the other due causes in diagnostics. Both policies and deadline changes require
new conformance cases and a new profile; the current lifetime/inactivity/duty
priority remains unchanged for old sessions.

## Capacity must preserve the ability to recover

`mentions-per-message: 1` adds no useful workflow restriction to a rule that
already targets exactly one other role. The next authoring profile should
derive recipients from rules, while the host checks whether every permitted
fan-out is supportable. If a rule can require three recipients and the host
allows two, reject creation rather than install a contract with a dead path.
Business constraints such as “notify at most two reviewers” belong in explicit
rules or a named business budget, not a generic transport field.

Keep host limits finite where needed. Separate business-message capacity from
the capacity required to record and inspect recovery. Read, receipt lookup and
an exact retry must not spend new business budget. Authorized cancellation and
failure resolution need a bounded, reserved control path; this cannot be an
arbitrary unlimited message channel. Its actions are fixed, authenticated,
idempotent and durably recorded. Even the final failure checkpoint needs
reserved storage. Reserve does not make disk loss safe: failure to persist is
an unavailable authority, never an invented acknowledgement.

Check known minimum completion costs before accepting work. In this two-party
example, do not spend the last available message on a question when no capacity
remains for its answer. Templates with several required contributions need an
explicit reservation plan. Static analysis cannot promise successful completion
for arbitrary branching or unbounded workflows; missing actors and external
effects remain possible. If capacity is exhausted despite admission planning,
record a distinct recoverable capacity outcome through the control path, rather
than leave a session active but unable to resolve. A valid final completion
using the last reserved unit should still complete.

This is more implementation work than moving two fields out of the header.
The new profile needs a precise control-operation schema, counter/reservation
rules, authorization, simultaneous-timer precedence, and durable capacity
behavior. A failed allocation before acceptance is not a failed workflow;
exhaustion after acceptance is not successful completion.

## Keep discovery simple without weakening it

Use a YAML sequence for roles. This already works under `compiler/1` and avoids
the comma-string convention. The checked frontmatter example uses the real
description and full body; it is not a shortened stand-in for the fixture hash.

The native format's body marker is a boundary, not an empty field. The slide showed
the marker and omitted what followed it. The frontmatter format uses its closing
`---` instead. Keep a clear bounded discovery boundary, since the host must read
metadata without reading or evaluating the complete body. Show the first body
declaration when teaching the format so the boundary's purpose is evident.

For the next grammar, prefer ordinary `type`, `state`, `end`, and `role`
declarations over the decorative plus-prefixed spellings. Preserve
typed fields, references, transition arrows and attached documentation. This
is an ergonomic recommendation, not parser support: do not remove the plus
signs from existing files under the old engine identifier. Nor should changing
the punctuation be required to adopt the already-supported YAML role list.

Discovery should identify name, description, roles and supported semantics.
Workflow-specific promises belong with the relevant state/rule, and resolved
host limits belong in the session policy returned at creation. Retain immutable
source selection and bounded, non-executable parsing. Standard skill packages
remain an integration option; no contract should require Band IDs or Band-only
metadata to run on another platform.

## Band and other hosts

Band owns authentication, room membership, durable ordering and policy
negotiation. The portable reducer owns obligations and their outcomes.
Dormant sessions can be unloaded from memory while their authoritative state
and receipts remain recoverable. Unloading is not a protocol state transition.
A host that requires finite session age must disclose and pin it before work
starts; the caller can reject it. Physical deletion follows separately declared
retention and authorization rules, with its effect on retry guarantees explicit.

Every platform exposes the same effective policy, obligations and diagnostics.
Band's room activity flag and a socket's heartbeat are not substitutes for
these fields. Active work still needs scheduled deadline advancement while no
requests arrive, trusted monotonic clock observation, transactional receipts,
and crash recovery. A future profile requires coordinated changes to the core,
portable state and views, transport creation schema, Band vendor pin, remote
preflight, and migration tests. It cannot be implemented only in the slides or
by changing the Band sweeper.

## Acceptance and delivery ledger

Consumer of this design: Eric. No receiving developer, external handoff or
production deployment is claimed.

| Review request | Disposition | Concrete result |
|---|---|---|
| Put recovery first, then minimize surprise | As expected: designed | Obligation deadlines, durable recovery and explicit host policy are separate |
| Explain lifetime | Changed: clarified and redesigned | Creation-based cap; optional in next profile; admission prevents hidden shortening |
| Rethink global inactivity and heartbeat traffic | Changed: redesigned | No global idle default; scoped waiting deadlines only when silence has meaning |
| Make mention limits specific and defaults logical | Changed: redesigned | Rule-derived recipients, advertised host budgets, completion checks and reserved recovery |
| Prefer roles as a list | As expected: verified | Complete YAML fixture compiles with identical normalized identities |
| Explain empty BODY and awkward plus syntax | Changed: clarified; next syntax proposed | BODY is a boundary; show real following content; ordinary keywords recommended for next grammar |
| Implement new runtime semantics | Not yet: separate profile work | This review changes design and explanation; old runtime and immutable sessions retain their contract |

The implementation acceptance matrix for the next profile is:

| Scenario | Required result |
|---|---|
| Quiet human wait, including host restart | No business timeout or keepalive traffic |
| Missing promised answer or group contribution | Named overdue responsibility at its original deadline |
| Read, duplicate, unrelated progress, socket heartbeat | No duty extension or new business charge |
| Repeated state entry or automatic retry | Declared bound prevents endless renewal |
| Work near a maximum duration | Reject before promising an unavailable window, unless shortening is explicit |
| Equal session and duty deadline | Same result on all implementations, with all earliest causes retained |
| Insufficient capacity before accepting work | No accepted obligation; explicit capacity refusal |
| Exhausted business budget after acceptance | Durable capacity outcome or authorized cancellation still possible |
| Crash after acceptance, before acknowledgement | Recover original receipt without repeating effects |
| Unknown non-idempotent effect at timeout | Hold for reconciliation; do not infer failure or execute again |
| Unsupported fan-out at creation | Reject creation, rather than install an unusable rule |
| Old/new profile restore or migration | No silent semantic reinterpretation; receipts and unresolved work preserved |

These targets must become observed old/requested-outcome checks followed by
new-profile GREEN evidence, portable vectors, and real host fault tests. The
current nine probes establish the premise; they do not satisfy that matrix.
