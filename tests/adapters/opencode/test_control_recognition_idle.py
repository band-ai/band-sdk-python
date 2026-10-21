"""With no ask to answer, nothing is a control and probing creates nothing."""

from __future__ import annotations

from band.testing import FakeAgentTools
from tests.adapters.opencode.conftest import ParkRoom
from tests.adapters.opencode.helpers import (
    PERMISSION_ASK,
    FakeOpencodeClient,
    client_asking,
    room_reply,
)


class TestControlRecognitionIdle:
    async def test_an_unseen_room_has_no_controls_and_stays_untouched(
        self, make_adapter, tools: FakeAgentTools
    ) -> None:
        client = FakeOpencodeClient()
        adapter = make_adapter(client)
        await adapter.on_started("OpenCode Agent", "A coding agent")

        verdicts = [
            adapter.is_control_message(room_reply(text))
            for text in ("approve req-1", "blue", "reject")
        ]

        assert verdicts == [False, False, False]
        assert adapter._rooms == {}
        assert (client.created_sessions, client.prompt_calls) == ([], [])
        assert client.registered_mcp_servers == []
        assert tools.messages_sent == []

    async def test_ordinary_work_beside_a_pending_ask_is_not_a_control(
        self, park_room: ParkRoom, tools: FakeAgentTools
    ) -> None:
        client = client_asking(PERMISSION_ASK)
        adapter = await park_room(client)
        posted_before = list(tools.messages_sent)

        assert not adapter.is_control_message(room_reply("what is the status?"))

        assert tools.messages_sent == posted_before
        assert len(client.prompt_calls) == 1
