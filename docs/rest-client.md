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
