"""Drive Antigravity seed outcomes through its public stdio launch boundary."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.resumeprobes.probe import SeedOutcome
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input


def seed_failure() -> type[BaseException]:
    """Identify the peer's injected provider error."""
    from acp.exceptions import (  # noqa: PLC0415 -- the acp extra is absent from other dependency lanes
        RequestError,
    )

    return RequestError


async def seed_antigravity(outcome: SeedOutcome, tools: FakeAgentTools) -> str:
    from tests.adapters.antigravity_acp.peerconfig import (  # noqa: PLC0415 -- the peer imports the optional acp extra
        PeerEvent,
        PeerSettings,
    )
    from tests.adapters.antigravity_acp.runtime import (  # noqa: PLC0415 -- the peer imports the optional acp extra
        open_room,
    )

    settings = {
        SeedOutcome.CONFIRMED: PeerSettings(),
        SeedOutcome.FAILED: PeerSettings(reply=None, prompt_error=True),
    }[outcome]
    with TemporaryDirectory(prefix="band-antigravity-resume-") as root:
        async with open_room(Path(root), settings, emit=()) as room:
            await room.adapter.on_event(turn_input(tools))
            [created] = room.records(PeerEvent.CREATED, ROOM_ID)
            return str(created["session"])
