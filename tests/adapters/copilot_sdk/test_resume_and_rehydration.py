"""Confirmed session resume, conservative failure, and session-id persistence."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import HISTORY_HEADING, CopilotSDKAdapterConfig
from band.converters.copilot_sdk import COPILOT_RESUME_RECORD, CopilotSDKSessionState
from band.core.exceptions import CleanupIncompleteError, ProviderUnavailableError
from tests.adapters.copilot_sdk.fakes import (
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
        assert HISTORY_HEADING not in session.prompts[0]

    @pytest.mark.parametrize("inject_history", [True, False])
    @pytest.mark.parametrize("failure", [RuntimeError("setup failed"), TimeoutError()])
    async def test_disposed_resume_failure_allows_a_fresh_session(
        self, inject_history: bool, failure: Exception
    ) -> None:
        client = FakeCopilotClient(resume_error=failure)
        adapter = await make_started_adapter(
            client,
            CopilotSDKAdapterConfig(inject_history_on_resume_failure=inject_history),
        )
        tools = ToolSchemaFakeTools()
        history = CopilotSDKSessionState(
            text="[Alice]: earlier", room_to_session=recorded()
        )
        with pytest.raises(type(failure)):
            await run_message(adapter, tools, history=history)
        assert client.deleted == [RECORDED_ID]
        client.resume_error = None
        await run_message(adapter, tools, history=history)
        assert client.resume_calls == [RECORDED_ID]
        assert client.deleted == [RECORDED_ID]
        assert len(client.create_calls) == 1
        assert len(client.sessions) == 1

    @pytest.mark.parametrize("inject_history", [True, False])
    async def test_undisposed_resume_failure_latches_unavailable(
        self, inject_history: bool
    ) -> None:
        client = FakeCopilotClient(resume_error=RuntimeError("setup failed"))
        client.delete_error = RuntimeError("delete failed")
        adapter = await make_started_adapter(
            client,
            CopilotSDKAdapterConfig(inject_history_on_resume_failure=inject_history),
        )
        tools = ToolSchemaFakeTools()
        history = CopilotSDKSessionState(
            text="[Alice]: earlier", room_to_session=recorded()
        )
        with pytest.raises(RuntimeError, match="delete failed"):
            await run_message(adapter, tools, history=history)
        client.resume_error = None
        client.delete_error = None
        with pytest.raises(ProviderUnavailableError):
            await run_message(adapter, tools, history=history)
        assert client.resume_calls == [RECORDED_ID]
        assert client.create_calls == []
        assert client.sessions == []
        with pytest.raises(CleanupIncompleteError):
            await adapter.on_cleanup("room-1")
        assert adapter.backend_turns.unavailable("room-1")

    @pytest.mark.asyncio
    async def test_fresh_room_with_history_text_injects_it(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(
            adapter, tools, history=CopilotSDKSessionState(text="[Alice]: earlier")
        )

        assert f"{HISTORY_HEADING}\n[Alice]: earlier" in (client.sessions[0].prompts[0])

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

        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        tools.send_event_error = RuntimeError("transient REST error")

        await run_message(adapter, tools)
        assert not [e for e in tools.events_sent if e["message_type"] == "task"]
        tools.send_event_error = None

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

    async def test_a_bootstrap_resumes_the_seeded_session_over_an_older_record(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        older = CopilotSDKSessionState(
            text="[Alice]: earlier", room_to_session=recorded()
        )
        tools.send_event_error = ConnectionError("Band REST unavailable")
        # The fresh session seeds, but its record post fails.
        await run_message(
            adapter, tools, history=CopilotSDKSessionState(text=older.text)
        )
        seeded = client.sessions[0]
        tools.send_event_error = None
        seeded.send_error = RuntimeError("broken pipe")
        with pytest.raises(RuntimeError, match="broken pipe"):
            await run_message(adapter, tools, history=older)

        await run_message(adapter, tools, history=older)

        assert client.resume_calls[-1] == seeded.session_id
        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": seeded.session_id
        }
