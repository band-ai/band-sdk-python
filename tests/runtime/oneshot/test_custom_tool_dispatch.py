"""OneShotInvoker: custom tool dispatch through a real adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from anthropic.types import ToolUseBlock
from band_rest import (
    CreateAgentChatMessageResponse,
    MessageSentResponse,
)

from band.adapters.anthropic import AnthropicAdapter
from tests.runtime.conftest import make_link_mock, platform_msg
from tests.runtime.oneshot.kit import VaultCodeInput, make_invoker, msg_body


class TestCustomToolDispatch:
    async def test_custom_tool_result_reaches_the_platform_reply(self) -> None:
        """The model calls a custom tool it needs to answer, then reports
        back via the real band_send_message platform tool.
        """
        looked_up: list[str] = []

        async def lookup_vault_code(args: VaultCodeInput) -> str:
            looked_up.append(args.topic)
            return "vault code is 4471-ECHO"

        link = make_link_mock(
            participants=[
                {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"},
            ],
            next_messages=[platform_msg("msg-1"), None],
        )
        link.rest.agent_api_messages.create_agent_chat_message = AsyncMock(
            return_value=CreateAgentChatMessageResponse(
                data=MessageSentResponse(id="sent-1", recipients=[], success=True)
            )
        )

        adapter = AnthropicAdapter(
            additional_tools=[(VaultCodeInput, lookup_vault_code)]
        )
        invoker = await make_invoker(link, adapter)

        turn_1 = MagicMock(stop_reason="tool_use")
        turn_1.content = [
            ToolUseBlock(
                type="tool_use", id="call-1", name="vaultcode", input={"topic": "vault"}
            )
        ]
        turn_2 = MagicMock(stop_reason="tool_use")
        turn_2.content = [
            ToolUseBlock(
                type="tool_use",
                id="call-2",
                name="band_send_message",
                input={
                    "content": "The vault code is 4471-ECHO",
                    "mentions": ["alice"],
                },
            )
        ]
        turn_3 = MagicMock(stop_reason="end_turn")
        turn_3.content = []

        with patch.object(
            adapter, "_call_anthropic", AsyncMock(side_effect=[turn_1, turn_2, turn_3])
        ):
            result = await invoker.handle_event(msg_body())

        assert result["status"] == "done"
        link.mark_processed.assert_awaited_once_with("room-1", "msg-1")
        link.mark_failed.assert_not_awaited()

        # Ran directly, not via AgentTools.execute_tool_call.
        assert looked_up == ["vault"]

        link.rest.agent_api_messages.create_agent_chat_message.assert_awaited_once()
        sent = link.rest.agent_api_messages.create_agent_chat_message.call_args.kwargs[
            "message"
        ]
        assert "4471-ECHO" in sent.content
        assert sent.mentions[0].id == "user-1"
