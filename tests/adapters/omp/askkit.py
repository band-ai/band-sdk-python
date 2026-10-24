"""Turn drivers and wire observations for native room asks."""

from __future__ import annotations

from pydantic import JsonValue

from band.adapters.omp import OmpAdapter
from band.converters.omp import OmpSessionState
from band.integrations.ompwire import OmpCommandName, OmpFrameType
from band.testing import FakeAgentTools
from tests.integrations.acp.conftest import make_platform_message
from tests.integrations.omprpc.kit import checkpoint_frames


async def ask_turn(
    adapter: OmpAdapter,
    tools: FakeAgentTools,
    *,
    bootstrap: bool = True,
    content: str = "Ask me which database to use",
) -> None:
    await adapter.run_judged_turn(
        msg=make_platform_message(content),
        tools=tools,
        history=OmpSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id="room-123",
    )


async def ask_frames(
    adapter: OmpAdapter, frame_type: OmpCommandName | OmpFrameType
) -> list[dict[str, JsonValue]]:
    frames = await checkpoint_frames(adapter._rooms["room-123"].client)
    return [frame for frame in frames if frame["type"] == frame_type]
