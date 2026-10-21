"""Room updates the runtime hands a message reach Claude as system lines
ahead of the message itself."""

from __future__ import annotations

from band.runtime.prompts import system_updates
from tests.adapters.claude_sdk.helpers import OpenRoom
from tests.framework_conformance.batchprobes import current_records

JOINED = "Alice joined the room"
CONTACTED = "Carol is now a contact"


class TestPromptFraming:
    async def test_participant_and_contact_updates_precede_the_message(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("hi all")])

        await room.send(
            "hello",
            participants_msg=JOINED,
            contacts_msg=CONTACTED,
        )

        participants, contacts, message = room.claude.prompts[-1].split("\n\n")
        assert [participants, contacts] == system_updates(JOINED, CONTACTED)
        assert [record["content"] for record in current_records(message)] == ["hello"]
