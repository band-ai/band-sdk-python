"""Control-signal payloads and real room executions observed under control."""

from __future__ import annotations

from band.client.streaming import AgentControlPayload, ControlMode
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext, ResyncRequest
from band.runtime.runtime import AgentRuntime


def control_payload(mode: str, scope: str = "agent", **kw) -> AgentControlPayload:
    return AgentControlPayload(mode=mode, scope=scope, agent_id="agent-123", **kw)


async def never_executes(_context: ExecutionContext, event: object) -> None:
    raise AssertionError(f"No cycle should run, got {event!r}")


class ControlledRooms:
    """Real idle room executions under one runtime, observed the way an
    adapter sees control: the aborts its ``on_interrupt`` hears, and the
    catch-ups a play queues."""

    def __init__(self, link: BandLink, *room_ids: str) -> None:
        self.heard: list[tuple[str, ControlMode]] = []
        self.runtime = AgentRuntime(
            link=link,
            agent_id=link.agent_id,
            on_execute=never_executes,
            on_control=self.on_interrupt,
        )
        self.executions = {
            room_id: ExecutionContext(
                room_id, link, never_executes, agent_id=link.agent_id
            )
            for room_id in room_ids
        }
        self.runtime.executions.update(self.executions)

    async def on_interrupt(self, room_id: str, mode: ControlMode) -> None:
        self.heard.append((room_id, mode))

    async def signal(self, mode: str, **payload: object) -> None:
        await self.runtime.handle_control(control_payload(mode, **payload))

    def resyncs(self, room_id: str) -> int:
        """Drain the room's queue, counting the /next catch-ups requested."""
        queue = self.executions[room_id].queue
        drained = [queue.get_nowait() for _ in range(queue.qsize())]
        return sum(isinstance(item, ResyncRequest) for item in drained)
