# REST Client

How the SDK talks to the Band REST API through the generated Fern client,
the `None`-vs-omit gotcha that trips up every optional parameter, and the
discipline for working around bugs in that generated client.

## REST Client API Pattern

The SDK uses Fern-generated REST client with property-based namespace API:

```python notest
# Pattern: agent_api_<resource>.method()
await link.rest.agent_api_chats.create_agent_chat(...)
await link.rest.agent_api_participants.list_agent_chat_participants(...)
```

**Sub-clients**: `identity`, `peers`, `contacts`, `chats`, `messages`, `events`, `participants`, `context`, `memories`, `files`, `profile`, `agents`

### Exception: chat-room sends

Posting a message or event to a room is the one place that does **not** call
the Fern client directly. `create_agent_chat_message` /
`create_agent_chat_event` go through `band.platform.posting`, which enforces
the platform's content rules (visible content, the events content cap) once
for every caller:

```python notest
from band.platform.posting import post_event, post_message

# rest / room_id / request are keyword-only.
await post_message(rest=link.rest, room_id=room_id, request=ChatMessageRequest(...))
await post_event(rest=link.rest, room_id=room_id, request=ChatEventRequest(...))
```

Both return `None` without making a request when the content has no visible
characters (whitespace-only, zero-width), rather than letting the platform
reject it:

```python fixture:client
from band.client.rest import ChatEventRequest, ChatMessageRequest
from band.platform.posting import post_event, post_message

message = await post_message(
    rest=client,
    room_id="room-1",
    request=ChatMessageRequest(content="   ", mentions=[]),
)
event = await post_event(
    rest=client,
    room_id="room-1",
    request=ChatEventRequest(content="\u200b", message_type="thought"),
)

assert message is None
assert event is None
```

A new direct call to either Fern method inside `src/band` fails
`tests/platform/test_posting_boundary.py`.

## REST Client OMIT vs Null

When calling REST endpoints with optional parameters, **never pass `None`** - the Fern client sends `null` which fails backend validation. Instead, use kwargs:

```python fixture:client
# WRONG - sends {"action": "approve", "handle": null, "request_id": "..."}
await client.agent_api_contacts.respond_to_agent_contact_request(action="approve", handle=None, request_id="...")

# CORRECT - sends {"action": "approve", "request_id": "..."}
kwargs = {"action": "approve", "request_id": "..."}
await client.agent_api_contacts.respond_to_agent_contact_request(**kwargs)
```

## Workarounds for band-client-rest Bugs

`band-client-rest` is pinned exactly (`pyproject.toml`, currently `==0.0.27`). Before
writing a workaround, check whether a newer release already fixes it upstream:

- `pip index versions band-client-rest`, then diff the relevant model/method
  (`uv pip install band-client-rest==<newer> --target /tmp/check`).
- Already fixed upstream → bump the pin. Default action, not a suggestion.
  Only write a workaround after confirming the bump is actually blocked (cite
  the blocker: failing CI, unresolved conflict) — "inconvenient" isn't one.
- Still needed → tie it to the pin: comment naming the exact version where
  it stops being reachable, so it's not silently dead code after the next bump.
- A test against the real dependency (not a stubbed exception) doubles as
  that tripwire. Check the CI status is real, though — a grouped Dependabot
  bump can fail at collection from an unrelated package first, hiding it.

Example (PR #531): a `resolve_handle` workaround for missing `data.id` was
scoped to `0.0.10`. `0.0.15` already dropped the `id` field from
`ResolvedEntity` upstream. Bumped straight to `0.0.26` (the pin has since moved
further), deleted the workaround — no version guard needed once the fix is
already upstream.
