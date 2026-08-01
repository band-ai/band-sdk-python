"""Pydantic field validation on Phase 0 contract models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.core.contracts import (
    BackendContext,
    DeliveryReceipt,
    EnvelopedTurnEvent,
    ModelRequest,
    ModelSamplingOptions,
    ModelToolCall,
    ThoughtEvent,
    ToolCallEvent,
)
from band.runtime.tools import BAND_LIST_CONTACTS


def test_delivery_receipt_rejects_non_posting_tool() -> None:
    with pytest.raises(ValidationError, match="room-posting"):
        DeliveryReceipt(tool_name=BAND_LIST_CONTACTS)


def test_backend_context_defaults() -> None:
    ctx = BackendContext()
    assert ctx.agent_name == ""
    assert ctx.agent_description == ""
    assert BackendContext(agent_name="bot").agent_name == "bot"


def test_model_sampling_bounds() -> None:
    with pytest.raises(ValidationError):
        ModelSamplingOptions(temperature=-0.1)
    with pytest.raises(ValidationError):
        ModelSamplingOptions(temperature=2.1)
    with pytest.raises(ValidationError):
        ModelSamplingOptions(max_output_tokens=0)
    assert (
        ModelSamplingOptions(temperature=1.0, max_output_tokens=16).temperature == 1.0
    )


def test_model_request_requires_messages() -> None:
    with pytest.raises(ValidationError):
        ModelRequest(messages=[])


def test_model_tool_call_requires_ids() -> None:
    with pytest.raises(ValidationError):
        ModelToolCall(id="", name="x", arguments={})
    with pytest.raises(ValidationError):
        ModelToolCall(id="1", name="", arguments={})


def test_tool_call_event_requires_tool_name() -> None:
    with pytest.raises(ValidationError):
        ToolCallEvent(tool_name="")


def test_enveloped_turn_event_sequence_non_negative() -> None:
    with pytest.raises(ValidationError):
        EnvelopedTurnEvent(
            run_id="r1",
            sequence=-1,
            timestamp=0.0,
            event=ThoughtEvent(content="hi"),
        )
