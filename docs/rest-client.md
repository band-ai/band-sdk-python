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

## Tool result bytes

`serialize_tool_result` converts bytes in model results (including lists of
models) to padded **URL-safe base64**. This shared boundary affects every adapter
that consumes those results. UTF-8 bytes are encoded too: `b"hello"` becomes
`"aGVsbG8="`; return `str` when the result is readable text. Plain dicts and other
non-model values retain their types at this boundary.

`tool_result_text`, used by Parlant and MCP, also encodes bytes in plain values
and nested models. Decode with `base64.urlsafe_b64decode`, rather than a decoder
that only accepts the standard `+`/`/` alphabet. Image content blocks have their
own protocol encoding and do not use this text serialization rule.

```python
import json
from base64 import urlsafe_b64decode

from band.runtime.tools import tool_result_text

encoded = json.loads(tool_result_text({"payload": b"\xfb\xff"}))["payload"]
assert encoded == "-_8="
assert urlsafe_b64decode(encoded) == b"\xfb\xff"
```

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

The platform's `/next` endpoint selects the oldest delivery that is not processed,
including failed and processing deliveries. Exhausting the SDK's local retry budget
does not remove that message from the platform. The SDK preserves its failed status
and diagnostics rather than acknowledging unsuccessful work as processed.

When a locally completed, exhausted, self-authored or server-processed head prevents
`/next` from advancing, startup and idle recovery use
`BandLink.get_actionable_messages(room_id)` to collect one oldest-first snapshot.
Pagination finishes before any delivery status changes. Message IDs are deduplicated
without reordering the server's stream, including messages without timestamps.
The listing is unfiltered: separate pending and processing scans could miss an older
delivery whose status changes between requests. Cursor continuation takes precedence
over legacy page counts; legacy page metadata is used only before cursor mode starts.
An incomplete listing, transport failure or unusable cursor is a recovery failure,
not proof that the room is drained.

Recovery retains the current `/next` selection boundary. A 204 response never falls
back to a processing-status listing, which cannot establish whether the room is
stopped. Normal `/next` recovery already includes stale processing work in platform
order. Snapshot recovery likewise includes pending, retryable failed and processing
deliveries together, without a preliminary filtered sweep.

An eligible delivery that is in flight, refused by the platform, awaiting its
processed acknowledgement or still retryable retains its FIFO position and blocks
newer work. `InputNotAccepted` also retains that position without consuming the
failure budget. A successful local execution whose acknowledgement needs retrying
is never executed again.

Startup hands control back to the WebSocket queue at its synchronization marker only
once that delivery is terminal. A newer STOP, PLAY or INTERRUPT invalidates a snapshot
read across the control boundary, including an empty snapshot; no stale row is claimed
or executed. Claims and context hydration keep the runtime's existing control-revision
fences. Completing startup does not disable later reconnect synchronization.

An idle snapshot with no new work behind a retained head reports a blocked pass, not
a confirmed empty room, so it cannot grow the empty-room polling backoff.
