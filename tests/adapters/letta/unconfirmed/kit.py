"""A ready Letta adapter over a recording server, per streaming mode."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import pytest

from band.adapters.letta import LettaAdapter, LettaAdapterConfig
from band.core.exceptions import ProviderUnavailableError
from band.testing import FakeAgentTools
from tests.adapters.lettakit import RecordingLettaClient, ready_letta_adapter
from tests.framework_conformance.turnprobes import ALICE, ROOM_ID, turn_input

AGENT_ID = "agent-1"
CONVERSATION_ID = "conversation-1"


class StreamingMode(StrEnum):
    """Which Letta endpoint carries the room's turn."""

    PER_ROOM = "per_room"
    SHARED = "shared"

    @property
    def sent(self) -> str:
        """The journal entry a turn sent through this mode leaves."""
        return (
            f"send agent {AGENT_ID}"
            if self is StreamingMode.PER_ROOM
            else f"send conversation {CONVERSATION_ID}"
        )

    @property
    def cancel(self) -> str:
        """The journal entry a run cancel through this mode leaves."""
        return (
            f"cancel agent {AGENT_ID}"
            if self is StreamingMode.PER_ROOM
            else f"cancel conversation {CONVERSATION_ID}"
        )


MODES = [pytest.param(mode, id=mode.value) for mode in StreamingMode]


def adapter_over_recording_server(
    mode: StreamingMode, *, room_id: str = ROOM_ID, **settings: Any
) -> tuple[LettaAdapter, RecordingLettaClient]:
    adapter, _ = ready_letta_adapter(
        LettaAdapterConfig(mode=mode.value, **settings), room_id=room_id
    )
    client = RecordingLettaClient()
    adapter._client = client
    room = adapter._rooms[room_id]
    room.agent_id = AGENT_ID
    if mode is StreamingMode.SHARED:
        room.conversation_id = CONVERSATION_ID
    return adapter, client


def new_tools() -> FakeAgentTools:
    return FakeAgentTools(participants=[ALICE])


async def refused_as_unavailable(adapter: LettaAdapter) -> FakeAgentTools:
    """Deliver the room's next input and require its terminal refusal."""
    tools = new_tools()
    with pytest.raises(ProviderUnavailableError):
        await adapter.on_event(turn_input(tools))
    return tools
