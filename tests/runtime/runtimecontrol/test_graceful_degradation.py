"""No adapter on_interrupt, and a custom Execution lacking the"""

from __future__ import annotations

from band.platform.link import BandLink
from band.runtime.runtime import AgentRuntime
from tests.runtime.runtimecontrol.kit import (
    control_payload,
    never_executes,
)


class TestGracefulDegradation:
    async def test_every_mode_is_a_no_op_without_the_optional_hooks(
        self, link: BandLink
    ) -> None:
        """No adapter on_interrupt, and a custom Execution lacking the
        control methods: every mode degrades to a no-op instead of raising."""

        class BareExecution:
            room_id = "r1"

        runtime = AgentRuntime(
            link=link, agent_id=link.agent_id, on_execute=never_executes
        )
        runtime.executions.update({"r1": BareExecution()})  # type: ignore[dict-item]

        for mode in ("interrupt", "stop", "play"):
            await runtime.handle_control(
                control_payload(mode, scope="room", room_id="r1")
            )
