"""Final-reply delivery: mentions, prompt shape, and failure surfacing."""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.runtime.tools import CHAT_ID_FIELD_NAME, ToolCallOutcome
from band.testing import reported_failures
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    FakeCopilotSession,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolInvocation
    from copilot.generated.session_events import (
        AbortData,
        AbortReason,
        AssistantTurnRetryData,
        ModelCallFailureData,
        ModelCallFailureKind,
        ModelCallFailureSource,
        SessionErrorData,
        SessionWarningData,
        ToolExecutionCompleteData,
        ToolExecutionCompleteError,
    )


class TestReply:
    @pytest.mark.asyncio
    async def test_reply_sent_with_sender_mention(self):
        client = FakeCopilotClient(reply_content="Hi Alice!")
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        assert len(tools.messages_sent) == 1
        sent = tools.messages_sent[0]
        assert sent["content"] == "Hi Alice!"
        assert sent["mentions"] == [{"id": "user-1", "name": "Alice"}]

    @pytest.mark.asyncio
    async def test_send_message_failure_is_not_reported_as_provider_failure(self):
        """Copilot answered fine; the room POST is what failed. That must not
        surface as a copilot_sdk AgentFailure -- deliver_reply's
        DeliveryFailedError must be recognized and left unreported here."""
        client = FakeCopilotClient(reply_content="Hi Alice!")
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        tools.send_message_error = RuntimeError("platform rejected the message")

        with pytest.raises(RuntimeError, match="platform rejected the message"):
            await run_message(adapter, tools)

        assert not reported_failures(tools)

    @pytest.mark.asyncio
    async def test_prompt_carries_the_message_but_no_room_id(self):
        """The tools are bound to the room, so the prompt never states it."""
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools, content="What's up?")

        prompt = client.sessions[0].prompts[0]
        assert CHAT_ID_FIELD_NAME not in prompt
        assert "[Alice]: What's up?" in prompt

    @pytest.mark.asyncio
    async def test_no_reply_raises_and_reports_error(self):
        client = FakeCopilotClient(reply_content=None)
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        with pytest.raises(RuntimeError, match="no reply"):
            await run_message(adapter, tools)

        assert not tools.messages_sent
        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert error_events

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("incident", "account"),
        [
            pytest.param(
                lambda: ModelCallFailureData(
                    source=ModelCallFailureSource.TOP_LEVEL,
                    error_type="overloaded_error",
                    status_code=529,
                ),
                "model call failed: overloaded_error status=529",
                id="model-call-failure",
            ),
            pytest.param(
                lambda: ModelCallFailureData(
                    source=ModelCallFailureSource.TOP_LEVEL,
                    failure_kind=ModelCallFailureKind.TRANSPORT,
                ),
                "model call failed: transport",
                id="model-call-failure-kind-only",
            ),
            pytest.param(
                lambda: AssistantTurnRetryData(turn_id="t1"),
                "turn retried: no reason given",
                id="retry",
            ),
            pytest.param(
                lambda: SessionWarningData(
                    message="context truncated", warning_type="context"
                ),
                "warning context: context truncated",
                id="warning",
            ),
            pytest.param(
                lambda: AbortData(reason=AbortReason.USER_INITIATED),
                "aborted: user_initiated",
                id="abort",
            ),
            pytest.param(
                lambda: ToolExecutionCompleteData(
                    success=False,
                    tool_call_id="call-1",
                    error=ToolExecutionCompleteError(message="permission denied"),
                ),
                "tool call-1 failed: permission denied",
                id="tool-failure",
            ),
        ],
    )
    async def test_no_reply_logs_the_trouble_the_turn_hit(
        self,
        incident: Callable[[], object],
        account: str,
        caplog: pytest.LogCaptureFixture,
    ):
        """A silent turn's log says what went wrong, not just that nothing came."""
        client = FakeCopilotClient(reply_content=None, turn_events=[incident()])
        adapter = await make_started_adapter(client)

        tools = ToolSchemaFakeTools()
        with (
            caplog.at_level(logging.WARNING),
            pytest.raises(RuntimeError, match="^Copilot turn produced no reply$"),
        ):
            await run_message(adapter, tools)

        warnings = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING
            and f"(incidents: {account})" in record.getMessage()
        ]
        assert len(warnings) == 1
        failures = reported_failures(tools)
        assert failures and failures[0]["message"] == "no assistant reply"
        assert account not in failures[0]["message"]

    @pytest.mark.asyncio
    async def test_session_error_raises_reports_and_evicts(self):
        """A session error makes send_and_wait raise (the real SDK has no
        non-fatal error path): the adapter must report it, evict the
        session, and re-raise — not fall through to the no-reply branch."""
        client = FakeCopilotClient(
            turn_events=[SessionErrorData(error_type="model_error", message="boom")],
        )
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        with pytest.raises(Exception, match="boom"):
            await run_message(adapter, tools)

        session = client.sessions[0]
        assert session.aborted and session.disconnected
        failures = reported_failures(tools)
        assert failures and failures[0]["provider"] == "copilot_sdk"
        assert failures[0]["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE

    @pytest.mark.asyncio
    async def test_fallback_send_suppressed_when_band_send_message_fired(self):
        async def model_sends_message(session: FakeCopilotSession) -> None:
            handler = session.find_tool("band_send_message").handler
            await handler(
                ToolInvocation(
                    tool_call_id="call-1",
                    tool_name="band_send_message",
                    arguments={"content": "sent via tool", "mentions": ["user-1"]},
                )
            )

        client = FakeCopilotClient(
            reply_content="Duplicate reply", turn_events=[model_sends_message]
        )
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        # Tool call executed, but the adapter must not also send its final text.
        assert tools.tool_calls == [
            {
                "tool_name": "band_send_message",
                "arguments": {"content": "sent via tool", "mentions": ["user-1"]},
            }
        ]
        assert not tools.messages_sent

    @pytest.mark.asyncio
    async def test_fallback_fires_when_band_send_message_fails(self):
        """A failed band_send_message (ok=False, no exception) must NOT mark the turn
        replied — the final-text fallback must still fire, else the user gets a silent
        turn."""

        class SendFailsTools(ToolSchemaFakeTools):
            async def execute_tool_call_structured(self, tool_name, arguments):
                if tool_name == "band_send_message":
                    return ToolCallOutcome(
                        value="Error executing band_send_message: upstream 500",
                        ok=False,
                        error_message="upstream 500",
                    )
                return await super().execute_tool_call_structured(tool_name, arguments)

        async def model_sends_message(session: FakeCopilotSession) -> None:
            await session.find_tool("band_send_message").handler(
                ToolInvocation(
                    tool_call_id="call-1",
                    tool_name="band_send_message",
                    arguments={"content": "sent via tool", "mentions": ["user-1"]},
                )
            )

        client = FakeCopilotClient(
            reply_content="Fallback reply", turn_events=[model_sends_message]
        )
        adapter = await make_started_adapter(client)
        tools = SendFailsTools()

        await run_message(adapter, tools)

        # The tool failed, so the fallback text must reach the room (no silent turn).
        assert [m["content"] for m in tools.messages_sent] == ["Fallback reply"]
