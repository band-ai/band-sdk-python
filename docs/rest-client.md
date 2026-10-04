# REST Client

`band-client-rest` is the generated Fern client behind `link.rest`, pinned exactly in
`pyproject.toml`. The pin discipline is what the code cannot say for itself.

Before writing a workaround for a bug in the generated client:

1. **Check for a fixed release first.** `pip index versions band-client-rest`, then diff the
   relevant model or method between the pin and the newer version.
2. **If it is fixed upstream, bump the pin.** That is the default action. Only a cited
   blocker (failing CI, an unresolved conflict) justifies a workaround; "inconvenient" is not
   one.
3. **If the workaround is still needed, tie it to the pin:** comment the exact version at
   which it stops being reachable, so it cannot sit dead after a later bump.
4. **Make a test against the real dependency the tripwire** (not a stubbed exception), and
   confirm the CI result is real: a grouped Dependabot bump (`uv-minor-and-patch`) can fail at
   collection from an unrelated package first and hide it.

## Tool path identifiers

Tool inputs validate REST path identifiers before dispatch using the shared
[identifier definitions](../src/band/runtime/tools/inputs/identifiers.py). IDs
contain ASCII letters, digits, underscores and hyphens; empty values, whitespace
and routing delimiters are rejected with standard field-specific tool feedback.
Accepted strings retain their exact spelling. Task references also accept the
existing leading `#` shorthand, including board numbers and UUIDs. This lexical
check does not establish resource existence. Direct REST client calls remain
responsible for their own input validation.

## Lifecycle acceptance

`MessageLifecycle` uses the public `agent_api_messages.with_raw_response` API
for `/next` and processing/processed/failed marks. Only an actual HTTP 204 is
quiet on `/next`; on marks it is a stopped-execution refusal. An accepted mark
requires HTTP 200 with valid success data. Empty errors and empty success bodies
cannot establish acceptance.

The generated client's empty-body shortcut runs before status classification in
pinned `band-client-rest` 0.0.41 and remains in released 0.0.46. Public raw response
status preserves the distinction without accessing generated private fields or
adding another HTTP client. Real-client HTTP transport tests pin this boundary.

## Retained failed messages and recovery

The platform's `/next` endpoint returns the oldest delivery that is not processed,
including failed and processing deliveries. Exhausting the SDK's local retry budget
does not remove that message from the platform. The SDK preserves its failed status
and diagnostic information rather than acknowledging unsuccessful work as processed.

When a retained, locally skipped head prevents `/next` from advancing, startup and
idle recovery use `BandLink.get_actionable_messages(room_id)` to collect pending,
failed, and processing deliveries. Recovery completes pagination before changing
delivery statuses, deduplicates message IDs, and processes eligible work oldest first.
It starts with the current cursor-based API, prefers a usable cursor over legacy
page counts, and also accepts legacy page metadata when no cursor is supplied.
An incomplete listing, transport failure, or unusable continuation cursor is a
recovery failure, not proof that the room is drained.

An eligible message that is in flight, cannot be claimed, still needs its processed
acknowledgement, or fails within its remaining retry budget keeps its FIFO position
and blocks newer work until recovery can advance. Locally completed messages are
not executed again. Startup recovery includes older pending and failed deliveries
alongside stale processing work so a crash-recovery sweep does not overtake them.
