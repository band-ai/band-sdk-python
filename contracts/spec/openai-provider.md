# Native OpenAI provider binding

This optional binding implements `remote-agent/1` through the Responses API.
Other implementations MAY omit it, but MUST declare that omission and MUST NOT
accept its configuration or claim its profiles. Command callbacks remain
supported. The room and local checker retain their authority.

## Configuration and exact input

`agent.provider` accepts `{kind:"openai",model,...}` under the service schema.
The model is an explicit host-selected ID; qualification SHOULD pin a snapshot.
Defaults are 30,000 ms, 2,048 output tokens and a 65,536-byte request body.
Allowed bounds are 1–120,000 ms, 16–8,192 output tokens and 1,024–262,144 request
bytes. Responses are capped at 131,072 bytes. Existing durable call/attempt
budgets cover response, wake and repair operations.

`remote-agent/openai-provider/1` identifies the model, sidecar-prompt SHA-256 and
canonical policy SHA-256. The policy object is exactly `{model,max_output_tokens,
max_request_bytes,timeout_ms,pricing}`; omitted pricing becomes null. Changing
this configuration requires the ordinary reviewed service migration. The configured
integer `timeout_ms` MUST be preserved exactly in the policy hash and prepared
request. Conversion to seconds is only for process/socket deadlines, never a
round-trip through floating-point arithmetic into the journaled policy.

`remote-agent/openai-request/1` contains `body`, `timeout_ms`,
`max_response_bytes:131072` and nullable `pricing`. The body contains the explicit
model, host sidecar prompt as `instructions`, and canonical JSON text as `input`
encoding `{operation,program_source,bundle}`. Source bytes are checked against
the pinned artifact. The other body fields are exactly:

```json
{"store":false,"stream":false,"truncation":"disabled","service_tier":"default","text":{"format":{"type":"json_object"}},"max_output_tokens":2048}
```

The configured output bound replaces the illustrative 2,048. The host MUST
journal this exact prepared input and reserve the call before dispatch. It MUST
NOT include credentials in the input, argv, state or metrics. The trusted worker
reads `OPENAI_API_KEY` from its explicit allowlisted environment. Only this native
worker receives that key; engine, command-provider, validator and generated
program children do not inherit host provider credentials. Other ambient host
variables are not forwarded to the worker.

The worker MUST send canonical body bytes using certificate- and
hostname-verified TLS to exactly `https://api.openai.com/v1/responses`. It MUST
NOT accept a participant-selected destination, follow redirects, invoke tools
or retry automatically. A killable child enforces the parent's absolute time
bound in addition to socket timeouts and byte limits.

The API uses `text.format` for output configuration. JSON mode guarantees less
than schema validity, so the host MUST still validate strict JSON, operation and
state shape, local admission and final room acceptance.
See the [Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)
and [structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

## Results and failures

`remote-agent/openai-result/1` contains nullable HTTP `status`, nullable bounded
`request_id`, base64 response `body`, `complete`, nullable safe `failure`,
`uncertain` and `redacted`. The entire worker result is ordinary captured process
stdout. It MUST be durably recorded before installing source or staging a reply.

Exact credential occurrences in the response body or request ID are replaced
with `[REDACTED_CREDENTIAL]`; `redacted` MUST indicate either change. Otherwise
the bounded body is preserved, including floating-point API metadata. Redaction
also obeys the byte cap. The generated business JSON separately uses the strict
safe-integer codec. Duplicate provider/generated keys, nonfinite numbers and
malformed results MUST be refused.

Only an API response with `status:"completed"` and one assistant message of
`output_text` items may supply a business result. Refusal, incomplete output,
unexpected tool/items, invalid operation results and provider-declared errors
MUST NOT become room effects. Response, wake and repair retain their distinct
host checks and permissions.

Missing credentials and known request/rate-limit refusals are definitive
failures. Network failure after dispatch, HTTP 408/5xx, response overflow and
parent timeout are uncertain. Uncertain outcomes MUST retain the original
execution and MUST NOT be resampled by polling or recovery. Explicit authorized
retry follows existing abandonment/budget rules. Diagnostics use safe categories,
never arbitrary remote error strings.

## Usage and estimates

`provider_usage` records reported input, cached-input and output tokens plus
nullable `estimated_cost_micro_usd`. Cached input MUST be a subset of total
input. Missing/invalid usage is unknown, not zero. Reported usage can count even
when refusal or incomplete output prevents accepting a business result.

Optional `pricing` contains `as_of` and integer nanodollars per token for input,
cached input and output. Rates are explicit operator policy, each 0–100,000,
with cached input no greater than input. This cap is $100 per million tokens
for each category. If any applicable rate exceeds the cap, operators MUST omit
`pricing` (or use null), making cost unknown; they MUST NOT clamp or substitute
a lower rate. The request MUST pin `service_tier` to
`default`. Rates apply only when the returned model exactly matches the configured
model and the returned `service_tier` is explicitly `default`. A missing or different
served tier leaves cost unknown while valid token usage remains reportable.
The served tier can differ from the requested tier; see the official
[Responses API reference](https://developers.openai.com/api/reference/resources/responses/methods/create).
The [processing-tier guide](https://developers.openai.com/api/docs/guides/fast-mode)
also explains why the response tier, not the requested tier, determines the rate.
The estimate is:

```text
ceil(((input_tokens - cached_input_tokens) * input_rate
      + cached_input_tokens * cached_rate + output_tokens * output_rate) / 1000)
```

This rounds up by less than one micro-dollar per call and is not an invoice.
The example GPT-4.1 Mini rates of 400/100/1,600 nanodollars per token were checked
October 10, 2026 against the [official model page](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
Operators MUST verify applicable rates when qualifying their release.

`model_usage` MUST commit atomically with the model result. Concurrent response
and repair completions cannot lose increments; recovery cannot count a result
twice. The aggregate exposes `reported_calls`, `unreported_calls`, input/cached/
output totals, `costed_calls`, summed estimated micro-dollar cost and
`currency:"USD"`. Unreported calls include reserved calls without usable usage,
legacy callbacks and unresolved requests. Cost totals cover only costed calls.
Quality, saved calls, actual tokens and cost estimates MUST remain distinct.

Observer metrics and operator inspection MUST omit prompts, source, provider
bodies and credentials. Offline replay returns the recorded worker result
without an API request or a room effect. The protocol schema defines all fields.
