"""A batch turn announces approvals to its own senders and keeps the room's
one-turn-at-a-time rule."""

from __future__ import annotations

import pytest

from band.adapters.opencode.approvals.wording import (
    APPROVAL_HANDLED_TEMPLATE,
    APPROVAL_REQUESTED_TEMPLATE,
)
from band.core.exceptions import InputNotAccepted
from band.testing import FakeAgentTools
from tests.adapters.opencode.conftest import ParkRoom
from tests.adapters.opencode.helpers import (
    ALICE,
    CAROL,
    HELPER,
    PERMISSION_ASK,
    FakeOpencodeClient,
    batch_input,
    deliver_turn,
    event_permission,
    handle_control,
    message_from,
    room_reply,
)


def approval_request(request_id: str) -> str:
    """The room notice OpenCode's scripted ``bash`` ask for ``request_id`` becomes."""
    return APPROVAL_REQUESTED_TEMPLATE.format(
        permission="bash", patterns="rm -rf tmp", request_id=request_id
    )


def audiences(tools: FakeAgentTools) -> dict[str, list[str]]:
    """Each notice the turn posted, with the participant ids it mentions."""
    return {
        post["content"]: [mention["id"] for mention in post["mentions"]]
        for post in tools.messages_sent
    }


class TestBatchTurn:
    async def test_an_approval_ask_goes_to_each_distinct_sender_once(
        self, park_room: ParkRoom, tools: FakeAgentTools
    ) -> None:
        await park_room(
            FakeOpencodeClient(prompt_event_sequences=[[PERMISSION_ASK]]),
            message_from(ALICE, "first ask"),
            message_from(HELPER, "a side note"),
            message_from(ALICE, "second ask"),
        )

        assert audiences(tools) == {approval_request("req-1"): [ALICE[0], HELPER[0]]}

    async def test_a_later_ask_still_goes_to_the_turn_senders_after_another_replied(
        self, park_room: ParkRoom, tools: FakeAgentTools
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[[PERMISSION_ASK]],
            reply_permission_events={"req-1": [event_permission("sess-1", "req-2")]},
        )
        adapter = await park_room(
            client,
            message_from(ALICE, "first ask"),
            message_from(HELPER, "a side note"),
        )

        assert await handle_control(adapter, room_reply("approve req-1", CAROL[0]))
        await tools.until_said(approval_request("req-2"))

        assert audiences(tools) == {
            approval_request("req-1"): [ALICE[0], HELPER[0]],
            APPROVAL_HANDLED_TEMPLATE.format(request_id="req-1", reply="once"): [
                CAROL[0]
            ],
            approval_request("req-2"): [ALICE[0], HELPER[0]],
        }

    async def test_a_batch_is_not_accepted_while_the_room_turn_waits_on_a_human(
        self, park_room: ParkRoom
    ) -> None:
        client = FakeOpencodeClient(prompt_event_sequences=[[PERMISSION_ASK]])
        adapter = await park_room(client)
        busy_tools = FakeAgentTools()

        with pytest.raises(InputNotAccepted):
            await deliver_turn(
                adapter,
                batch_input(
                    [message_from(ALICE, "one more"), message_from(CAROL, "and this")],
                    busy_tools,
                ),
            )

        assert len(client.prompt_calls) == 1
        assert busy_tools.messages_sent == []
        assert not busy_tools.turn.complete
