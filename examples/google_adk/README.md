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
