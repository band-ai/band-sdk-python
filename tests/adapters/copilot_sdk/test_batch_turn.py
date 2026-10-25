"""A batch turn reaches the room's Copilot session like any other turn."""

from __future__ import annotations

import pytest

from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    make_started_adapter,
    requires_copilot_sdk,
)
from tests.framework_conformance.batchprobes import (
    ALICE_FIRST,
    BATCH,
    deliver,
    delivery_input,
)

pytestmark = requires_copilot_sdk

HISTORY_HEADING = "[Previous conversation context:]"


class TestBatchTurn:
    @pytest.mark.asyncio
    async def test_a_batch_is_one_prompt_to_one_session(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)

        await deliver(adapter, await delivery_input(BATCH))

        assert [len(session.prompts) for session in client.sessions] == [1]

    @pytest.mark.asyncio
    async def test_the_next_turn_continues_the_session_without_replaying_history(
        self,
    ):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)

        await deliver(adapter, await delivery_input(BATCH))
        await deliver(adapter, await delivery_input([ALICE_FIRST]))

        (session,) = client.sessions
        assert [HISTORY_HEADING in prompt for prompt in session.prompts] == [
            True,
            False,
        ]
