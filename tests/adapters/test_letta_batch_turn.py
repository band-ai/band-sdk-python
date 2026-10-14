"""A batch turn reaches the Letta agent as one message."""

from __future__ import annotations

import pytest

from tests.adapters.lettakit import (
    make_assistant_message,
    make_letta_response,
    ready_letta_adapter,
)
from tests.framework_conformance.batchprobes import BATCH, deliver, delivery_input
from tests.runtime.batching.kit import ROOM_ID


class TestLettaBatchTurn:
    @pytest.mark.asyncio
    async def test_a_batch_is_one_user_message_to_the_agent(self) -> None:
        adapter, client = ready_letta_adapter(room_id=ROOM_ID)
        client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Done.")
        )

        await deliver(adapter, await delivery_input(BATCH))

        client.agents.messages.create.assert_awaited_once()
        sent = client.agents.messages.create.await_args.kwargs["messages"]
        assert [message["role"] for message in sent] == ["user"]
