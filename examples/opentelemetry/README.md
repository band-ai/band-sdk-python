# OpenTelemetry with Band

Band emits standard Python log records and standard framework calls. It creates
no `TracerProvider`, no `LoggerProvider`, no processor, and no exporter, and it
depends on no OpenTelemetry package — the host process owns the telemetry
pipeline. This example is the host half, in about 100 lines.

| File | What it is |
|---|---|
| `otel_setup.py` | The pipeline: one shared `Resource`, both providers, console exporters, trace-context injection, flush and shutdown |
| `pydantic_ai_agent.py` | A normal Band agent that turns the pipeline on and traces its Pydantic AI runs |

## Run it

Needs a Band agent in `agent_config.yaml` under the key `pydantic_agent`, plus
`BAND_WS_URL`, `BAND_REST_URL`, and `OPENAI_API_KEY`.

```bash
uv run examples/opentelemetry/pydantic_ai_agent.py
```

Everything exports to the console, so there is no collector to run and no OTLP
endpoint to configure.

## What you should see

A Band log line, in JSON, carrying the span it was emitted inside:

```json
{"timestamp": "2026-07-30 21:20:00,123", "level": "INFO", "logger": "__main__", "message": "Starting Band agent with OpenTelemetry", "otelTraceID": "0af7651916cd43dd8448eb211c80319c", "otelSpanID": "b7ad6b7169203331", "otelTraceSampled": true, "otelServiceName": "band-pydantic-ai-agent"}
```

…and the same trace on stdout from the span exporter:

```json
{
    "name": "agent.startup",
    "context": {
        "trace_id": "0x0af7651916cd43dd8448eb211c80319c",
        "span_id": "0xb7ad6b7169203331"
    },
    "resource": { "attributes": { "service.name": "band-pydantic-ai-agent" } }
}
```

Then, once someone messages the agent, Pydantic AI's own spans arrive the same
way, one trace per turn: `invoke_agent agent` over a `chat <model>` span per
model call — carrying the `gen_ai.*` attributes, token counts included — and an
`execute_tool band_send_message` span for the reply the agent posts to the room.

Band's logs go to stderr and the exporters write to stdout, so `2>/dev/null`
leaves you with pure telemetry and `1>/dev/null` with pure logs.

## The order that matters

```python
with telemetry(SERVICE) as otel:          # 1. providers + trace-context injection
    LogSettings(...).configure()          # 2. Band's logging
    otel.attach_log_handler()             # 3. the OTEL log handler
```

Step 3 comes last because `logging.config.dictConfig` is non-incremental: it
*replaces* the root logger's handlers, so a handler attached before step 2 is
silently dropped and exports nothing. Band applies its configuration with
`dictConfig`, and offers no "keep my handlers" option — a handler it preserved
would already have been closed.

Step 1 can go either side of step 2. Injection is a log-record factory rather
than a handler, so `dictConfig` never touches it.

The four correlation keys (`otelTraceID`, `otelSpanID`, `otelTraceSampled`,
`otelServiceName`) are in Band's default JSON output. Without instrumentation
they are `null`; the log schema does not change shape when you turn tracing on.
Choosing your own `json_fields` replaces that default wholesale — splice
`*OTEL_CORRELATION_FIELDS` into your list to keep correlation.

## Choices worth knowing about

- **`LoggingInstrumentor` can install the log handler for you** — that is its
  `enable_log_auto_instrumentation` default, and that handler survives
  `dictConfig` because the package monkey-patches it. This example opts out and
  attaches its own instead, so the handler and its level stay the host's, and
  the ordering rule above is the same one that applies to any other handler you
  own (a shipper, a Sentry handler).
- **No provider is published globally.** Each consumer is handed one — the
  instrumentor, the log handler, and the adapter's
  `InstrumentationSettings(tracer_provider=...)`. `set_tracer_provider` takes
  only the first call of a process, so a helper that both publishes and shuts
  down its providers leaves a second run writing into a dead one. A host that
  *does* own the globals (the usual thing for a long-lived service, and what
  other instrumentors look for) can publish them once at startup and then use
  the shorter `PydanticAIAdapter(instrument=True)`.
- **Console exporters batch.** The context manager force-flushes both providers
  before shutting them down; without that, the tail of a short run is lost.

## References

- [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)
- [`opentelemetry-instrumentation-logging`](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/logging/logging.html)
- [Pydantic AI instrumentation](https://ai.pydantic.dev/logfire/)
