"""A proxy-managed sandbox agent completes turns under one owner and after a restart.

Completed echo turns prove the proxy replaced the sentinel with a working key
on the wire (REST + WS), which the custody smokes cannot.
"""

from __future__ import annotations

import pytest

from tests.docker.toolkit.docker_cli import BUILD_TIMEOUT_S
from tests.docker.toolkit.sandbox_agent import workload_posture
from tests.docker.toolkit.sbx_cli import CREATE_TIMEOUT_S, Sandbox
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps

OWNED_TURNS = 3


@lane(Lane.KIT)
@requires(Dep.DOCKER, Dep.SBX)
@pytest.mark.timeout(extra=BUILD_TIMEOUT_S + CREATE_TIMEOUT_S)
# The session WebSocket observer lives on the session loop.
@pytest.mark.asyncio(loop_scope="session")
class TestOwnedSandboxSession:
    async def test_owned_session_completes_turns_and_restarts(
        self,
        round_trip_sandbox: tuple[ProvisionedAgent, str, Sandbox],
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        agent, room_id, sandbox = round_trip_sandbox

        async def completed_echo(message: str) -> None:
            async with reply_capture(room_id) as capture:
                mid = await user_ops.send_message(
                    room_id, message, mention_id=agent.id, mention_name=agent.name
                )
                replies = await capture.wait_for_reply(mid, agent.id)
            replies.assert_contains_any(["echo:"])
            replies.assert_contains_any([message])

        with sandbox.owned_run():
            assert workload_posture(sandbox) == ("1000", "200", "403")
            for turn in range(1, OWNED_TURNS + 1):
                await completed_echo(f"owned-session-{turn}")
        sandbox.stop()
        with sandbox.owned_run():
            await completed_echo("after-restart")
