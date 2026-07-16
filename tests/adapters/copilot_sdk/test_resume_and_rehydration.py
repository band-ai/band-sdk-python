"""Session resume, history injection fallback, and session-id persistence."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapterConfig
from band.converters.copilot_sdk import CopilotSDKSessionState
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk


class TestResumeAndRehydration:
    @pytest.mark.asyncio
    async def test_resumes_session_from_history(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(
            adapter,
            tools,
            history=CopilotSDKSessionState(
                text="[Alice]: earlier", session_id="band-room-1"
            ),
        )

        assert client.resume_calls == ["band-room-1"]
        session = client.sessions[0]
        assert session.resumed
        # Resumed sessions restore history from disk — no re-injection.
        assert "[Previous conversation context:]" not in session.prompts[0]

    @pytest.mark.asyncio
    async def test_resume_failure_creates_fresh_and_injects_history(self):
        client = FakeCopilotClient(resume_error=RuntimeError("no session state"))
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(
            adapter,
            tools,
            history=CopilotSDKSessionState(
                text="[Alice]: earlier", session_id="band-room-1"
            ),
        )

        session = client.sessions[0]
        assert not session.resumed
        assert (
            "[Previous conversation context:]\n[Alice]: earlier" in session.prompts[0]
        )

    @pytest.mark.asyncio
    async def test_resume_failure_injection_disabled(self):
        client = FakeCopilotClient(resume_error=RuntimeError("no session state"))
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(inject_history_on_resume_failure=False)
        )
        tools = ToolSchemaFakeTools()

        await run_message(
            adapter,
            tools,
            history=CopilotSDKSessionState(
                text="[Alice]: earlier", session_id="band-room-1"
            ),
        )

        assert "[Previous conversation context:]" not in client.sessions[0].prompts[0]

    @pytest.mark.asyncio
    async def test_fresh_room_with_history_text_injects_it(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(
            adapter, tools, history=CopilotSDKSessionState(text="[Alice]: earlier")
        )

        assert (
            "[Previous conversation context:]\n[Alice]: earlier"
            in (client.sessions[0].prompts[0])
        )

    @pytest.mark.asyncio
    async def test_session_id_persisted_as_task_event(self):
        """Persistence is unconditional bookkeeping — no Emit gate needed."""
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)
        await run_message(adapter, tools, is_session_bootstrap=False)

        task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
        assert len(task_events) == 1  # persisted once, not per turn
        assert task_events[0]["metadata"] == {
            "copilot_session_id": "band-copilot-agent-room-1"
        }

    @pytest.mark.asyncio
    async def test_persist_retried_after_send_failure(self):
        """A transient task-event send failure must be retried next turn."""

        class FlakyEventTools(ToolSchemaFakeTools):
            fail_once = True

            async def send_event(self, content, message_type, metadata=None):
                if message_type == "task" and self.fail_once:
                    self.fail_once = False
                    raise RuntimeError("transient REST error")
                return await super().send_event(content, message_type, metadata)

        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = FlakyEventTools()

        await run_message(adapter, tools)
        assert not [e for e in tools.events_sent if e["message_type"] == "task"]

        await run_message(adapter, tools, is_session_bootstrap=False)
        task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
        assert len(task_events) == 1  # retried and then not re-persisted

    @pytest.mark.asyncio
    async def test_known_session_id_not_repersisted(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(
            adapter, tools, history=CopilotSDKSessionState(session_id="band-room-1")
        )

        assert not [e for e in tools.events_sent if e["message_type"] == "task"]
