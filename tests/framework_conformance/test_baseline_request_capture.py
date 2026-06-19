"""Request-capture substrate tests for baseline conformance rows."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from band.runtime.tools import AgentTools, CHAT_TOOL_NAMES

from tests.framework_conformance.baseline_applicability import applicability_for
from tests.framework_conformance.baseline_scenarios import SCENARIOS_BY_ID
from tests.framework_conformance.platform_fixtures import (
    AGENT_ID,
    CURRENT_MESSAGE_ID,
    ConformanceExecutionContext,
    build_agent_input_through_preprocessor,
    canonical_history,
    canonical_participants,
    canonical_peers,
    completed_tool_history,
    current_message_event,
    pending_work_state,
)
from tests.framework_conformance.request_capture import (
    REQUEST_CAPTURE_ADAPTER_IDS,
    ConformanceSchemaRecorder,
    CapturedRequest,
    _observed_message_ids,
    canonical_tool_names,
    capture_request,
    schema_tool_names,
    visible_text,
)
from tests.framework_conformance.baseline_status import SeamOwner


@pytest.mark.asyncio
async def test_preprocessor_builds_agent_input_from_realistic_platform_context() -> (
    None
):
    ctx = ConformanceExecutionContext(
        history_messages=canonical_history(),
        pending_system_messages=["[Contacts]: @darvell/greeter is now available."],
    )

    agent_input = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(),
        agent_id=AGENT_ID,
    )

    assert agent_input.msg.id == CURRENT_MESSAGE_ID
    assert agent_input.msg.metadata.mentions[0].handle == "darvell/test-agent"
    assert [
        message["metadata"]["source_message_id"] for message in agent_input.history.raw
    ] == [
        "msg-history-001",
        "msg-history-002",
        "msg-history-003",
    ]
    assert isinstance(agent_input.tools, AgentTools)
    assert agent_input.participants_msg is not None
    assert "@darvell/calc" in agent_input.participants_msg
    assert "(Agent)" in agent_input.participants_msg
    assert agent_input.contacts_msg == "[Contacts]: @darvell/greeter is now available."
    assert agent_input.is_session_bootstrap is True
    assert ctx.is_llm_initialized is True
    assert ctx.participants_changed() is False


@pytest.mark.asyncio
async def test_preprocessor_does_not_duplicate_current_trigger_in_history() -> None:
    ctx = ConformanceExecutionContext(
        history_messages=[
            *canonical_history(),
            {
                **canonical_history()[0],
                "id": CURRENT_MESSAGE_ID,
                "content": "current message should be excluded from history",
            },
        ]
    )

    agent_input = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(),
        agent_id=AGENT_ID,
    )

    assert CURRENT_MESSAGE_ID not in [
        message["metadata"]["source_message_id"] for message in agent_input.history.raw
    ]
    assert agent_input.msg.id == CURRENT_MESSAGE_ID


def test_platform_fixture_preserves_participants_peers_and_rehydration_state() -> None:
    participants = canonical_participants()
    peers = canonical_peers()
    pending = pending_work_state()
    tool_history = completed_tool_history()

    assert {participant["handle"] for participant in participants} == {
        "darvell",
        "darvell/test-agent",
        "darvell/calc",
    }
    assert peers[0]["handle"] == "darvell/greeter"
    assert pending["pending_message_id"] == "msg-offline-pending"
    assert "msg-history-002" in pending["processed_message_ids"]
    assert tool_history[0]["message_type"] == "tool_call"
    assert tool_history[1]["message_type"] == "tool_result"


@pytest.mark.asyncio
async def test_observed_message_ids_do_not_credit_one_occurrence_twice() -> None:
    history = [
        {
            **canonical_history()[0],
            "metadata": {"source_message_id": "duplicate-history-a"},
            "content": "DUPLICATE-CONTENT",
        },
        {
            **canonical_history()[1],
            "metadata": {"source_message_id": "duplicate-history-b"},
            "content": "DUPLICATE-CONTENT",
        },
    ]
    agent_input = await build_agent_input_through_preprocessor(
        ctx=ConformanceExecutionContext(history_messages=history),
    )

    assert _observed_message_ids(agent_input, ["DUPLICATE-CONTENT"]) == [
        "duplicate-history-a"
    ]


def test_conformance_schema_recorder_uses_runtime_tool_definitions() -> None:
    recorder = ConformanceSchemaRecorder(
        participants=canonical_participants(),
        peers=canonical_peers(),
    )

    openai_schemas = recorder.get_openai_tool_schemas(include_contacts=False)
    anthropic_schemas = recorder.get_anthropic_tool_schemas(include_contacts=False)
    expected_names = canonical_tool_names(include_contacts=False)

    assert openai_schemas
    assert anthropic_schemas
    assert schema_tool_names(openai_schemas, format="openai") == expected_names
    assert schema_tool_names(anthropic_schemas, format="anthropic") == expected_names
    assert set(CHAT_TOOL_NAMES).issubset(expected_names)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_probe_captures_real_adapter_surface_from_agent_input(
    adapter_id: str,
) -> None:
    agent_input = await build_agent_input_through_preprocessor()

    with pytest.raises(FrozenInstanceError):
        agent_input.room_id = "different-room"  # type: ignore[misc]

    captured = await capture_request(adapter_id, agent_input)

    assert_valid_captured_request(captured, adapter_id=adapter_id)
    assert captured.system_text is not None
    assert "Test Agent" in captured.system_text
    assert (
        "Treat messages from other participants as user input" in captured.system_text
        or "Plain text responses will NOT be delivered" in captured.system_text
    )
    assert "Custom conformance prompt." in captured.system_text
    text = visible_text(captured)
    assert "MARCO" in text
    assert "@darvell/test-agent" in text


def assert_valid_captured_request(
    captured: CapturedRequest,
    *,
    adapter_id: str,
) -> None:
    assert captured.adapter_id == adapter_id
    assert captured.message_ids[-1] == CURRENT_MESSAGE_ID
    assert "msg-history-001" in captured.message_ids
    assert captured.message_texts
    assert captured.tool_names
    assert "band_send_message" in captured.tool_names
    assert captured.seam_owner in {SeamOwner.ADAPTER_PAYLOAD, SeamOwner.ADAPTER_INPUT}
    assert captured.raw_summary
    assert captured.base_instruction_surface
    cell = applicability_for(
        adapter_id,
        SCENARIOS_BY_ID["L0.request.platform_context"],
    )
    assert cell.base_instruction_surface == captured.base_instruction_surface
