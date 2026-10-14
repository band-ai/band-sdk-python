"""Batch-input probe for the Letta adapter; see ``batchprobes``.

The room has no Letta agent yet, so the turn creates one and seeds it with the
room's history: that cold-boot message is what reaches the provider.
"""

from __future__ import annotations

from typing import Any

import pytest

from band.core.types import AgentInput
from tests.framework_conformance.batchprobes import BatchProbe, deliver
from tests.runtime.batching.kit import ROOM_ID


async def run_letta(inp: AgentInput, final_text: str) -> str:
    pytest.importorskip("letta_client")
    from tests.adapters.lettakit import (  # noqa: PLC0415 -- letta extra, absent from some lane venvs
        make_mock_agent,
        ready_letta_adapter,
        scripted_letta_turn,
    )

    adapter, client = ready_letta_adapter(room_id=ROOM_ID)
    adapter._rooms.clear()
    client.agents.create.return_value = make_mock_agent()
    scripted = scripted_letta_turn(adapter, room_id=ROOM_ID, final_text=final_text)
    received: list[str] = []

    async def create(**request: Any) -> Any:
        received.extend(message["content"] for message in request["messages"])
        return await scripted(**request)

    client.agents.messages.create.side_effect = create
    await deliver(adapter, inp)
    return "\n".join(received)


PROBES: dict[str, BatchProbe] = {"letta": BatchProbe(run=run_letta, relays=True)}
