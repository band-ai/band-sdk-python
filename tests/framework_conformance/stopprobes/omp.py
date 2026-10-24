"""Backend-stop probes for the native OMP adapter; see ``stopprobes``.

The adapter keeps no ``backend_turns``: its backend turn is one RPC prompt
awaited inside the handler. Ending the turn cancels that prompt, whose client
aborts it, and the adapter then closes the room's OMP process so no turn keeps
running behind the room. The fake replaces only the OMP process and records
both.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from pydantic import JsonValue

from band.client.streaming import ControlMode
from band.integrations.ompwire import OmpCommandName
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    cancel_turn,
    wait_until,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

if TYPE_CHECKING:
    from band.integrations.ompwire import (
        OmpEvent,
        OmpHostToolCall,
        OmpToolResult,
        OmpUIRequest,
        OmpUIResponse,
    )

PROMPT_CANCELLED = "prompt cancelled"
CLOSED = "closed"


class HeldOmpClient:
    """An OMP process whose prompt runs until it is cancelled."""

    def __init__(self) -> None:
        self.journal: list[str] = []
        self.prompting = False

    async def start(self) -> None:
        pass

    async def request(
        self, command: str, **parameters: JsonValue
    ) -> dict[str, JsonValue]:
        match command:
            case OmpCommandName.GET_STATE:
                return {"sessionId": "stop-probe-session", "isSettled": True}
            case OmpCommandName.SET_ASK_DIALOG:
                return {"enabled": True}
            case OmpCommandName.SET_HOST_TOOLS:
                return {}
            case _:
                raise AssertionError(f"Unexpected OMP command: {command}")

    async def prompt(
        self,
        message: str,
        *,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        del message, on_event, on_tool, on_ui
        self.prompting = True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.journal.append(PROMPT_CANCELLED)
            raise

    async def close(self) -> None:
        self.journal.append(CLOSED)


async def run_probe(mode: ControlMode | None) -> list[str]:
    """Run a turn and end it with ``mode``; ``None`` stops an idle room."""
    from band.adapters.omp import (  # noqa: PLC0415 -- the omp extra is absent from other adapter lanes
        OmpAdapter,
        OmpAdapterConfig,
    )

    client = HeldOmpClient()
    with (
        tempfile.TemporaryDirectory(prefix="band-stop-omp-") as workspace,
        patch("band.adapters.omp.OmpRpcClient", return_value=client),
        patch("band.adapters.omp.omp_mcp_ready", new=AsyncMock()),
    ):
        adapter = OmpAdapter(OmpAdapterConfig(cwd=workspace))
        await adapter.on_started("Agent", "A coding agent")
        try:
            if mode is None:
                await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
            else:
                turn = asyncio.create_task(
                    adapter.on_event(turn_input(FakeAgentTools()))
                )
                await wait_until(lambda: client.prompting)
                await cancel_turn(turn)
                await adapter.on_interrupt(ROOM_ID, mode)
            return list(client.journal)
        finally:
            await adapter.cleanup_all()


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: run_probe(mode),
            expected=[PROMPT_CANCELLED, CLOSED],
        )
        for mode in ENDING_CONTROLS
    },
    "idle-room": StopCase(run=lambda: run_probe(None), expected=[]),
}
