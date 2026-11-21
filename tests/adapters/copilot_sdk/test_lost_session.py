"""A recorded session the Copilot runtime lost is replaced in the same turn."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import HISTORY_HEADING
from band.converters.copilot_sdk import COPILOT_RESUME_RECORD, CopilotSDKSessionState
from band.core.exceptions import ProviderUnavailableError
from band.testing import reported_failures
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk

LOST_ID = "band-room-1-lost"
EARLIER = "[Alice]: the tracking marker is m-42"


def history() -> CopilotSDKSessionState:
    """Bootstrap history naming room-1's recorded session and its transcript."""
    return CopilotSDKSessionState(text=EARLIER, room_to_session={"room-1": LOST_ID})


class TestLostSession:
    async def test_lost_session_is_replaced_by_a_replayed_session_in_the_same_turn(
        self,
    ) -> None:
        # The runtime would also refuse to delete it, as the Copilot CLI does.
        client = FakeCopilotClient()
        client.lost.add(LOST_ID)
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools, history=history())

        assert client.resume_calls == [LOST_ID]
        assert client.deleted == []
        (session,) = client.sessions
        assert not session.resumed
        assert session.session_id != LOST_ID
        assert f"{HISTORY_HEADING}\n{EARLIER}" in session.prompts[0]
        assert reported_failures(tools) == []
        assert COPILOT_RESUME_RECORD.candidates(tools.events_sent) == {
            "room-1": session.session_id
        }

    async def test_room_with_a_replaced_session_keeps_working_and_cleans_up(
        self,
    ) -> None:
        client = FakeCopilotClient()
        client.lost.add(LOST_ID)
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools, history=history())

        await run_message(adapter, tools, history=history(), is_session_bootstrap=False)
        await adapter.on_cleanup("room-1")

        assert client.resume_calls == [LOST_ID]
        assert len(client.create_calls) == 1
        (session,) = client.sessions
        assert len(session.prompts) == 2
        assert session.disconnected

    async def test_failed_lookup_disposes_the_session_and_fails_the_turn(
        self,
    ) -> None:
        # Without the runtime's word that the session is gone, a failed resume
        # may have left state behind, so it is disposed before anything else.
        failure = RuntimeError("resume failed")
        client = FakeCopilotClient(resume_error=failure)
        client.metadata_error = ConnectionError("runtime unreachable")
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        with pytest.raises(RuntimeError, match="resume failed"):
            await run_message(adapter, tools, history=history())

        assert client.metadata_calls == [LOST_ID]
        assert client.deleted == [LOST_ID]
        assert client.create_calls == []

    async def test_failed_lookup_and_failed_disposal_latch_unavailable(
        self,
    ) -> None:
        client = FakeCopilotClient()
        client.lost.add(LOST_ID)
        client.metadata_error = ConnectionError("runtime unreachable")
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        with pytest.raises(RuntimeError, match="Session file not found"):
            await run_message(adapter, tools, history=history())
        client.metadata_error = None
        with pytest.raises(ProviderUnavailableError):
            await run_message(adapter, tools, history=history())

        assert client.resume_calls == [LOST_ID]
        assert client.create_calls == []
