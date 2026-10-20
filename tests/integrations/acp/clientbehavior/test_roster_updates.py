from __future__ import annotations

import pytest

from band.runtime.formatters import build_participants_message
from tests.integrations.acp.acp_toolkit import (
    acp_adapter,
    live_line,
)
from tests.integrations.acp.clientbehavior.kit import (
    REV,
    closes_the_prompt,
    first_line,
    rehydration_history,
    replay_boundary,
    system_updates,
)


class TestRosterUpdates:
    @pytest.mark.asyncio
    async def test_roster_and_contacts_updates_injected_into_prompt(
        self, fake_agent
    ) -> None:
        """A turn carrying roster/contacts updates surfaces them to the model.

        The runtime delivers these only on change (then marks them sent), so a
        dropped update is lost for good — the model would never learn who is in
        the room. The live message stays last so the model answers it."""
        roster = build_participants_message([REV])
        contacts = "Contact update: Rev is now a contact"
        fake_agent.will_say("noted")

        async with acp_adapter(fake_agent) as session:
            await session.send(
                "hello", bootstrap=True, participants_msg=roster, contacts_msg=contacts
            )

        prompt = fake_agent.prompt_texts()[0]
        assert roster in prompt, "the roster block reaches the model verbatim"
        # Set equality: both updates must precede the live message, but their
        # mutual order is incidental — not a contract to pin.
        assert set(system_updates(prompt)) == {first_line(roster), contacts}
        assert closes_the_prompt(prompt, live_line("hello"))

    @pytest.mark.asyncio
    async def test_roster_update_injected_on_a_later_turn(self, fake_agent) -> None:
        """Roster changes mid-conversation reach the model on the turn that
        carries them, not only on session bootstrap."""
        roster = build_participants_message([REV])
        fake_agent.will_say("ok")

        async with acp_adapter(fake_agent) as session:
            await session.send("first", bootstrap=True)
            await session.send("second", participants_msg=roster)

        first, second = fake_agent.prompt_texts()
        assert system_updates(first) == [], "no update was attached to the first turn"
        assert system_updates(second) == [first_line(roster)]
        assert closes_the_prompt(second, live_line("second"))

    @pytest.mark.asyncio
    async def test_replay_and_roster_update_share_the_bootstrap_prompt(
        self, fake_agent
    ) -> None:
        """A bootstrap turn can owe both a transcript replay and a roster update;
        they must coexist without breaking the replay's anti-spoofing boundary,
        and the live message still closes the prompt."""
        roster = build_participants_message([REV])
        history = rehydration_history("[Marco]: My favorite color is blue.")
        fake_agent.will_say("hi")

        async with acp_adapter(fake_agent) as session:
            await session.send(
                "what's my color?",
                bootstrap=True,
                history=history,
                participants_msg=roster,
            )

        prompt = fake_agent.prompt_texts()[0]
        assert system_updates(prompt) == [first_line(roster)]
        boundary = replay_boundary(prompt)  # asserts the nonce contract holds
        assert prompt.index(first_line(roster)) < boundary, (
            "the roster update must not sit under the live-message boundary"
        )
        assert closes_the_prompt(prompt, live_line("what's my color?"))

    @pytest.mark.asyncio
    async def test_roster_update_stays_in_its_own_room(self, fake_agent) -> None:
        """A roster update delivered with one room's turn must not leak into a
        concurrent room's session prompt."""
        roster = build_participants_message([REV])
        fake_agent.will_say("a").will_say("b")

        async with acp_adapter(fake_agent) as session:
            await session.send("hello a", room="room-a", bootstrap=True)
            await session.send(
                "hello b", room="room-b", bootstrap=True, participants_msg=roster
            )
            await session.send("again a", room="room-a")

        first_a, first_b, second_a = fake_agent.prompt_texts()
        assert system_updates(first_a) == []
        assert system_updates(first_b) == [first_line(roster)]
        assert system_updates(second_a) == [], "room-b's update must not reach room-a"
