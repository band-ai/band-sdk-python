"""Room updates the runtime hands a message reach Claude as system lines
ahead of the message itself."""

from __future__ import annotations

from tests.adapters.claude_sdk.helpers import OpenRoom


class TestPromptFraming:
    async def test_participant_and_contact_updates_precede_the_message(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("hi all")])

        await room.send(
            "hello",
            participants_msg="Alice joined the room",
            contacts_msg="Carol is now a contact",
        )

        participants, contacts, message = room.claude.prompts[-1].split("\n\n")
        assert (participants, contacts) == (
            "[System]: Alice joined the room",
            "[System]: Carol is now a contact",
        )
        assert message.endswith("hello")
