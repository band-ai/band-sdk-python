"""Turn-outcome probes for the claude_sdk adapter; see ``turnprobes``.

The scripted Claude CLI (``tests/adapters/claude_sdk/fakecli.py``) makes each
Band tool call through the adapter's real in-process MCP server.
"""

from __future__ import annotations

from unittest.mock import patch

from band.runtime.tools import MCP_TOOL_PREFIX
from band.testing.fake_tools import FakeAgentTools
from tests.baseline.decisions import ModelDecision
from tests.framework_conformance.turnprobes import (
    ROOM_ID,
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
    user_message,
)


def scripted_turn(script: TurnScript) -> list[ModelDecision]:
    """The CLI turn following ``script``: each Band call, then the final text."""
    decisions = [
        ModelDecision.call(
            f"{MCP_TOOL_PREFIX}{call.name}", chat_id=ROOM_ID, **call.arguments
        )
        for call in script.tool_calls
    ]
    if script.final_text:
        decisions.append(ModelDecision.text_reply(script.final_text))
    return decisions


async def run_claude_sdk(script: TurnScript, tools: FakeAgentTools) -> None:
    from band.adapters.claude_sdk import (  # noqa: PLC0415 -- claude_sdk extra, absent from the standard dev-crewai/dev-parlant lane venvs
        ClaudeSDKAdapter,
    )
    from tests.adapters.claude_sdk.fakecli import (  # noqa: PLC0415 -- claude_sdk extra, absent from the standard dev-crewai/dev-parlant lane venvs
        FakeClaude,
    )

    claude = FakeClaude()
    claude.script(scripted_turn(script))
    adapter = ClaudeSDKAdapter()
    with patch(
        "band.integrations.claude_sdk.session_manager.ClaudeSDKClient", claude.client
    ):
        await adapter.on_started("Agent", "An agent under test")
        try:
            await adapter.on_event(turn_input(tools))
        finally:
            await adapter.cleanup_all()
    claude.assert_done()


async def ask_claude_sdk_status(tools: FakeAgentTools) -> None:
    from band.adapters.claude_sdk import (  # noqa: PLC0415 -- claude_sdk extra, absent from the standard dev-crewai/dev-parlant lane venvs
        ClaudeApprovalOptions,
        ClaudeSDKAdapter,
        ClaudeSDKAdapterConfig,
        ClaudeSDKCommand,
    )

    adapter = ClaudeSDKAdapter(
        ClaudeSDKAdapterConfig(approvals=ClaudeApprovalOptions(mode="manual"))
    )
    await adapter.on_started("Agent", "An agent under test")
    try:
        await adapter.on_event(
            turn_input(tools, user_message(f"/{ClaudeSDKCommand.STATUS}"))
        )
    finally:
        await adapter.cleanup_all()


PROBES: dict[str, TurnOutcomeProbe] = {
    "claude_sdk": TurnOutcomeProbe(run=run_claude_sdk, settle=ask_claude_sdk_status),
}
