from __future__ import annotations

import asyncio

import pytest

from band.adapters.opencode import OpencodeAdapterConfig
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.audiences import AGENT
from tests.adapters.hold import Hold
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_message_updated,
    event_permission,
    event_session_error,
    event_session_idle,
    event_text_part,
    room_turn,
    run_single_turn,
    turn_finished,
)


class TestOpencodeAudienceCompletion:
    async def test_undeliverable_permission_declines_but_waits_for_provider_completion(
        self, make_adapter
    ) -> None:
        completion = Hold()
        client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", "request")]],
            reply_permission_events={
                "request": [completion.wait, event_session_idle("sess-1")]
            },
        )
        adapter = make_adapter(client)
        tools = FakeAgentTools(participants=[AGENT])
        task = asyncio.create_task(run_single_turn(adapter, tools))
        try:
            await completion.arrived()
            assert [reply["reply"] for reply in client.permission_replies] == ["reject"]
            assert not tools.turn.complete
            assert tools.messages_sent == []
            turn = await room_turn(adapter)
            completion.released.set()
            await task
            await turn_finished(turn)
            assert tools.turn.complete
            assert not tools.turn.replied
            assert reported_failures(tools) == []
        finally:
            completion.released.set()
            await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.parametrize(
        "fallback", [False, True], ids=["fallback-disabled", "fallback-enabled"]
    )
    @pytest.mark.parametrize("text", ["", "Provider text"], ids=["silent", "visible"])
    async def test_provider_error_is_preserved_when_originators_left(
        self, make_adapter, fallback: bool, text: str
    ) -> None:
        error = "provider unavailable"
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "message"),
                    event_text_part("sess-1", "message", text),
                    event_session_error("sess-1", error),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(
            client, config=OpencodeAdapterConfig(fallback_send_agent_text=fallback)
        )
        tools = FakeAgentTools(participants=[AGENT])
        await run_single_turn(adapter, tools)
        assert tools.messages_sent == []
        assert any(error in failure["message"] for failure in reported_failures(tools))
