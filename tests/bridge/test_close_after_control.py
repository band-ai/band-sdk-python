"""``AgentRunner.close()`` cancels a play nudge spawned while its link is still
disconnecting: the link applies control signals until ``disconnect()`` returns."""

from __future__ import annotations

import asyncio

from band.client.streaming import AgentControlPayload
from tests.bridge.kit import build_runner


class TestCloseAfterControl:
    async def test_a_play_applied_during_disconnect_never_outlives_close(
        self,
    ) -> None:
        runner, _, link = build_runner()
        story: list[str] = []
        nudge_started = asyncio.Event()

        async def next_message(room_id: str) -> None:
            story.append(f"nudge {room_id}")
            nudge_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                story.append(f"nudge {room_id} cancelled")
                raise

        async def disconnect() -> None:
            """A PLAY queued in the link's control lane applies before it ends."""
            await runner._control.handle(
                AgentControlPayload(
                    mode="play", scope="room", agent_id="agent-1", room_id="r1"
                )
            )
            await nudge_started.wait()

        link.get_next_message.side_effect = next_message
        link.disconnect.side_effect = disconnect

        await asyncio.wait_for(runner.close(), timeout=5.0)

        assert story == ["nudge r1", "nudge r1 cancelled"]
