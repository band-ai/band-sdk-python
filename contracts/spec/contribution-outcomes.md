# Room contribution outcomes

A collection awaits one useful reply from each selected member. Whether that
member's work can still deliver is room coordination state. A terminal system
failure can end the wait even when the agent cannot send another message.

Use `room.contributions/1` in the discovery header's `extensions` list. The host
MUST explicitly enable this capability before creating the session. It emits
`band-contract/contribution/1`, collection/2 and state/5. Earlier profiles keep
their existing behavior; a new source starts a new run or an explicit host
migration. An old reader MUST reject the new feature instead of dropping it.

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

`review()` declares the successful delivery shape. `offline @30s` additionally
requires `host.presence/1` and keeps the continuous-absence semantics in
[coordination](coordination.md). No custom failure message type is required.

## Two authorized producers, one outcome

The room exposes these standard logical operations through its discovered
host adapter; concrete public tool names and transport authentication belong
to that adapter:

| Operation | Authority | Meaning |
|---|---|---|
| `stop_contribution` | Currently authorized bound member | I have stopped this particular contribution and will not produce its reply |
| `fail_contribution` | Trusted host that runs this contribution's execution | Execution for this particular contribution ended unsuccessfully |

Both settle that member as `failed`, with a cause whose `origin` is set by the
room to `member` or `host`. A member cannot stop someone else's contribution or
claim host authority in a payload. The receiving lead is not a host merely
because it coordinates the work. Membership removal retains its existing
authorization behavior and can still terminate the session.

For example, a host that has finished its retry policy can submit:

```json
{
  "id": "failure-7",
  "session": "review-42",
  "collection": "reports",
  "entry": 1,
  "member": "reviewers/frontend",
  "cause": {
    "type": "provider/budget_exhausted",
    "reference": "provider-event-49",
    "message": "The assigned review ended after its provider budget was exhausted."
  }
}
```

The operation itself declares a terminal outcome for the contribution.
`cause.type` is an arbitrary bounded source type; it is not a declared contract
message type or a closed error enum. `reference` is an optional opaque identity
for original evidence, and may refer to a system event, job record or other
host-retained evidence. The source keeps its original schema. The room stores
the type, reference and summary; it does not fetch a URL or copy an unbounded
diagnostic payload into the contract. Access to referenced evidence is checked
by its owner. A control does not make that source public or turn it into a
business fact usable by a message `ref` field.

The room requires exactly the shown report fields and exactly `type`,
`reference`, `message` in `cause`. A missing reference is explicitly `null`.
IDs are nonempty valid UTF-8 of at most 256 bytes; `entry` is a positive JSON
safe integer. Cause type is 1–128 UTF-8 bytes, summary 1–2,048 bytes, and the
optional reference 1–256 bytes. Unknown fields and malformed bounds reject
the operation without settling anyone. A report ID starting with `redacted:`
is reserved for checker-projection placeholders and rejects as `invalid_contribution`.

The role view supplies `contribution_controls`, listing only the caller's
pending contributions with the exact operation, session, collection, entry and
member. The public operation descriptor supplies the report schema and limits.
An ordinary agent can use this directly. No local journal, Python runner or
particular model provider is required.

## Terminal means this contribution has ended

The host adapter MUST associate a report with the precise room-issued session,
collection entry and member before calling `fail_contribution`. A runtime job
or attempt ID alone is insufficient. The adapter must know that the assigned
contribution has ended, including any permitted retry or takeover policy.
A failure from a superseded attempt must not stop a replacement attempt still
working on that contribution. That execution-to-contribution mapping is host
integration work; the room does not infer it from error text.

Host authority is scoped to executions, not to the room. The adapter MUST
authenticate the `fail_contribution` caller as the host that runs that exact
session, collection entry and member, and MUST reject any other report with
`unauthorized_contribution` before invoking the reducer, settling no one.
Hosting one member's execution grants nothing over another member, another
owner's execution in a shared or cross-owner room, or another session. The
pure reducer receives `fail_contribution` as already-authorized host input and
cannot check this binding. Band's host channel is that authenticated adapter
endpoint; it is never a room message, a participant tool or a payload field.
Before enabling `room.contributions/1` in shared rooms, a Band integration MUST
qualify that one host's report for another host's member, and for another
session, is rejected.

