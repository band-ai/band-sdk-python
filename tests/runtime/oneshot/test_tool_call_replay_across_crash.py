"""OneShotInvoker: a crash before acknowledgement replays tool side effects."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import ToolUseBlock
from pydantic import BaseModel

from band.adapters.anthropic import AnthropicAdapter
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from tests.runtime.conftest import make_link_mock, platform_msg
from tests.runtime.oneshot.kit import make_invoker, msg_body

# ---------------------------------------------------------------------------
# Tool-call replay across a container crash.
#
# OneShotInvoker keeps no state across calls by design, so nothing survives
# a crash between a tool's side effect and mark_processed — the platform's
# /next re-serves the same still-"processing" message to whatever container
# picks it up next, and a fresh adapter instance replays the whole turn from
# scratch. This documents that as current, real behavior (not a bug this
# file can fix — see band.runtime.single_instance and
# band_sdk_core.ClaimRegistry, both of which state the same
# cross-process boundary is out of the SDK's reach).
# ---------------------------------------------------------------------------


class SendEmailInput(BaseModel):
    """Send an email to an address."""

    to: str


class TestToolCallReplayAcrossCrash:
    async def test_crash_before_mark_processed_replays_the_tool(self) -> None:
        sent_emails: list[str] = []

        @declares_turn_effect(TurnEffect.ACT)
        async def send_email(args: SendEmailInput) -> str:
            sent_emails.append(args.to)
            return "sent"

        link = make_link_mock(next_messages=[platform_msg("msg-1")])

        # --- Attempt 1: the tool's side effect fires, then the container
        # dies before the turn finishes (never reaches mark_processed).
        adapter_a = AnthropicAdapter(additional_tools=[(SendEmailInput, send_email)])
        invoker_a = await make_invoker(link, adapter_a)

        tool_use_email = MagicMock(stop_reason="tool_use")
        tool_use_email.content = [
            ToolUseBlock(
                type="tool_use",
                id="call-1",
                name="sendemail",
                input={"to": "boss@example.com"},
            )
        ]
        with (
            patch.object(
                adapter_a,
                "_call_anthropic",
                AsyncMock(side_effect=[tool_use_email, SystemExit("container killed")]),
            ),
            pytest.raises(SystemExit),
        ):
            await invoker_a.handle_event(msg_body())

        assert sent_emails == ["boss@example.com"]
        link.mark_processed.assert_not_awaited()
        link.mark_failed.assert_not_awaited()

        # --- Restart: a fresh container, fresh adapter, same still-open
        # message — /next re-serves it exactly as it would after a crash.
        link.get_next_message = AsyncMock(side_effect=[platform_msg("msg-1"), None])
        link.mark_processing.reset_mock()
        adapter_b = AnthropicAdapter(additional_tools=[(SendEmailInput, send_email)])
        invoker_b = await make_invoker(link, adapter_b)

        turn_end = MagicMock(stop_reason="end_turn")
        turn_end.content = []
        with patch.object(
            adapter_b,
            "_call_anthropic",
            AsyncMock(side_effect=[tool_use_email, turn_end]),
        ):
            result = await invoker_b.handle_event(msg_body())

        assert result["status"] == "done"
        link.mark_processed.assert_awaited_once_with("room-1", "msg-1")

        # The email tool fired twice for one logical request — nothing in
        # OneShotInvoker (or the platform's claim primitives it relies on)
        # prevents a crash-and-restart from replaying a completed side
        # effect. Custom tools with real-world side effects need their own
        # idempotency (e.g. keying on tool arguments), same as any at-least-
        # once delivery system.
        assert sent_emails == ["boss@example.com", "boss@example.com"]
