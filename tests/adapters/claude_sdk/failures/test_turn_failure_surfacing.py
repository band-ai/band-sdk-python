from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk._errors import CLIConnectionError
from claude_agent_sdk.types import PermissionResultDeny, ToolPermissionContext
from pydantic import BaseModel

from band.adapters.claude_sdk import ClaudeSDKAdapter, TurnResultAlreadyReported
from band.core.types import Emit, ToolEventKey
from tests.adapters.claude_sdk.helpers import (
    ANY_MODEL,
    MISSING_REPLY_TEXT,
    SEND_MESSAGE_MCP_NAME,
    denial,
    error_events,
    narrated_message_types,
    reply_to_approval,
    result_message,
    tool_result_payload,
    tool_turn,
)


class TestTurnFailureSurfacing:
    """A failed or silent turn must surface a room-visible error."""

    def test_declined_the_reply_ignores_malformed_denial_entries(self):
        """``permission_denials`` is typed ``list[Any]`` — raw, unvalidated
        CLI JSON, not a structure this SDK guarantees the shape of. A
        malformed entry must not crash turn-completion, just fail to match."""
        adapter = ClaudeSDKAdapter()
        assert (
            adapter._declined_the_reply(["not-a-dict", 42, None], {"tool-1"}) is False
        )

    @staticmethod
    def _client_yielding(*sdk_messages) -> MagicMock:
        mock_client = MagicMock()

        async def mock_receive():
            for message in sdk_messages:
                yield message

        mock_client.receive_response = mock_receive
        return mock_client

    @pytest.mark.asyncio
    async def test_reports_error_on_is_error_result(self, mock_tools):
        """``is_error`` must surface even though ``subtype`` claims success.

        On a hard failure such as a CLI auth error, the CLI reports
        ``is_error=True`` with ``subtype="success"``, so the adapter must gate
        on ``is_error`` and never on ``subtype``.
        """
        adapter = ClaudeSDKAdapter()
        result_msg = result_message(
            is_error=True,
            result="Not logged in · Please run /login",
            api_error_status=None,
        )
        mock_client = self._client_yielding(result_msg)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert "Not logged in · Please run /login" in errors[0]
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "claude_sdk"
        # No structured api_error_status on this failure -- code stays unset
        # rather than inventing one.
        assert failure.code is None

    @pytest.mark.asyncio
    async def test_error_detail_includes_api_error_status(self, mock_tools):
        """The HTTP status on a failed API call is surfaced alongside ``result``."""
        adapter = ClaudeSDKAdapter()
        result_msg = result_message(
            is_error=True,
            result="Failed to authenticate. API Error: 401",
            api_error_status=401,
            errors=["authentication_error: invalid API key"],
        )
        mock_client = self._client_yielding(result_msg)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert "401" in errors[0]
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.code == "401"
        assert failure.detail == ["authentication_error: invalid API key"]

    @pytest.mark.asyncio
    async def test_reports_missing_reply_when_no_terminal_tool_ran(self, mock_tools):
        """A clean turn that never called a Band tool must not go silent."""
        adapter = ClaudeSDKAdapter()
        result_msg = result_message(is_error=False)
        mock_client = self._client_yielding(result_msg)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_no_error_reported_when_reply_tool_ran(self, mock_tools):
        """A turn that replied via band_send_message must stay quiet."""
        adapter = ClaudeSDKAdapter()
        turn = tool_turn(SEND_MESSAGE_MCP_NAME)
        result_msg = result_message(is_error=False)
        mock_client = self._client_yielding(*turn, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_assistant_carried_tool_result_also_counts(self, mock_tools):
        """A tool result arriving inside an assistant message (accepted
        defensively alongside the protocol's user-envelope shape) still counts
        as the turn's reply."""
        adapter = ClaudeSDKAdapter()
        assistant_msg = AssistantMessage(
            content=[
                ToolUseBlock(id="tool-1", name=SEND_MESSAGE_MCP_NAME, input={}),
                ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=False),
            ],
            model=ANY_MODEL,
        )
        result_msg = result_message(is_error=False)
        mock_client = self._client_yielding(assistant_msg, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_execution_narration_covers_user_envelope_results(self, mock_tools):
        """With Emit.TOOL_CALLS on, a protocol-shaped turn narrates both the
        tool_call and the tool_result (which arrives in a user envelope)."""
        adapter = ClaudeSDKAdapter(emit=Emit.TOOL_CALLS)
        turn = tool_turn(SEND_MESSAGE_MCP_NAME)
        mock_client = self._client_yielding(*turn, result_message())

        await adapter._process_response(mock_client, "room-123", mock_tools)

        narrated_types = narrated_message_types(mock_tools)
        assert "tool_call" in narrated_types
        assert "tool_result" in narrated_types

    @pytest.mark.asyncio
    async def test_tool_result_payload_includes_name_and_is_error(self, mock_tools):
        """The tool_result event must carry NAME and IS_ERROR: parse_tool_result
        (converters/parsing.py) drops any payload missing a name outright, and
        every sibling adapter's tool_result payload sets both."""
        adapter = ClaudeSDKAdapter(emit=Emit.TOOL_CALLS)
        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=SEND_MESSAGE_MCP_NAME, input={})],
            model=ANY_MODEL,
        )
        user_msg = UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="boom", is_error=True)
            ]
        )
        mock_client = self._client_yielding(assistant_msg, user_msg, result_message())

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        payload = tool_result_payload(mock_tools)
        assert payload[ToolEventKey.NAME] == "band_send_message"
        assert payload[ToolEventKey.IS_ERROR] is True

    @pytest.mark.asyncio
    async def test_no_error_reported_when_only_read_only_tool_ran(self, mock_tools):
        """A read-only lookup (e.g. band_list_contacts) is not a terminal reply."""
        adapter = ClaudeSDKAdapter()
        turn = tool_turn("mcp__band__band_list_contacts")
        result_msg = result_message(is_error=False)
        mock_client = self._client_yielding(*turn, result_msg)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_tool_result_with_is_error_none_counts_as_success(self, mock_tools):
        """The SDK's own convention: ``ToolResultBlock.is_error`` omitted from
        the CLI's JSON (``None``) means success, same as an explicit ``False``."""
        adapter = ClaudeSDKAdapter()
        assistant_msg = AssistantMessage(
            content=[
                ToolUseBlock(id="tool-1", name=SEND_MESSAGE_MCP_NAME, input={}),
            ],
            model=ANY_MODEL,
        )
        user_msg = UserMessage(
            content=[ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=None)]
        )
        result_msg = result_message(is_error=False)
        mock_client = self._client_yielding(assistant_msg, user_msg, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_custom_terminal_tool_counts_as_reply(self, mock_tools):
        """A custom tool marked ``band_terminal=True`` must be recognized under
        its actual registered MCP name (get_custom_tool_name), not the Python
        handler's ``__name__`` — those two can differ."""

        class DeployInput(BaseModel):
            target: str

        def run_the_deploy_handler(args: DeployInput) -> str:
            return "deployed"

        run_the_deploy_handler.band_terminal = True
        adapter = ClaudeSDKAdapter(
            additional_tools=[(DeployInput, run_the_deploy_handler)]
        )
        # get_custom_tool_name(DeployInput) == "deploy" — deliberately unlike
        # the handler's own __name__, to prove the fix keys off the former.
        turn = tool_turn("mcp__band__deploy")
        result_msg = result_message(is_error=False)
        mock_client = self._client_yielding(*turn, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_declined_reply_tool_does_not_also_report_missing_reply(
        self, mock_tools
    ):
        """A denied tool call already posts its own decline notice — the
        missing-reply guard must not pile a second, contradictory error on
        top of a turn the approval flow already explained."""
        adapter = ClaudeSDKAdapter(approval_mode="auto_decline")
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext(tool_use_id="tool-1")
        )
        assert isinstance(decision, PermissionResultDeny)

        # The declined call's result comes back as an error, same as a real
        # denial would surface through the SDK's own protocol.
        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=SEND_MESSAGE_MCP_NAME, input={})],
            model=ANY_MODEL,
        )
        user_msg = UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="denied", is_error=True)
            ]
        )
        result_msg = result_message(
            is_error=False,
            permission_denials=[denial("tool-1", SEND_MESSAGE_MCP_NAME)],
        )
        mock_client = self._client_yielding(assistant_msg, user_msg, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_declined_side_tool_still_reports_missing_reply(self, mock_tools):
        """Declining a tool that would never have delivered the reply (e.g. a
        read-only lookup) does not explain a subsequent silent turn — only a
        decline notice for what would have been the reply tool does."""
        adapter = ClaudeSDKAdapter(approval_mode="auto_decline")
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            "mcp__band__band_list_contacts",
            {},
            ToolPermissionContext(tool_use_id="tool-1"),
        )
        assert isinstance(decision, PermissionResultDeny)

        assistant_msg = AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tool-1", name="mcp__band__band_list_contacts", input={}
                )
            ],
            model=ANY_MODEL,
        )
        user_msg = UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="denied", is_error=True)
            ]
        )
        result_msg = result_message(
            is_error=False,
            permission_denials=[denial("tool-1", "mcp__band__band_list_contacts")],
        )
        mock_client = self._client_yielding(assistant_msg, user_msg, result_msg)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_notified_decline_does_not_leak_past_a_turn_that_replied(
        self, mock_tools
    ):
        """A side tool declined-and-notified in a turn that still replies via
        band_send_message must not leave a stale notified-decline entry for
        this room once the turn completes — otherwise it grows unbounded
        over the life of a room that declines side tools but keeps
        answering normally."""
        adapter = ClaudeSDKAdapter(approval_mode="auto_decline")
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            "mcp__band__band_list_contacts",
            {},
            ToolPermissionContext(tool_use_id="tool-1"),
        )
        assert isinstance(decision, PermissionResultDeny)
        assert "tool-1" in adapter._notified_declines["room-123"]

        turn = tool_turn(SEND_MESSAGE_MCP_NAME)
        result_msg = result_message(
            is_error=False,
            permission_denials=[denial("tool-1", "mcp__band__band_list_contacts")],
        )
        mock_client = self._client_yielding(*turn, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert error_events(mock_tools) == []
        assert "room-123" not in adapter._notified_declines

    @pytest.mark.asyncio
    async def test_user_envelope_tool_use_is_tracked(self, mock_tools):
        """A tool_use block carried in a user-type envelope (e.g. a
        subagent's nested call) must be tracked the same as one carried by
        an assistant message, not silently dropped."""
        adapter = ClaudeSDKAdapter()
        user_msg = UserMessage(
            content=[
                ToolUseBlock(id="tool-1", name=SEND_MESSAGE_MCP_NAME, input={}),
                ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=False),
            ]
        )
        result_msg = result_message(is_error=False)
        mock_client = self._client_yielding(user_msg, result_msg)

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_stream_eof_after_delivered_reply_completes_the_turn(
        self, mock_tools
    ):
        """The CLI dying between a delivered reply and its ResultMessage must
        not fail the turn: the reply already reached the room, and a failed
        turn would make the runtime redeliver the message and answer the user
        twice. No exception, no room-visible error."""
        adapter = ClaudeSDKAdapter()
        turn = tool_turn(SEND_MESSAGE_MCP_NAME)
        mock_client = self._client_yielding(*turn)  # no ResultMessage

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_stream_eof_without_reply_still_fails_the_turn(self, mock_tools):
        """An EOF on a turn that delivered nothing is a dead client: it must
        reach the dead-client recovery path, not return as a success."""
        adapter = ClaudeSDKAdapter()
        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=SEND_MESSAGE_MCP_NAME, input={})],
            model=ANY_MODEL,
        )
        mock_client = self._client_yielding(assistant_msg)  # no result, no reply

        with pytest.raises(CLIConnectionError, match="ended without a result"):
            await adapter._process_response(mock_client, "room-123", mock_tools)

    @pytest.mark.asyncio
    async def test_replayed_tool_result_from_previous_turn_counts_as_reply(
        self, mock_tools
    ):
        """A resumed session can replay a tool result whose tool_use streamed
        in an earlier, truncated turn. The pending-call map is room-scoped so
        that result still resolves to its tool name and counts as the turn's
        answer — no spurious missing-reply error on an answered turn."""
        adapter = ClaudeSDKAdapter()
        tool_use_turn = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=SEND_MESSAGE_MCP_NAME, input={})],
            model=ANY_MODEL,
        )
        dead_client = self._client_yielding(tool_use_turn)  # dies before result
        with pytest.raises(CLIConnectionError, match="ended without a result"):
            await adapter._process_response(dead_client, "room-123", mock_tools)

        replayed_result = UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=False)
            ]
        )
        resumed_client = self._client_yielding(
            replayed_result, result_message(is_error=False)
        )
        await adapter._process_response(resumed_client, "room-123", mock_tools)

        assert error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_band_tool_error_string_is_not_terminal_work(self, mock_tools):
        """A Band tool wrapper that caught an exception returns an "Error "
        string without setting is_error; that reply never reached the room,
        so the missing-reply guard must still fire."""
        adapter = ClaudeSDKAdapter()
        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=SEND_MESSAGE_MCP_NAME, input={})],
            model=ANY_MODEL,
        )
        failed_result = UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tool-1", content="Error sending message", is_error=None
                )
            ]
        )
        mock_client = self._client_yielding(
            assistant_msg, failed_result, result_message(is_error=False)
        )

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_unserializable_tool_payload_does_not_abort_the_turn(
        self, mock_tools
    ):
        """Narration payloads are serialized lazily, past the emit gate and
        inside its try — a tool result the default no-emit adapter can't
        json.dumps must cost nothing and never abort the turn."""
        adapter = ClaudeSDKAdapter()
        turn = tool_turn(SEND_MESSAGE_MCP_NAME)
        turn[1].content[0].content = object()  # not JSON-serializable

        mock_client = self._client_yielding(*turn, result_message(is_error=False))

        await adapter._process_response(mock_client, "room-123", mock_tools)

        assert error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_silent_auto_decline_still_reports_missing_reply(self, mock_tools):
        """``approval_text_notifications=False`` means auto_decline denies a
        tool call without ever telling the room why — so the missing-reply
        guard must still fire; the turn cannot be silently marked as already
        explained when no explanation was actually delivered."""
        adapter = ClaudeSDKAdapter(
            approval_mode="auto_decline", approval_text_notifications=False
        )
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext(tool_use_id="tool-1")
        )
        assert isinstance(decision, PermissionResultDeny)
        mock_tools.send_message.assert_not_awaited()

        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=SEND_MESSAGE_MCP_NAME, input={})],
            model=ANY_MODEL,
        )
        user_msg = UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="denied", is_error=True)
            ]
        )
        # The CLI still reports the denial (see permission_denials) even
        # though our own notification never reached the room — proves the
        # guard is gated on delivery, not merely on the CLI's own record.
        result_msg = result_message(
            is_error=False,
            permission_denials=[denial("tool-1", SEND_MESSAGE_MCP_NAME)],
        )
        mock_client = self._client_yielding(assistant_msg, user_msg, result_msg)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_declined_marker_does_not_leak_into_next_turn(self, mock_tools):
        """A decline from a turn that then dies before its ResultMessage must
        not leave the room's next, unrelated turn looking pre-explained, even
        though nothing ever clears the leftover notified-tool_use_id record
        (a real CLI never reuses a tool_use_id, so the next turn's own
        ``permission_denials`` can never accidentally match it)."""
        adapter = ClaudeSDKAdapter(approval_mode="auto_decline")
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext(tool_use_id="tool-1")
        )
        assert isinstance(decision, PermissionResultDeny)

        dead_turn_client = self._client_yielding()  # ends with no ResultMessage
        with pytest.raises(CLIConnectionError, match="ended without a result"):
            await adapter._process_response(dead_turn_client, "room-123", mock_tools)

        # A fresh, unrelated turn: no tool activity, no permission_denials.
        next_turn_client = self._client_yielding(result_message(is_error=False))
        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(next_turn_client, "room-123", mock_tools)

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_undelivered_approval_prompt_still_reports_missing_reply(
        self, mock_tools
    ):
        """Manual mode's approval prompt itself failed to send — the room got
        no explanation at all — so the missing-reply guard must still fire
        even though the tool call was denied."""
        adapter = ClaudeSDKAdapter(approval_mode="manual", approval_wait_timeout_s=5.0)
        mock_tools.send_message = AsyncMock(side_effect=RuntimeError("network down"))
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext()
        )
        assert isinstance(decision, PermissionResultDeny)

        result_msg = result_message(is_error=False)
        mock_client = self._client_yielding(result_msg)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert MISSING_REPLY_TEXT in errors[0]

    @pytest.mark.asyncio
    async def test_undelivered_timeout_notice_still_reports_missing_reply(
        self, mock_tools
    ):
        """An approval that times out into a decline suppresses the
        missing-reply guard only when its timeout notice actually reached the
        room; here the prompt sends fine but the timeout notice fails, so the
        guard must still fire."""
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_wait_timeout_s=0.05,
            approval_timeout_decision="decline",
        )
        mock_tools.send_message = AsyncMock(
            side_effect=[{"status": "sent"}, RuntimeError("network down")]
        )
        adapter._room_tools["room-123"] = mock_tools
        can_use_tool = adapter._make_can_use_tool("room-123")

        decision = await can_use_tool(
            SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext()
        )
        assert isinstance(decision, PermissionResultDeny)

        result_msg = result_message(is_error=False)
        mock_client = self._client_yielding(result_msg)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(mock_client, "room-123", mock_tools)

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert MISSING_REPLY_TEXT in errors[0]

    def _declined_reply_turn(self) -> MagicMock:
        return self._client_yielding(
            AssistantMessage(
                content=[
                    ToolUseBlock(id="tool-1", name=SEND_MESSAGE_MCP_NAME, input={})
                ],
                model=ANY_MODEL,
            ),
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="tool-1", content="denied", is_error=True
                    )
                ]
            ),
            result_message(
                is_error=False,
                permission_denials=[denial("tool-1", SEND_MESSAGE_MCP_NAME)],
            ),
        )

    @pytest.mark.asyncio
    async def test_delivered_manual_decline_notice_explains_the_turn(self, mock_tools):
        """A room /decline whose "resolved" notice reached the room already
        explains the declined reply, so no missing-reply error follows."""
        adapter = ClaudeSDKAdapter(approval_mode="manual", approval_wait_timeout_s=5.0)
        adapter._room_tools["room-123"] = mock_tools

        decision = await reply_to_approval(
            adapter,
            mock_tools,
            "decline",
            {"id": "u1", "name": "Bob"},
            room_id="room-123",
            tool_use_id="tool-1",
        )
        assert isinstance(decision, PermissionResultDeny)

        await adapter._process_response(
            self._declined_reply_turn(), "room-123", mock_tools
        )

        assert error_events(mock_tools) == []

    @pytest.mark.asyncio
    async def test_undelivered_manual_decline_notice_still_reports_missing_reply(
        self, mock_tools
    ):
        """A room /decline whose "resolved" notice failed to send explained
        nothing, so the missing-reply guard must still fire."""
        adapter = ClaudeSDKAdapter(approval_mode="manual", approval_wait_timeout_s=5.0)
        mock_tools.send_message = AsyncMock(
            side_effect=[{"status": "sent"}, RuntimeError("network down")]
        )
        adapter._room_tools["room-123"] = mock_tools

        decision = await reply_to_approval(
            adapter,
            mock_tools,
            "decline",
            {"id": "u1", "name": "Bob"},
            room_id="room-123",
            tool_use_id="tool-1",
        )
        assert isinstance(decision, PermissionResultDeny)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter._process_response(
                self._declined_reply_turn(), "room-123", mock_tools
            )

        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert MISSING_REPLY_TEXT in errors[0]
