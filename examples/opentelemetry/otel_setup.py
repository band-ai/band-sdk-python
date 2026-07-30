"""Host-owned OpenTelemetry pipeline for a Band agent.

Band creates no ``TracerProvider``, ``LoggerProvider``, processor, or exporter,
and depends on no OpenTelemetry package. The host owns the pipeline; this module
is that half of the demo — one ``Resource`` shared by traces and logs, console
exporters instead of a collector, and a clean flush on the way out.

Setup order is the part that is easy to get wrong, so it is split across two
calls:

1. :func:`telemetry` stands up the providers and turns on trace-context
   injection. It installs no logging handler.
2. :meth:`Telemetry.attach_log_handler` installs one, and must be called
   **after** Band configures logging: ``logging.config.dictConfig`` is
   non-incremental, so it replaces the root logger's handlers and a handler
   attached earlier is silently dropped.

Injection itself is order-independent — it is a log-record factory, not a
handler — so ``band.*`` records carry the live trace whichever way round the
two are configured.

Nothing here is published as an OpenTelemetry global. The providers are handed
to each consumer explicitly, so the pipeline can be started, shut down, and
started again — ``set_tracer_provider`` takes only the first call of a process
and would leave the second run writing into a provider that has already been
shut down.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from opentelemetry import trace
from opentelemetry.instrumentation.logging import LoggingInstrumentor

# The SDK's own LoggingHandler is deprecated in favour of this one.
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    ConsoleLogRecordExporter,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


@dataclass
class Telemetry:
    """A live pipeline: the providers to hand out, and the log sink."""

    tracer_provider: TracerProvider
    logger_provider: LoggerProvider
    handler: LoggingHandler | None = field(default=None, repr=False)

    @property
    def tracer(self) -> trace.Tracer:
        """Tracer for the host's own spans."""
        return self.tracer_provider.get_tracer(__name__)

    def attach_log_handler(self, level: int = logging.INFO) -> LoggingHandler:
        """Export Python log records as OTEL logs, exactly once.

        Call it after Band's logging setup. Calling it again is safe — it
        re-attaches the same handler rather than adding a second one, so a
        process that reconfigures logging does not start exporting duplicates.
        """
        if self.handler is None:
            self.handler = LoggingHandler(
                level=level, logger_provider=self.logger_provider
            )
        root = logging.getLogger()
        if self.handler not in root.handlers:
            root.addHandler(self.handler)
        return self.handler

    def detach_log_handler(self) -> None:
        """Stop exporting Python log records."""
        if self.handler is not None:
            logging.getLogger().removeHandler(self.handler)


@contextmanager
def telemetry(service_name: str) -> Iterator[Telemetry]:
    """Run the host's OpenTelemetry pipeline for the duration of the block."""
    resource = Resource.create({SERVICE_NAME: service_name})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(ConsoleLogRecordExporter())
    )

    # Adds otelTraceID / otelSpanID / otelTraceSampled / otelServiceName to every
    # log record; Band's JSON formatter already carries those keys. The provider
    # is passed explicitly, so the service name comes from this Resource and no
    # global is consulted. No handler is installed here — the host owns that,
    # after Band's logging setup.
    LoggingInstrumentor().instrument(
        tracer_provider=tracer_provider,
        inject_trace_context=True,
        enable_log_auto_instrumentation=False,
    )

    pipeline = Telemetry(tracer_provider, logger_provider)
    try:
        yield pipeline
    finally:
        pipeline.detach_log_handler()
        LoggingInstrumentor().uninstrument()
        # Console exporters batch, so an un-flushed exit loses the tail.
        tracer_provider.force_flush()
        logger_provider.force_flush()
        tracer_provider.shutdown()
        logger_provider.shutdown()
