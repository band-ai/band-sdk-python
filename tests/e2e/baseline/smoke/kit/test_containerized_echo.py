"""The band-python-kit image's baked SDK reaches the live platform.

No framework adapter here — just the raw SDK running inside the real container
as the non-root ``agent`` user, so it is pinned to the kit lane.
"""

from __future__ import annotations

import pytest

from tests.docker.toolkit.docker_cli import BUILD_TIMEOUT_S
from tests.docker.toolkit.live_agent import assert_echoed
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.toolkit.timeouts import TIMEOUT_BACKSTOP_MARGIN_S


@lane(Lane.KIT)
@requires(Dep.DOCKER)
# The session's first kit smoke pays the cold image build.
@pytest.mark.timeout(extra=BUILD_TIMEOUT_S + TIMEOUT_BACKSTOP_MARGIN_S)
# The session WebSocket observer lives on the session loop.
@pytest.mark.asyncio(loop_scope="session")
class TestContainerizedEcho:
    async def test_containerized_agent_replies_over_live_platform(
        self,
        live_containerized_agent: tuple[ProvisionedAgent, str],
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        agent, room_id = live_containerized_agent

        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id, "ping", mention_id=agent.id, mention_name=agent.name
            )
            replies = await capture.wait_for_reply(mid, agent.id)

        assert_echoed(replies, "ping")
