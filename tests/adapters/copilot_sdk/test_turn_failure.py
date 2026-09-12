"""Failed turns: session eviction, runtime revival, and empty-reply handling."""

from __future__ import annotations

import pytest

from band.testing import reported_failures
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk


class TestTurnFailure:
    @pytest.mark.asyncio
    async def test_failed_turn_aborts_and_evicts_session(self):
        """A failed/timed-out turn must not leave a dead session cached."""
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools)

        dead = client.sessions[0]
        dead.send_error = TimeoutError("turn timed out")
        with pytest.raises(TimeoutError):
            await run_message(adapter, tools, is_session_bootstrap=False)

        # The stale turn is aborted on the runtime and the session dropped...
        assert dead.aborted
        assert dead.disconnected
        failures = reported_failures(tools)
        assert failures
        assert failures[0]["provider"] == "copilot_sdk"

        # ...so the next message starts clean, resuming by the stored id.
        await run_message(adapter, tools, is_session_bootstrap=False)
        assert client.resume_calls == ["band-copilot-agent-room-1"]
        assert client.sessions[-1].prompts

    @pytest.mark.asyncio
    async def test_crashed_runtime_revived_on_next_message(self):
        """After a failed turn evicts the session, the next message must
        re-run client.start() — a crashed CLI only heals through start()."""
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools)

        client.sessions[0].send_error = RuntimeError("broken pipe")
        with pytest.raises(RuntimeError):
            await run_message(adapter, tools, is_session_bootstrap=False)
        starts_before = client.start_calls

        await run_message(adapter, tools, is_session_bootstrap=False)

        assert client.start_calls > starts_before  # revival attempted
        assert client.sessions[-1].prompts  # and the turn ran

    @pytest.mark.asyncio
    async def test_empty_final_text_raises_no_reply(self):
        """An empty final assistant message is a failed turn, not a silent no-op."""
        client = FakeCopilotClient(reply_content="   ")
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        with pytest.raises(RuntimeError, match="no reply"):
            await run_message(adapter, tools)

        assert not tools.messages_sent

    @pytest.mark.asyncio
    async def test_session_creation_failure_is_reported(self):
        """A create_session failure, raised before on_message's own turn
        processing begins, must still be reported."""
        client = FakeCopilotClient(create_error=RuntimeError("Copilot CLI unreachable"))
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        with pytest.raises(RuntimeError, match="Copilot CLI unreachable"):
            await run_message(adapter, tools)

        failures = reported_failures(tools)
        assert failures
        assert failures[0]["provider"] == "copilot_sdk"