These events leave the contribution pending:

- An error that the agent or host is still recovering from.
- A tool failure, failed model request or finished model turn without a terminal
  disposition of the assigned work.
- A rejected or malformed business reply.
- A system message with no trustworthy association to this contribution.

If no trustworthy terminal report is available, the existing deadline and any
declared offline grace decide when the wait ends. A valid review containing a
negative finding, refusal of a proposed change or unsuccessful test result is
still `ok` as a delivery. The report's payload and later workflow decide what
that finding means for the work.

## Admission, races and durable receipts

The room checks authority and report shape, then advances timers with its own
trusted clock before admission. A report MUST identify the active collection's
current entry and a pending member. Wrong sessions, stale rounds, unrelated
members and already-settled contributions reject. At the exact deadline the
deadline settles first. If a valid reply wins first, a later failure cannot
replace it. If a terminal failure wins first, a later reply cannot replace it.
Another collection round requires a new scoped report.

Authority is checked before the receipt lookup. A `stop_contribution` caller
that is not the named member's bound actor is rejected with
`unauthorized_contribution`, even when it replays another member's exact
accepted control after settlement. The report ID is unique in the session's
contribution-control namespace. Its fingerprint covers the report and the
producer class (`member` or `host`); because only the named member's actor
passes the authority check, a member receipt is only ever returned to that
member. An identical retry with the same producer class returns
`duplicate_contribution` and the original receipt fields, including after
collection closure or restore. A changed body or producer class with the same
ID rejects as `id_conflict`. Retries do not settle twice, change causes, renew
timers or consume capacity.

Each accepted control increments the state version, appends its collection log
record and retains its receipt. A session permits at most `max_messages`
accepted contribution controls in addition to its business-message budget.
Further new controls reject with `contribution_capacity`; existing deadlines
remain effective and retries remain available. The bound is finite and shared
across all collection rounds and both producers. Cause sizes and checkpoint
limits remain enforced. This is not host-signal accounting: `max_controls`, its
reserved terminal record and `control_capacity_exhausted`
([host signals](host-signals.md)) apply only to presence and membership
controls. Contribution controls have no reserved slot, and exhausting their
budget never changes the session's state.

Collections close when all members settle or their deadline arrives. A failed
member does not by itself fail the whole session. The result keeps its `failed`
status, settlement time and `failure` evidence separately from `message`, which
is `null` for a control outcome. Successful reports keep their original accepted
message identities and payloads. Zero successful reports is representable and
must be handled by the next state's guidance or decision.

Full checkpoints retain causes and receipts. Restore verifies their scope,
identity, fingerprint, member status, time and corresponding log entry. Member
views show their own result; receivers see the collection. Another contributor's
optional checker checkpoint keeps members' statuses, settlement times and
report message IDs, which it needs to validate its own reply. When that
contributor does not receive the collection, another member's failure is
private: its control ID becomes the opaque placeholder `redacted:` plus 32
hexadecimal digits (derived from the collection name, entry and member) wherever
it appears. Its cause keeps its shape with private values removed: `type` and
`message` become the string `"redacted"`, `reference` becomes `null`, and its
control receipt's fingerprint becomes 64 ASCII zeroes
([coordination](coordination.md#collect-results-then-continue)).
Only the trusted full checkpoint is suitable for authoritative room recovery.

## Reference implementation and compatibility

The local APIs are `Engine.stop_contribution/3` with authenticated actor context
and `Engine.fail_contribution/3` with trusted `authority: :host`. The trusted
JSON-lines adapters expose the corresponding operations. That pipe is not a
public API and MUST NOT be exposed directly to participants. An external host
must implement discovery, transport authentication, execution correlation,
durable commit and delivery before claiming integration.

Collect `failure type()` was removed. The compiler rejects it with a `syntax`
error whose detail points to these room contribution controls.

See the [portable schema](schemas/protocol-contributions.schema.json),
[runtime tests](../contract-lab/test/contribution_test.exs),
[current example](../contract-lab/contracts/v3/review-lanes.band), and
[portable assertions](tools/checkcoordination.py).
