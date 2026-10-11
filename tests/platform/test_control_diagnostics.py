from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest
from phoenix_channels_python_client.phx_messages import Event
from phoenix_channels_python_client.utils import make_message

from band.client.streaming.client import AgentControlPayload, WebSocketClient
from band.testing import fake_phoenix_server
from tests.e2e.baseline.toolkit.diagnostics import (
    OBSERVATION_LIMIT,
    RAW_FRAME_LIMIT,
    TRANSPORT_LOGGERS,
    collect_control_transport,
)

AGENT_ID = "diagnostic-agent"
TOPIC = f"agent_control:{AGENT_ID}"
CANARY = "secret-canary-must-not-escape"


def control_payload(correlation_id: str) -> dict[str, object]:
    return {
        "mode": "stop",
        "scope": "room",
        "agent_id": AGENT_ID,
        "room_id": "diagnostic-room",
        "execution_id": None,
        "correlation_id": correlation_id,
        "reason": CANARY,
        "unexpected_credentials": CANARY,
    }


async def test_pre_filter_observation_distinguishes_dropped_from_delivered_controls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    typed = []
    final_received = asyncio.Event()

    async def on_control(payload: AgentControlPayload) -> None:
        typed.append(payload.correlation_id)
        if payload.correlation_id == "final":
            final_received.set()

    async def on_supersede(payload: object) -> None:
        raise AssertionError("Unexpected supersede")

    with collect_control_transport(AGENT_ID, enabled=True) as diagnostics:
        async with (
            fake_phoenix_server() as server,
            WebSocketClient(server.url, CANARY, agent_id=AGENT_ID) as client,
        ):
            await client.join_agent_control_channel(AGENT_ID, on_supersede, on_control)
            await server.push(TOPIC, "agent.control", control_payload("first"))
            for join_ref, correlation_id in (("stale", "stale"), (None, "null")):
                message = make_message(
                    topic=TOPIC,
                    event=Event("agent.control"),
                    payload=control_payload(correlation_id),
                    join_ref=join_ref,
                )
                assert server._connection is not None
                await server._connection.send(
                    server._protocol.serialize_message(message)
                )
            await server.push(TOPIC, "agent.control", control_payload("final"))
            await asyncio.wait_for(final_received.wait(), timeout=5)

    assert diagnostics.control_correlations == ["first", "stale", "null", "final"]
    assert typed == ["first", "final"]
    assert diagnostics.dropped_join_refs == ["stale", None]
    assert diagnostics.join_acknowledged
    assert CANARY not in json.dumps(diagnostics.snapshot())
    assert CANARY not in caplog.text


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_enabled_capture_preserves_failures_and_restores_logging(
    failure: type[BaseException],
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger(TRANSPORT_LOGGERS[0])
    output = io.StringIO()
    original_level, original_handlers, original_propagate = (
        logger.level,
        logger.handlers,
        logger.propagate,
    )
    handler = logging.StreamHandler(output)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        with (
            pytest.raises(failure, match="probe failed"),
            collect_control_transport(AGENT_ID, enabled=True) as diagnostics,
        ):
            logger.debug(
                "Parsing raw message: %s",
                json.dumps(
                    ["1", None, TOPIC, "agent.control", control_payload("safe")]
                ),
            )
            logger.warning("Transport warning remains observable")
            logger.error("Other agent failure remains observable")
            raise failure("probe failed")
        logger.info("original handler restored")
        assert output.getvalue().splitlines() == [
            "Transport warning remains observable",
            "Other agent failure remains observable",
            "original handler restored",
        ]
        assert logger.handlers == [handler]
        assert logger.level == logging.INFO
        assert logger.propagate is False
        assert CANARY not in json.dumps(diagnostics.snapshot())
        assert "Control transport observations:" in caplog.text
        assert CANARY not in caplog.text
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate


def test_capture_bounds_output_filters_other_agents_and_handles_malformed_frames() -> (
    None
):
    logger = logging.getLogger(TRANSPORT_LOGGERS[0])
    with collect_control_transport(AGENT_ID, enabled=True) as diagnostics:
        for index in range(OBSERVATION_LIMIT + 3):
            logger.debug(
                "Parsing raw message: %s",
                json.dumps(
                    ["1", None, TOPIC, "agent.control", control_payload(str(index))]
                ),
            )
        logger.debug(
            "Parsing raw message: %s",
            json.dumps(
                [
                    "1",
                    None,
                    "agent_control:other-agent",
                    "agent.control",
                    control_payload(CANARY),
                ]
            ),
        )
        logger.debug("Parsing raw message: %s", "invalid-json")
        logger.debug("Parsing raw message: %s", " " * (RAW_FRAME_LIMIT + 1))
        logger.debug(
            "Parsing raw message: %s",
            json.dumps(["1", None, TOPIC, [], {"status": {}}]),
        )
    snapshot = diagnostics.snapshot()
    assert len(snapshot["observations"]) == OBSERVATION_LIMIT
    assert snapshot["dropped_observations"] == 3
    assert snapshot["oversized_frames"] == 1
    assert snapshot["observations"][-1]["correlation_id"] == str(OBSERVATION_LIMIT + 2)
    assert CANARY not in json.dumps(snapshot)


def test_disabled_capture_preserves_normal_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger(TRANSPORT_LOGGERS[0])
    with collect_control_transport(AGENT_ID, enabled=False) as diagnostics:
        logger.warning("Existing transport warning remains observable")
    assert caplog.messages == ["Existing transport warning remains observable"]
    assert diagnostics.snapshot()["observations"] == []
