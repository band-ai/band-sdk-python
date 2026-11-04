# Google ADK examples

`GoogleADKAdapterConfig.max_retries` defaults to `2`: each Gemini generation
request makes at most three HTTP attempts. Upgrading from SDK versions before
this default: ADK left native retries unset, so genai made a single HTTP attempt
per generation unless you configured retries yourself. Google genai owns
transient error classification, exponential backoff and jitter. Completed tools
and the ADK runner are never replayed. Exhausted errors retain normal turn
failure reporting. Generation uses HTTPX to avoid aiohttp reconnect resends
outside that budget.

Set `GoogleADKAdapterConfig(max_retries=0)` for one HTTP attempt per request.
The frozen config accepts budgets from `0` through `10` inclusive
(`GOOGLE_ADK_MAX_GENERATION_RETRIES`).

Native HTTP retries run inside one test attempt. They do not rescue a failed
test or guarantee provider availability. First-attempt E2E accounting keeps
exhausted errors, missing cells and required skips red.

For focused retry diagnostics, set the `google_genai._api_client` logger to
`INFO` to observe the native retry count, exception class and delay. Failed
requests contribute no fabricated token usage; successful model responses
contribute to one Band usage record for the turn.

### Declining a turn

The SDK-created single-agent ADK runner stops requesting model output after a
complete successful tool batch containing `band_no_reply` or a custom handler
marked `@declares_turn_effect(TurnEffect.DECLINE)`. Every sibling call still
finishes and its result is retained. A failed sibling keeps the next model
request available for recovery; malformed arguments and recognized failed tool
outputs remain visible as errors. This behavior qualifies the default
non-streaming runner with continuous function calling disabled.

Stopping the inner model loop does not cancel other rooms or outer orchestration.
Native fatal dispatch errors still abort the run. In ADK 1.10, an unknown sibling
can abort and let runner cleanup return before another tool handler settles;
runner cleanup alone does not guarantee sibling settlement. The separate
[lifecycle follow-up](https://linear.app/thenvoi/issue/INT-2001) tracks that limit.
Direct cancellation with held tool cleanup was separately qualified: cancellation
reached both handlers, and the adapter waited for their cleanup before closing
and propagating `CancelledError`. This does not establish a general guarantee
for repeated cancellation or every native execution mode.
