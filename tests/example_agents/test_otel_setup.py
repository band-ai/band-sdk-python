"""Tests for the host-owned OpenTelemetry pipeline in examples/opentelemetry.

The example is the executable answer to "how do I get Band logs into my tracing
stack", so what is pinned here is the part a reader would otherwise get wrong:
the setup order, and that a Band log line really does carry the surrounding
span. Driven by real OpenTelemetry objects — a fake pipeline would prove nothing
about the wiring.
"""

from __future__ import annotations

import json
import logging

import pytest
from opentelemetry.instrumentation.logging.handler import LoggingHandler

from band import LogSettings
from tests.loaders import load_script_module
from tests.logsupport import band_log_env, restored_logging

otel_setup = load_script_module(
    "examples/opentelemetry/otel_setup.py", "otel_setup_example"
)

SERVICE = "band-otel-test"


def otel_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if isinstance(h, LoggingHandler)]


def test_band_json_log_carries_the_surrounding_span(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ``band.*`` record emitted inside a span reports that span's ids.

    Band's JSON formatter always carries the correlation keys; the instrumentor
    started by ``telemetry()`` is what fills them in.
    """
    with (
        restored_logging(),
        band_log_env(monkeypatch, CONSOLE_STYLE="json", FILE=None),
        otel_setup.telemetry(SERVICE) as pipeline,
    ):
        LogSettings().configure()
        pipeline.attach_log_handler()
        with pipeline.tracer.start_as_current_span("probe") as span:
            logging.getLogger("band.probe").info("correlated")
            context = span.get_span_context()

    record = json.loads(capsys.readouterr().err)

    assert record["message"] == "correlated"
    assert record["otelTraceID"] == format(context.trace_id, "032x")
    assert record["otelSpanID"] == format(context.span_id, "016x")
    assert record["otelServiceName"] == SERVICE


def test_attaching_twice_keeps_one_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running host setup must not export every record twice."""
    with (
        restored_logging(),
        band_log_env(monkeypatch, FILE=None),
        otel_setup.telemetry(SERVICE) as pipeline,
    ):
        LogSettings().configure()
        first = pipeline.attach_log_handler()
        second = pipeline.attach_log_handler()

        assert second is first
        assert otel_handlers() == [first]


def test_handler_attached_before_band_config_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Why the example attaches last: Band's dictConfig replaces root's handlers.

    This handler does not defend itself against that (unlike the one
    ``LoggingInstrumentor`` installs for you, which patches ``dictConfig``), so
    attaching it first would silently export nothing.
    """
    with (
        restored_logging(),
        band_log_env(monkeypatch, FILE=None),
        otel_setup.telemetry(SERVICE) as pipeline,
    ):
        pipeline.attach_log_handler()
        LogSettings().configure()

        assert otel_handlers() == []

        pipeline.attach_log_handler()
        assert otel_handlers() == [pipeline.handler]
