"""Adapter runners for the delivery matrix — thin wraps of existing fakes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from unittest.mock import patch

from band.core.turn.observing import ObservingTools
from band.runtime.tools import BAND_SEND_MESSAGE

from tests.baseline.adapter import Adapter
from tests.baseline.delivery.outcome import (
    TurnOutcome,
)
from tests.baseline.delivery.checks import outcome_from_observing
from tests.baseline.delivery.scenarios import (
    ACP_CLIENT,
    DIRECT_POST_TEXT,
    TOOL_POST_TEXT,
    DeliveryScenario,
    InProcessAction,
)
from tests.baseline.delivery.tools import (
    ROOM_ID,
    BandSendMessageArgs,
    VIA_TOOL,
    tools_for_scenario,
)

DeliveryRunner = Callable[[DeliveryScenario], Awaitable[TurnOutcome]]


# --- Copilot SDK -----------------------------------------------------------------


async def run_copilot(scenario: DeliveryScenario) -> TurnOutcome:
    from band.adapters.copilot_sdk import (
        CopilotSDKAdapterConfig,
        _COPILOT_SDK_AVAILABLE,
    )
    from band.converters.copilot_sdk import CopilotSDKSessionState
    from band.integrations.copilot_sdk import ASK_USER_ROOM
    from tests.adapters.copilot_sdk.fakes import (
        FakeCopilotClient,
        FakeCopilotSession,
        ToolSchemaFakeTools,
        make_platform_message,
        make_started_adapter,
    )
    from tests.adapters.copilot_sdk.test_ask_user_room import ask_mid_turn

    if not _COPILOT_SDK_AVAILABLE:
        raise RuntimeError("copilot_sdk extra required")

    from copilot import ToolInvocation

    tools = tools_for_scenario(scenario, ToolSchemaFakeTools)

    async def model_posts(session: FakeCopilotSession) -> None:
        await session.find_tool(BAND_SEND_MESSAGE).handler(
            ToolInvocation(
                tool_call_id="call-1",
                tool_name=BAND_SEND_MESSAGE,
                arguments=BandSendMessageArgs(
                    content=VIA_TOOL.content, mentions=("user-1",)
                ).to_arguments(),
            )
        )

    async def model_lists(session: FakeCopilotSession) -> None:
        await session.find_tool("band_get_participants").handler(
            ToolInvocation(
                tool_call_id="call-1",
                tool_name="band_get_participants",
                arguments={},
            )
        )

    match scenario.action:
        case InProcessAction.SEND_MESSAGE_OK:
            from copilot import UserInputRequest

            answers: list[dict[str, Any]] = []
            question: UserInputRequest = {
                "question": DIRECT_POST_TEXT,
                "choices": [],
                "allowFreeform": True,
            }
            client = FakeCopilotClient(
                reply_content=scenario.agent_text,
                turn_events=[ask_mid_turn(question, answers)],
            )
            adapter = await make_started_adapter(
                client, CopilotSDKAdapterConfig(ask_user=ASK_USER_ROOM)
            )
        case InProcessAction.POST_OK | InProcessAction.POST_FAIL:
            client = FakeCopilotClient(
                reply_content=scenario.agent_text, turn_events=[model_posts]
            )
            adapter = await make_started_adapter(client)
        case InProcessAction.NON_POSTING_TOOL:
            client = FakeCopilotClient(
                reply_content=scenario.agent_text, turn_events=[model_lists]
            )
            adapter = await make_started_adapter(client)
        case InProcessAction.NO_POST:
            client = FakeCopilotClient(
                reply_content=scenario.agent_text, turn_events=[]
            )
            adapter = await make_started_adapter(client)

    observing = ObservingTools(_inner=tools)
    await adapter.on_message(
        msg=make_platform_message(room_id=ROOM_ID, content="hello"),
        tools=observing,
        history=CopilotSDKSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=ROOM_ID,
    )
    return outcome_from_observing(observing, tools)


# --- Codex -----------------------------------------------------------------------


async def run_codex(scenario: DeliveryScenario) -> TurnOutcome:
    from band.adapters.codex import CodexAdapter, CodexAdapterConfig
    from band.integrations.codex.types import CodexSessionState
    from tests.adapters.test_codex_adapter import (
        FakeCodexClient,
        ToolSchemaFakeTools,
        _event_notification,
        _event_request,
        make_platform_message,
    )

    match scenario.action:
        case InProcessAction.POST_OK | InProcessAction.POST_FAIL:
            tool_events = [
                _event_request(
                    42,
                    "item/tool/call",
                    {
                        "tool": BAND_SEND_MESSAGE,
                        "arguments": VIA_TOOL.to_arguments(),
                        "callId": "call-42",
                    },
                )
            ]
        case InProcessAction.NON_POSTING_TOOL:
            tool_events = [
                _event_request(
                    42,
                    "item/tool/call",
                    {
                        "tool": "band_lookup_peers",
                        "arguments": {"page": 1, "page_size": 10},
                        "callId": "call-42",
                    },
                )
            ]
        case _:
            tool_events = []

    events: list[Any] = [*tool_events]
    events.extend(
        [
            _event_notification(
                "item/completed",
                {"item": {"type": "agentMessage", "text": scenario.agent_text}},
            ),
            _event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    }
                },
            ),
        ]
    )

    tools = tools_for_scenario(scenario, ToolSchemaFakeTools, fail_error="send failed")
    observing = ObservingTools(_inner=tools)
    adapter = CodexAdapter(
        config=CodexAdapterConfig(transport="ws", fallback_send_agent_text=True),
        client_factory=lambda _config: FakeCodexClient(events=events),
    )
    await adapter.on_started("Codex Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(),
        observing,
        CodexSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=ROOM_ID,
    )
    return outcome_from_observing(observing, tools)


# --- OpenCode --------------------------------------------------------------------


async def run_opencode(scenario: DeliveryScenario) -> TurnOutcome:
    from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
    from tests.adapters.opencode.helpers import (
        FakeMCPBackend,
        FakeOpencodeClient,
        event_message_updated,
        event_session_idle,
        event_text_part,
        event_tool_part,
        make_fake_mcp_backend_factory,
        run_single_turn,
    )

    # The fake serve runs the band tool the scripted part reports, so only a
    # real room write can mint a receipt -- an SSE status alone cannot.
    mcp = FakeMCPBackend()

    match scenario.action:
        case InProcessAction.POST_OK | InProcessAction.POST_FAIL:
            posted_ok = scenario.action is InProcessAction.POST_OK
            tool_parts: list[Any] = [
                event_tool_part(
                    "sess-1",
                    "msg-1",
                    tool=BAND_SEND_MESSAGE,
                    call_id="c1",
                    status="completed" if posted_ok else "error",
                    input_data=VIA_TOOL.to_arguments(),
                )
            ]
        case InProcessAction.NON_POSTING_TOOL:
            tool_parts = [
                event_tool_part(
                    "sess-1",
                    "msg-1",
                    tool="bash",
                    call_id="c1",
                    status="completed",
                    input_data={"command": "ls"},
                )
            ]
        case _:
            tool_parts = []

    parts: list[Any] = [
        event_message_updated("sess-1", "msg-1"),
        event_text_part("sess-1", "msg-1", scenario.agent_text),
        *tool_parts,
        event_session_idle("sess-1"),
    ]

    fake_client = FakeOpencodeClient(prompt_event_sequences=[parts], mcp=mcp)
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(),
        client_factory=lambda _: fake_client,
    )
    tools = tools_for_scenario(scenario)
    observing = ObservingTools(_inner=tools)
    with patch(
        "band.adapters.opencode.adapter.create_band_mcp_backend",
        make_fake_mcp_backend_factory(mcp),
    ):
        await run_single_turn(adapter, observing)

    return outcome_from_observing(observing, tools)


# --- ACP -------------------------------------------------------------------------


async def run_acp(scenario: DeliveryScenario) -> TurnOutcome:
    from tests.integrations.acp.acp_toolkit import FakeACPAgent, acp_adapter

    agent = FakeACPAgent()
    match scenario.action:
        case InProcessAction.POST_OK:
            agent.will_say(scenario.agent_text).will_call_band_tool(
                "tc-1", content=TOOL_POST_TEXT
            )
        case InProcessAction.POST_FAIL:
            agent.will_call_band_tool(
                "tc-1", content=TOOL_POST_TEXT, status="failed"
            ).will_say(scenario.agent_text)
        case InProcessAction.NON_POSTING_TOOL:
            agent.will_call_tool("tc-1", "get_weather", result="72F").will_say(
                scenario.agent_text
            )
        case _:
            agent.will_say(scenario.agent_text)

    async with acp_adapter(agent) as session:
        reply = await session.send("question?", room=ROOM_ID)

    return TurnOutcome(
        texts=tuple(reply.texts),
        receipt_tool=reply.receipt.tool_name if reply.receipt else None,
    )


RUNNERS: Mapping[str, DeliveryRunner] = {
    Adapter.COPILOT_SDK: run_copilot,
    Adapter.CODEX: run_codex,
    Adapter.OPENCODE: run_opencode,
    ACP_CLIENT: run_acp,
}


async def run_delivery(adapter: str, scenario: DeliveryScenario) -> TurnOutcome:
    return await RUNNERS[adapter](scenario)
