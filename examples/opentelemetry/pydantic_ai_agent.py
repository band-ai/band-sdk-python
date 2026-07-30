# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "band-sdk[pydantic-ai,logging]",
#   "opentelemetry-sdk>=1.44.0",
#   "opentelemetry-instrumentation-logging>=0.65b0",
# ]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Band agent with host-owned OpenTelemetry: correlated logs and framework spans.

Everything OpenTelemetry lives in ``otel_setup.py`` — Band itself depends on no
OpenTelemetry package. This script only wires the two together in the order that
works, and proves it: one application log emitted inside an explicit span carries
the same trace id the span reports, and Pydantic AI's own spans appear alongside.

Exports go to the console, so no collector is needed.

Run with:
    uv run examples/opentelemetry/pydantic_ai_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from otel_setup import telemetry
from band import Agent, LoggingStyle, LogSettings
from band.adapters import PydanticAIAdapter

SERVICE = "band-pydantic-ai-agent"

logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    with telemetry(SERVICE) as otel:
        # Order matters: Band's dictConfig replaces the root logger's handlers,
        # so the OTEL log handler goes on afterwards.
        LogSettings(log_console_style=LoggingStyle.JSON).for_application().configure()
        otel.attach_log_handler()

        # A log line inside a span: its otelTraceID matches the span the console
        # exporter prints, which is the whole point of the correlation fields.
        with otel.tracer.start_as_current_span("agent.startup"):
            logger.info("Starting Band agent with OpenTelemetry")

        # instrument=True hands the run to the global TracerProvider set above.
        # Pass an InstrumentationSettings instead when the host keeps its
        # providers out of the globals.
        adapter = PydanticAIAdapter(
            model="openai:gpt-5.4-mini",
            custom_section="You are a helpful assistant. Be concise and friendly.",
            instrument=True,
        )

        agent = Agent.from_config(
            "pydantic_agent",
            adapter=adapter,
            ws_url=ws_url,
            rest_url=rest_url,
        )

        await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
