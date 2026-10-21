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

### Actionable page

`MessageLifecycle.get_actionable_page` (and `BandLink.get_actionable_page`) reads
one page of `agent_api_messages.list_agent_messages`: ascending, 100 per page
(`ACTIONABLE_PAGE_SIZE`, the platform's maximum), with `status` omitted so the
list holds every outstanding delivery, `processing` and `failed` included. The
cursor is omitted rather than sent as `None`, and a page that reports `has_more`
without a `next_cursor` raises `ValueError`. Failures propagate to the caller;
only the room scheduler decides how to retry. `/next` and this list both omit
some deliveries the WebSocket carries; see
[queued-message selection](websocket-events.md#queued-message-selection).

### Delivery status metadata

`ChatMessageMetadata` in the pinned client declares only `delegation`. Its
`delivery_status` survives deserialization only through `extra="allow"`: a
dependency contract the SDK requires, not a declared field. Batch selection relies on it to skip a
delivery the platform already reports `failed` for this agent, so a regenerated
client that stops preserving extras would silently turn that exclusion off. This
block runs against the real model and fails if the field is dropped:

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from band_rest.types.chat_message import ChatMessage

from band.core.types import delivery_status_for_agent
from band.platform.message_lifecycle import MessageLifecycle

failed = ChatMessage.model_validate(
    {
        "id": "m1",
        "content": "@bot hi",
        "message_type": "text",
        "sender_id": "u1",
        "sender_type": "User",
        "metadata": {"delivery_status": {"agent-1": {"status": "failed"}}},
    }
)
rest = MagicMock()
rest.agent_api_messages.list_agent_messages = AsyncMock(
    return_value=SimpleNamespace(
        data=[failed], metadata=SimpleNamespace(has_more=False, next_cursor=None)
    )
)


async def main() -> None:
    page = await MessageLifecycle().get_actionable_page(rest, "room-1")

    assert page.next_cursor is None
    assert delivery_status_for_agent(page.messages[0].metadata, "agent-1") == "failed"
    kwargs = rest.agent_api_messages.list_agent_messages.call_args.kwargs
    assert kwargs["sort_order"] == "asc"
    assert kwargs["limit"] == 100
    assert "status" not in kwargs and "cursor" not in kwargs


with asyncio.Runner() as runner:
    runner.run(main())
```

### Terminal write results

`mark_processed_result` and `mark_failed_result` (on `BandLink` and
`MessageLifecycle`) perform the single logical processed/failed write and return
a `SettlementResult`:

| Result | Evidence |
| --- | --- |
| `ACCEPTED` | HTTP 200 with a valid success payload |
| `NOT_FOUND` | Any proven HTTP 404, including an empty, malformed or schema-invalid body |
| `REJECTED` | HTTP 422 with `error.code == "validation_error"` and a non-empty `error.details["/processing_attempt"]` list: the platform's no-current-attempt error |
| `UNCONFIRMED` | Everything else: transport or parse failure, `success: false`, 401/403/409/503, any other 422 |

HTTP 204 still raises `RoomExecutionStoppedError`; it is not a result. `mark_processed`
and `mark_failed` are projections that return `result is SettlementResult.ACCEPTED`,
so every non-accepted result reads as `False`. `mark_processing` keeps its plain
bool contract. One call is one logical write, but the generated client's
`max_retries=3` still permits up to four HTTP attempts on 408/409/429/5xx.

The `*_result` methods are additive: the boolean methods keep their signatures and
literal `True`/`False` returns, cancellation still propagates, and one-shot
invocation stays on the boolean facade. Runtime recovery branches on the enum
(never its truthiness) because it must tell a write to retry (`UNCONFIRMED`) from
one to park (`NOT_FOUND`, `REJECTED`); see
[settlement](turn-outcome.md#terminal-intents-and-settlement). `NOT_FOUND` does
not say whether the message was deleted or room access was lost: the platform
answers both with the same 404.
