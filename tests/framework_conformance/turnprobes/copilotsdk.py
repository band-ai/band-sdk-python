"""Turn-outcome probes for the copilotsdk adapter(s); see ``turnprobes``."""

from __future__ import annotations

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    FakeCopilotSession,
    make_started_adapter,
)
from tests.framework_conformance.turnprobes import (
    ROOM_ID,
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
)


async def run_copilot_sdk_turn(script: TurnScript, tools: FakeAgentTools) -> None:
    from copilot import (  # noqa: PLC0415 -- copilot_sdk extra, not in every lane's venv
        ToolInvocation,
    )

    async def perform_tool_calls(session: FakeCopilotSession) -> None:
        # The fake tools advertise no schemas, so the scripted model calls the
        # adapter's own bridged-tool handler, which every Band tool shares.
        handler = adapter._make_tool_handler(ROOM_ID)
        for index, call in enumerate(script.tool_calls):
            await handler(
                ToolInvocation(
                    tool_call_id=f"call-{index}",
                    tool_name=call.name,
                    arguments=call.arguments,
                )
            )

    client = FakeCopilotClient(
        reply_content=script.final_text or None, turn_events=[perform_tool_calls]
    )
    adapter = await make_started_adapter(client)
    await adapter.on_event(turn_input(tools))


PROBES: dict[str, TurnOutcomeProbe] = (
    {"copilot_sdk": TurnOutcomeProbe(run=run_copilot_sdk_turn)}
    if _COPILOT_SDK_AVAILABLE
    else {}
)
