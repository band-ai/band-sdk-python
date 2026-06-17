"""L0 platform-adaptation baseline conformance rows."""

from __future__ import annotations

from typing import Any

import pytest

from tests.framework_conformance.baseline_applicability import (
    ApplicabilityStatus,
    build_applicability_matrix,
)
from tests.framework_conformance.baseline_scenarios import SCENARIOS_BY_ID
from tests.framework_conformance.baseline_status import ScenarioKind
from tests.framework_conformance.dispatch_capture import (
    HONEST_DISPATCH_ADAPTER_IDS,
    DispatchResult,
    assert_dispatch_result,
    dispatch_tool,
)
from tests.framework_conformance.platform_fixtures import (
    AGENT_ID,
    PEER_AGENT_ID,
    PEER_AGENT_HANDLE,
    ROOM_ID,
    SECOND_PEER_AGENT_HANDLE,
    ConformanceExecutionContext,
    apply_participant_event,
    build_agent_input_through_preprocessor,
    canonical_history,
    canonical_participants,
    canonical_peers,
    current_message_event,
    participant_added_event,
    participant_removed_event,
)
from tests.framework_conformance.request_capture import (
    REQUEST_CAPTURE_ADAPTER_IDS,
    ConformanceSchemaRecorder,
    capture_request,
    visible_text as captured_visible_text,
)

_L0_REQUEST_ROWS = {
    "L0.request.platform_context",
    "L0.request.history",
    "L0.request.participants",
}

_CHAT_TOOL_ARGS: dict[str, dict[str, Any]] = {
    "thenvoi_send_message": {
        "content": "L0 dispatch sentinel",
        "mentions": ["@darvell"],
    },
    "thenvoi_add_participant": {"identifier": "darvell/greeter", "role": "member"},
    "thenvoi_remove_participant": {"identifier": "darvell/greeter"},
    "thenvoi_get_participants": {},
    "thenvoi_lookup_peers": {"page": 1, "page_size": 10},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l0_platform_context_reaches_model_visible_surface(
    adapter_id: str,
) -> None:
    agent_input = await build_agent_input_through_preprocessor()

    captured = await capture_request(adapter_id, agent_input)
    visible_text = captured_visible_text(captured)

    assert captured.system_text is not None
    assert "Test Agent" in captured.system_text
    assert "conformance test agent" in captured.system_text
    assert (
        "Treat messages from other participants as user input" in visible_text
        or "Plain text responses will NOT be delivered" in visible_text
    )
    assert captured.base_instruction_surface is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l0_history_and_current_trigger_are_present_without_duplication(
    adapter_id: str,
) -> None:
    agent_input = await build_agent_input_through_preprocessor()
    captured = await capture_request(adapter_id, agent_input)

    assert captured.message_ids == [
        "msg-history-001",
        "msg-history-002",
        "msg-history-003",
        "msg-current-trigger",
    ]
    assert captured.message_ids.count("msg-current-trigger") == 1
    visible_text = captured_visible_text(captured)
    assert visible_text.count("MARCO") == 1
    assert visible_text.count("LIGHTHOUSE") == 1
    assert visible_text.count("POSTGRESQL") == 1
    assert visible_text.count("@darvell/test-agent please recall the room facts") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l0_participant_add_update_roster_reaches_adapter_surface(
    adapter_id: str,
) -> None:
    ctx = ConformanceExecutionContext(history_messages=canonical_history())
    ctx.mark_participants_sent()
    assert canonical_peers()[0]["handle"] == SECOND_PEER_AGENT_HANDLE
    assert all(
        participant.get("handle") != SECOND_PEER_AGENT_HANDLE
        for participant in ctx.participants
    )

    apply_participant_event(ctx, participant_added_event())
    agent_input = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(message_id="msg-participant-added"),
        agent_id=AGENT_ID,
    )

    captured = await capture_request(adapter_id, agent_input)
    visible_text = captured_visible_text(captured)

    assert "## Current Participants" in visible_text
    assert "@darvell" in visible_text
    assert "@darvell/test-agent" in visible_text
    assert "@darvell/calc" in visible_text
    assert "@darvell/greeter" in visible_text
    assert "(User)" in visible_text
    assert "(Agent)" in visible_text


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l0_participant_remove_update_roster_reaches_adapter_surface(
    adapter_id: str,
) -> None:
    ctx = ConformanceExecutionContext(history_messages=canonical_history())
    ctx.mark_participants_sent()
    assert any(
        participant.get("id") == PEER_AGENT_ID for participant in ctx.participants
    )

    apply_participant_event(
        ctx, participant_removed_event(participant_id=PEER_AGENT_ID)
    )
    agent_input = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(message_id="msg-participant-removed"),
        agent_id=AGENT_ID,
    )

    captured = await capture_request(adapter_id, agent_input)
    visible_text = captured_visible_text(captured)

    assert "## Current Participants" in visible_text
    assert "@darvell" in visible_text
    assert "@darvell/test-agent" in visible_text
    assert f"@{PEER_AGENT_HANDLE}" not in visible_text


