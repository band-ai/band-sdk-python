"""Observe Antigravity batch framing through its public stdio launch boundary."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from band.core.types import AgentInput
from tests.framework_conformance.batchprobes import BatchProbe, deliver


async def run(inp: AgentInput, final_text: str) -> str:
    from tests.adapters.antigravity_acp.peerconfig import (  # noqa: PLC0415 -- the peer imports the optional acp extra
        PeerEvent,
        PeerSettings,
    )
    from tests.adapters.antigravity_acp.runtime import (  # noqa: PLC0415 -- the peer imports the optional acp extra
        open_room,
    )

    with TemporaryDirectory(prefix="band-antigravity-batch-") as root:
        async with open_room(
            Path(root),
            PeerSettings(reply=None, text=final_text),
            config={"inject_band_tools": False},
            emit=(),
        ) as room:
            await deliver(room.adapter, inp)
            return "\n\n".join(
                str(record["text"])
                for record in room.records(PeerEvent.PROMPTED, inp.room_id)
            )


PROBE = BatchProbe(run=run)
