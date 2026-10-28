"""Session resume, history injection fallback, and session-id persistence."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapterConfig
from band.converters.copilot_sdk import COPILOT_RESUME_RECORD, CopilotSDKSessionState
from tests.adapters.copilot_sdk.fakes import (
    HISTORY_HEADING,
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk

RECORDED_ID = "band-room-1"


def recorded() -> dict[str, str]:
    """A fresh room-to-session map naming room-1's recorded session."""
    return {"room-1": RECORDED_ID}


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
                text="[Alice]: earlier", room_to_session=recorded()
            ),
        )

        assert client.resume_calls == [RECORDED_ID]
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
                text="[Alice]: earlier", room_to_session=recorded()
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
                text="[Alice]: earlier", room_to_session=recorded()
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
        assert COPILOT_RESUME_RECORD.candidates(task_events) == {
            "room-1": client.sessions[0].session_id
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
            adapter, tools, history=CopilotSDKSessionState(room_to_session=recorded())
        )

        assert not [e for e in tools.events_sent if e["message_type"] == "task"]

    async def test_another_rooms_record_starts_a_fresh_replayed_session(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(
            adapter,
            tools,
            history=CopilotSDKSessionState(
                text="[Alice]: earlier", room_to_session={"room-2": RECORDED_ID}
            ),
        )

        assert client.resume_calls == []
        assert f"{HISTORY_HEADING}\n[Alice]: earlier" in client.sessions[0].prompts[0]

    async def test_a_failed_seed_after_a_failed_resume_retries_the_record_unreplayed(
        self,
    ):
        client = FakeCopilotClient(
            resume_error=RuntimeError("no session state"),
            prompt_errors=[RuntimeError("model unavailable")],
        )
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(inject_history_on_resume_failure=False)
        )
        tools = ToolSchemaFakeTools()
        history = CopilotSDKSessionState(
            text="[Alice]: earlier", room_to_session=recorded()
        )
        with pytest.raises(RuntimeError, match="model unavailable"):
            await run_message(adapter, tools, history=history)

        await run_message(adapter, tools, is_session_bootstrap=False)

        assert client.resume_calls == [RECORDED_ID, RECORDED_ID]
        assert HISTORY_HEADING not in client.sessions[-1].prompts[0]

    async def test_a_bootstrap_resumes_the_seeded_session_over_an_older_record(self):
        client = FakeCopilotClient(resume_error=RuntimeError("transient"))
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        older = CopilotSDKSessionState(
            text="[Alice]: earlier", room_to_session=recorded()
        )
        tools.send_event_error = ConnectionError("Band REST unavailable")
        await run_message(adapter, tools, history=older)  # seeds unrecorded Y
        seeded = client.sessions[0]
        tools.send_event_error = None
        client.resume_error = None
        seeded.send_error = RuntimeError("broken pipe")
        with pytest.raises(RuntimeError, match="broken pipe"):
            await run_message(adapter, tools, history=older)

        await run_message(adapter, tools, history=older)

        assert client.resume_calls[-1] == seeded.session_id
        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": seeded.session_id
        }