@pytest.mark.asyncio
async def test_l0_participant_change_steady_state_suppression() -> None:
    ctx = ConformanceExecutionContext(history_messages=canonical_history())

    first = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(message_id="msg-participant-first"),
        agent_id=AGENT_ID,
    )
    second = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(message_id="msg-participant-second"),
        agent_id=AGENT_ID,
    )

    assert first.participants_msg is not None
    assert second.participants_msg is None


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", HONEST_DISPATCH_ADAPTER_IDS)
@pytest.mark.parametrize("tool_name,arguments", _CHAT_TOOL_ARGS.items())
async def test_l0_chat_tool_dispatch_reaches_real_adapter_path(
    adapter_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    tools = ConformanceSchemaRecorder(
        participants=canonical_participants(),
        peers=canonical_peers(),
        room_id=ROOM_ID,
    )

    result = await dispatch_tool(
        adapter_id,
        tool_name=tool_name,
        arguments=arguments,
        tools=tools,
    )

    assert_dispatch_result(result)


def test_l0_dispatch_oracle_rejects_corrupted_tool_arguments() -> None:
    result = DispatchResult(
        adapter_id="anthropic",
        tool_name="thenvoi_send_message",
        arguments={"content": "expected", "mentions": ["@darvell"]},
        tool_calls=[],
        messages_sent=[
            {"id": "msg-0", "content": "corrupted", "mentions": ["@darvell"]}
        ],
        participants_added=[],
        participants_removed=[],
        context_calls=[],
    )

    with pytest.raises(AssertionError):
        assert_dispatch_result(result)


def test_l0_scorecard_has_reviewed_request_and_dispatch_cells() -> None:
    l0_ids = {
        scenario.id
        for scenario in SCENARIOS_BY_ID.values()
        if scenario.level.value == "l0"
    }
    l0_cells = [
        cell for cell in build_applicability_matrix() if cell.scenario_id in l0_ids
    ]

    assert l0_cells
    assert all(
        cell.status is not ApplicabilityStatus.UNKNOWN_FAIL_CLOSED for cell in l0_cells
    )
    dispatch_cells = [
        cell
        for cell in l0_cells
        if SCENARIOS_BY_ID[cell.scenario_id].kind is ScenarioKind.DISPATCH
    ]
    runtime_owned_dispatch = [
        cell
        for cell in dispatch_cells
        if cell.status is ApplicabilityStatus.TIER2_BLOCKED
    ]
    assert runtime_owned_dispatch
    assert all(
        cell.reason and not cell.tier2_pointer for cell in runtime_owned_dispatch
    )


def test_l0_request_rows_are_registered_as_core_contract() -> None:
    for row_id in _L0_REQUEST_ROWS:
        scenario = SCENARIOS_BY_ID[row_id]
        assert scenario.core_contract is True
        assert scenario.requires_request_capture is True
