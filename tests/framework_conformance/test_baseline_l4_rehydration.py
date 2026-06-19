"""L4 rehydration baseline conformance rows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from band.core.types import AgentInput
from band.runtime.execution import ExecutionContext
from band.runtime.types import SessionConfig

from tests.framework_conformance.baseline_applicability import (
    ApplicabilityStatus,
    build_applicability_matrix,
)
from tests.framework_conformance.baseline_scenarios import SCENARIOS_BY_ID
from tests.framework_conformance.baseline_status import BaselineContract
from tests.framework_conformance.platform_fixtures import (
    AGENT_ID,
    ROOM_ID,
    USER_ID,
    ConformanceExecutionContext,
    build_agent_input_through_preprocessor,
    canonical_history,
    canonical_participants,
    completed_tool_history,
    current_message_event,
    history_message,
)
from tests.framework_conformance.request_capture import (
    REQUEST_CAPTURE_ADAPTER_IDS,
    ConformanceSchemaRecorder,
    CapturedRequest,
    CapturedRequestItem,
    RehydrationRequestState,
    RequestItemPurpose,
    capture_request,
    visible_text,
)

_PENDING_L4_ID = "msg-l4-offline-pending"
_CURRENT_L4_ID = "msg-l4-current-trigger"


class _L4ReplayGuardInput(BaseModel):
    """A custom side-effect tool that must not be replayed from history."""

    code: str


@dataclass(frozen=True)
class _ScriptedResponse:
    stop_reason: str
    content: list[Any]


def _rehydration_state(captured: CapturedRequest) -> RehydrationRequestState:
    assert captured.supports_rehydration_state
    assert captured.rehydration is not None
    return captured.rehydration


def _assert_no_current_tool_replay(captured: CapturedRequest) -> None:
    state = _rehydration_state(captured)
    assert state.completed_tool_call_ids == ("tool-call-001",)
    assert state.pending_tool_call_ids == ()
    assert all(
        item.tool_call_id != "tool-call-001"
        for item in captured.items
        if item.purpose is RequestItemPurpose.CURRENT_WORK
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l4_cold_start_rebuilds_persisted_history_in_canonical_order(
    adapter_id: str,
) -> None:
    history = [*canonical_history(), *completed_tool_history()]
    ctx = ConformanceExecutionContext(history_messages=history)
    agent_input = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(
            content="@darvell/test-agent continue from cold start.",
            message_id=_CURRENT_L4_ID,
        ),
    )

    captured = await capture_request(adapter_id, agent_input)
    expected_history_ids = (
        "msg-history-001",
        "msg-history-002",
        "msg-history-003",
        "msg-tool-call-001",
        "msg-tool-result-001",
    )
    state = _rehydration_state(captured)

    assert state.history_message_ids == expected_history_ids
    assert state.current_work_message_ids == (_CURRENT_L4_ID,)
    assert captured.message_ids == [*expected_history_ids, _CURRENT_L4_ID]
    text = visible_text(captured)
    assert "MARCO" in text
    assert "LIGHTHOUSE" in text
    assert "POSTGRESQL" in text
    assert "band_send_message" in text
    assert "tool-call-001" in text
    assert "msg-sent" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l4_offline_pending_message_is_current_work_not_inert_history(
    adapter_id: str,
) -> None:
    pending_context_message = history_message(
        message_id=_PENDING_L4_ID,
        content="OFFLINE-L4-PENDING should execute once as current work.",
        sender_id="22222222-2222-4222-8222-222222222222",
        sender_type="User",
        sender_name="Darvell",
        offset_seconds=4,
        metadata={"mentions": [], "source_message_id": _PENDING_L4_ID},
    )
    ctx = ConformanceExecutionContext(
        history_messages=[*canonical_history(), pending_context_message]
    )
    agent_input = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(
            content="OFFLINE-L4-PENDING should execute once as current work.",
            message_id=_PENDING_L4_ID,
        ),
    )

    captured = await capture_request(adapter_id, agent_input)
    state = _rehydration_state(captured)

    assert state.current_work_message_ids == (_PENDING_L4_ID,)
    assert _PENDING_L4_ID not in state.history_message_ids
    assert state.source_message_counts[_PENDING_L4_ID] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l4_handled_message_is_history_not_current_work(adapter_id: str) -> None:
    handled_id = "msg-l4-handled-user"
    pending_id = "msg-l4-unhandled-pending"
    handled_message = history_message(
        message_id=handled_id,
        content="HANDLED-L4 should remain historical only.",
        sender_id=USER_ID,
        sender_type="User",
        sender_name="Darvell",
        offset_seconds=4,
        metadata={"mentions": [], "source_message_id": handled_id},
    )
    handled_reply = history_message(
        message_id="msg-l4-handled-reply",
        content="HANDLED-L4 already answered.",
        sender_id=AGENT_ID,
        sender_type="Agent",
        sender_name="Test Agent",
        offset_seconds=5,
        metadata={"mentions": [], "source_message_id": handled_id},
    )
    ctx = ConformanceExecutionContext(
        history_messages=[*canonical_history(), handled_message, handled_reply]
    )
    agent_input = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(
            content="UNHANDLED-L4 should be the only current work.",
            message_id=pending_id,
        ),
    )

    captured = await capture_request(adapter_id, agent_input)
    state = _rehydration_state(captured)

    assert state.current_work_message_ids == (pending_id,)
    assert handled_id in state.history_message_ids
    assert handled_id not in state.current_work_message_ids
    assert state.source_message_counts[handled_id] == 2
    assert "HANDLED-L4 should remain historical only." in visible_text(captured)
    assert "HANDLED-L4 already answered." in visible_text(captured)


@pytest.mark.asyncio
async def test_l4_processed_replay_message_does_not_reach_adapter_handler() -> None:
    handled_id = "msg-l4-processed-replay"
    handled_context_message = history_message(
        message_id=handled_id,
        content="@darvell/test-agent PROCESSED-L4 must not reopen.",
        sender_id=USER_ID,
        sender_type="User",
        sender_name="Darvell",
        offset_seconds=4,
        metadata={
            "mentions": [],
            "source_message_id": handled_id,
            "delivery_status": {AGENT_ID: {"status": "processed"}},
        },
    )
    handled_reply = history_message(
        message_id="msg-l4-processed-reply",
        content="PROCESSED-L4 was already answered.",
        sender_id=AGENT_ID,
        sender_type="Agent",
        sender_name="Test Agent",
        offset_seconds=5,
        metadata={"mentions": [], "source_message_id": handled_id},
    )
    context_items = []
    for message in (handled_context_message, handled_reply):
        item = MagicMock()
        item.id = message["id"]
        item.content = message["content"]
        item.sender_id = message["sender_id"]
        item.sender_type = message["sender_type"]
        item.sender_name = message["sender_name"]
        item.message_type = message["message_type"]
        item.metadata = message["metadata"]
        item.inserted_at = message["inserted_at"]
        context_items.append(item)

    handler = AsyncMock()
    link = MagicMock()
    link.mark_processing = AsyncMock(return_value=True)
    link.mark_processed = AsyncMock(return_value=True)
    link.mark_failed = AsyncMock(return_value=True)
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=context_items)
    )
    ctx = ExecutionContext(
        room_id=ROOM_ID,
        link=link,
        on_execute=handler,
        config=SessionConfig(enable_context_hydration=True),
        agent_id=AGENT_ID,
    )
    replay_event = current_message_event(
        content="@darvell/test-agent PROCESSED-L4 must not reopen.",
        message_id=handled_id,
    )

    processed = await ctx._process_event(replay_event)

    assert processed is True
    handler.assert_not_called()
    link.mark_processing.assert_not_called()
    link.mark_processed.assert_not_called()
    link.mark_failed.assert_not_called()
    assert handled_id in ctx._processed_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l4_completed_tool_history_is_inert_for_request_capture_adapters(
    adapter_id: str,
) -> None:
    tools = ConformanceSchemaRecorder(
        participants=canonical_participants(),
        room_id=ROOM_ID,
    )
    ctx = ConformanceExecutionContext(
        history_messages=[*canonical_history(), *completed_tool_history()]
    )
    agent_input = await build_agent_input_through_preprocessor(ctx=ctx)
    agent_input = AgentInput(
        msg=agent_input.msg,
        tools=tools,
        history=agent_input.history,
        participants_msg=agent_input.participants_msg,
        contacts_msg=agent_input.contacts_msg,
        is_session_bootstrap=agent_input.is_session_bootstrap,
        room_id=agent_input.room_id,
    )

    captured = await capture_request(adapter_id, agent_input)
    text = visible_text(captured)

    state = _rehydration_state(captured)

    assert "msg-tool-call-001" in state.history_message_ids
    assert "msg-tool-result-001" in state.history_message_ids
    _assert_no_current_tool_replay(captured)
    assert "tool-call-001" in text
    assert "msg-sent" in text
    assert tools.tool_calls == []
    assert tools.messages_sent == []


@pytest.mark.asyncio
async def test_l4_tool_replay_oracle_rejects_completed_tool_as_current_work() -> None:
    captured = await capture_request(
        REQUEST_CAPTURE_ADAPTER_IDS[0],
        await build_agent_input_through_preprocessor(
            ctx=ConformanceExecutionContext(
                history_messages=[*canonical_history(), *completed_tool_history()]
            )
        ),
    )
    replay_item = CapturedRequestItem(
        surface="test",
        index=999,
        role="assistant",
        text="replayed completed tool call",
        purpose=RequestItemPurpose.CURRENT_WORK,
        tool_call_id="tool-call-001",
    )
    mutated = replace(captured, items=(*captured.items, replay_item))

    with pytest.raises(AssertionError):
        _assert_no_current_tool_replay(mutated)


@pytest.mark.asyncio
async def test_l4_tool_replay_oracle_rejects_pending_completed_tool_split() -> None:
    captured = await capture_request(
        REQUEST_CAPTURE_ADAPTER_IDS[0],
        await build_agent_input_through_preprocessor(
            ctx=ConformanceExecutionContext(
                history_messages=[*canonical_history(), *completed_tool_history()]
            )
        ),
    )
    state = _rehydration_state(captured)
    mutated_state = replace(state, pending_tool_call_ids=("tool-call-001",))
    mutated = replace(captured, rehydration=mutated_state)

    with pytest.raises(AssertionError):
        _assert_no_current_tool_replay(mutated)


@pytest.mark.asyncio
async def test_l4_completed_tool_history_does_not_replay_side_effects() -> None:
    from anthropic.types import TextBlock

    from band.adapters.anthropic import AnthropicAdapter

    class _NoReplayAnthropicAdapter(AnthropicAdapter):
        def __init__(self, *, calls: list[_L4ReplayGuardInput]) -> None:
            async def l4_replay_guard(args: _L4ReplayGuardInput) -> dict[str, str]:
                calls.append(args)
                return {"replayed": args.code}

            super().__init__(
                provider_key="test-provider-key",
                additional_tools=[(_L4ReplayGuardInput, l4_replay_guard)],
            )
            self._responses = [
                _ScriptedResponse(
                    stop_reason="end_turn",
                    content=[TextBlock(text="done", type="text")],
                )
            ]

        async def _call_anthropic(
            self,
            messages: list[dict[str, Any]],
            tools: list[Any],
        ) -> Any:
            del messages, tools
            return self._responses.pop(0)

    calls: list[_L4ReplayGuardInput] = []
    tools = ConformanceSchemaRecorder(
        participants=canonical_participants(),
        room_id=ROOM_ID,
    )
    ctx = ConformanceExecutionContext(
        history_messages=[*canonical_history(), *completed_tool_history()]
    )
    agent_input = await build_agent_input_through_preprocessor(ctx=ctx)
    agent_input = AgentInput(
        msg=agent_input.msg,
        tools=tools,
        history=agent_input.history,
        participants_msg=agent_input.participants_msg,
        contacts_msg=agent_input.contacts_msg,
        is_session_bootstrap=agent_input.is_session_bootstrap,
        room_id=agent_input.room_id,
    )
    adapter = _NoReplayAnthropicAdapter(calls=calls)

    await adapter.on_started("Test Agent", "A conformance test agent")
    await adapter.on_event(agent_input)

    assert calls == []
    assert tools.tool_calls == []


def test_l4_rehydration_rows_are_request_visible_except_cleanup_hardening() -> None:
    request_rows = {
        "L4.request.offline_pending_once",
        "L4.request.handled_message_dedup",
        "L4.request.completed_tool_no_requeue",
    }
    for row_id in request_rows:
        scenario = SCENARIOS_BY_ID[row_id]
        assert scenario.core_contract is True
        assert scenario.domain_contract is BaselineContract.L4_REHYDRATION
        assert scenario.requires_request_capture is True

    cleanup = SCENARIOS_BY_ID["L4.runtime.cleanup_not_required_for_crash_correctness"]
    assert cleanup.core_contract is False

    cells = [
        cell
        for cell in build_applicability_matrix()
        if cell.scenario_id in {*request_rows, cleanup.id}
    ]
    assert cells
    covered = [
        cell for cell in cells if cell.status is ApplicabilityStatus.COVERED_BY_EXISTING
    ]
    excluded = [
        cell for cell in cells if cell.status is ApplicabilityStatus.EXCLUDED_BRIDGE
    ]
    blocked = [
        cell for cell in cells if cell.status is ApplicabilityStatus.TIER2_BLOCKED
    ]
    applicable = [
        cell for cell in cells if cell.status is ApplicabilityStatus.APPLICABLE
    ]
    assert covered
    assert excluded
    assert blocked
    assert len(covered) + len(excluded) + len(blocked) + len(applicable) == len(cells)
    assert all(cell.covered_by_existing is not None for cell in covered)
    assert all(cell.coverage_evidence for cell in covered)

    request_cells = [cell for cell in cells if cell.scenario_id in request_rows]
    assert {
        cell.adapter_id
        for cell in request_cells
        if cell.status is not ApplicabilityStatus.APPLICABLE
    } == {
        "crewai_flow",
        "a2a",
        "a2a_gateway",
        "acp",
        "slack",
    }
