"""What the adapter submits to ``opencode serve`` names each input's sender."""

from __future__ import annotations

from band.runtime.formatters import InputField
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.opencode.helpers import (
    ALICE,
    CAROL,
    HELPER,
    FakeOpencodeClient,
    batch_input,
    deliver_turn,
    event_message_updated,
    event_session_idle,
    event_text_part,
    message_from,
)
from tests.framework_conformance.batchprobes import current_records

#: OpenCode answers the turn in text, which the adapter relays to the room.
ANSWERED_TURN = [
    event_message_updated("sess-1", "msg-1"),
    event_text_part("sess-1", "msg-1", "Done."),
    event_session_idle("sess-1"),
]


def prompted_inputs(client: FakeOpencodeClient, turn: int) -> list[tuple[str, ...]]:
    """(sender id, sender name, content) of each input the ``turn``th prompt carried."""
    (part,) = client.prompt_calls[turn]["parts"]
    return [
        (
            record[InputField.SENDER_ID],
            record[InputField.SENDER_NAME],
            record[InputField.CONTENT],
        )
        for record in current_records(part["text"])
    ]


class TestBatchPrompt:
    async def test_every_input_of_every_turn_carries_its_own_sender(
        self, make_adapter
    ) -> None:
        client = FakeOpencodeClient(prompt_event_sequences=[ANSWERED_TURN] * 2)
        adapter = make_adapter(client)
        await adapter.on_started("OpenCode Agent", "A coding agent")

        await deliver_turn(
            adapter,
            batch_input(
                [
                    message_from(ALICE, "first ask"),
                    message_from(HELPER, "a side note"),
                    message_from(ALICE, "second ask"),
                ],
                FakeAgentTools(participants=ROOM_MEMBERS),
                is_session_bootstrap=True,
            ),
        )
        await deliver_turn(
            adapter,
            batch_input(
                [message_from(CAROL, "later ask")],
                FakeAgentTools(participants=ROOM_MEMBERS),
            ),
        )

        assert [prompted_inputs(client, turn) for turn in (0, 1)] == [
            [
                (*ALICE, "first ask"),
                (*HELPER, "a side note"),
                (*ALICE, "second ask"),
            ],
            [(*CAROL, "later ask")],
        ]
        await adapter.on_cleanup("room-1")

    async def test_the_system_prompt_names_no_sender_of_any_turn(
        self, make_adapter
    ) -> None:
        client = FakeOpencodeClient(prompt_event_sequences=[ANSWERED_TURN] * 2)
        adapter = make_adapter(client)
        await adapter.on_started("OpenCode Agent", "A coding agent")

        await deliver_turn(
            adapter,
            batch_input(
                [message_from(ALICE, "first ask"), message_from(HELPER, "a note")],
                FakeAgentTools(participants=ROOM_MEMBERS),
            ),
        )
        await deliver_turn(
            adapter,
            batch_input(
                [message_from(CAROL, "later ask")],
                FakeAgentTools(participants=ROOM_MEMBERS),
            ),
        )

        identities = (*ALICE, *HELPER, *CAROL)
        assert [
            [identity in call["system"] for identity in identities]
            for call in client.prompt_calls
        ] == [[False] * len(identities)] * 2
        await adapter.on_cleanup("room-1")
