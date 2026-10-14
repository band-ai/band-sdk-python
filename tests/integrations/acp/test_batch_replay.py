"""A session minted after bootstrap replays history up to a batch's earliest input."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest

from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.batchprobes import (
    ALICE_FIRST,
    ALICE_SECOND,
    EARLIER_ANSWER,
    EARLIER_QUESTION,
    HELPER,
    LATER_PENDING,
    STORED_ROOM,
    Original,
    deliver,
    delivery_input,
    stored_message,
)
from tests.framework_conformance.batchprobes.acp import ADAPTERS
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import started_acp_adapter
from tests.runtime.helpers import ROOM_ID

# Alice's two inputs with a message of someone else's between them.
AROUND_HELPER = (ALICE_FIRST, ALICE_SECOND)


async def replayed_prompt(
    framework_id: str, originals: Sequence[Original], room: Sequence[Original]
) -> str:
    """The only prompt a fresh session gets for ``originals`` when the room
    already holds ``room``: the session is minted after bootstrap, so its
    history is re-fetched from the room."""
    agent = FakeACPAgent().will_say("ok")
    tools = FakeAgentTools(room_id=ROOM_ID)
    tools.set_room_context(
        [{**stored_message(o), "message_type": "text"} for o in room]
    )
    inp = replace(
        await delivery_input(originals), tools=tools, is_session_bootstrap=False
    )

    async with started_acp_adapter(ADAPTERS[framework_id](), agent) as session:
        await deliver(session.adapter, inp)

    (prompt,) = agent.prompt_texts()
    return prompt


def times_shown(prompt: str, *texts: str) -> dict[str, int]:
    return {text: prompt.count(text) for text in texts}


@pytest.mark.parametrize("framework_id", sorted(ADAPTERS))
class TestACPBatchReplay:
    async def test_history_stops_before_the_earliest_input_and_skips_what_follows_it(
        self, framework_id: str
    ) -> None:
        prompt = await replayed_prompt(framework_id, AROUND_HELPER, STORED_ROOM)

        assert times_shown(
            prompt,
            EARLIER_QUESTION,
            EARLIER_ANSWER,
            ALICE_FIRST.content,
            HELPER.content,
            ALICE_SECOND.content,
            LATER_PENDING,
        ) == {
            EARLIER_QUESTION: 1,
            EARLIER_ANSWER: 1,
            ALICE_FIRST.content: 1,
            HELPER.content: 0,
            ALICE_SECOND.content: 1,
            LATER_PENDING: 0,
        }

    async def test_no_input_slips_into_history_when_the_room_lacks_the_earliest(
        self, framework_id: str
    ) -> None:
        room_without_earliest = [o for o in STORED_ROOM if o.id != ALICE_FIRST.id]

        prompt = await replayed_prompt(
            framework_id, AROUND_HELPER, room_without_earliest
        )

        assert times_shown(prompt, ALICE_FIRST.content, ALICE_SECOND.content) == {
            ALICE_FIRST.content: 1,
            ALICE_SECOND.content: 1,
        }
