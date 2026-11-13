"""With only a user key, `band-kit provision` registers a fresh agent that starts
in its proxy-managed sandbox, and re-provisioning never duplicates it.

``band.docker.provision.run()`` runs in-process so the test exercises the exact
orchestration without the packaged console script. The never-in-VM proofs are
``test_proxy_custody.py``'s; this covers the registration step in front of them.
"""

from __future__ import annotations

import argparse

import pytest

from band.docker.provision import run as provision_run
from tests.docker.toolkit.docker_cli import BUILD_TIMEOUT_S
from tests.docker.toolkit.sbx_cli import CREATE_TIMEOUT_S
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.user_ops import UserOps


@lane(Lane.KIT)
@requires(Dep.DOCKER, Dep.SBX)
@pytest.mark.timeout(extra=BUILD_TIMEOUT_S + CREATE_TIMEOUT_S)
# The session WebSocket observer lives on the session loop.
@pytest.mark.asyncio(loop_scope="session")
class TestSelfRegistration:
    async def test_self_registered_agent_round_trips_with_no_duplicate_on_reprovision(
        self,
        self_registered_agent: tuple[str, str, argparse.Namespace],
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        agent_id, room_id, args = self_registered_agent

        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id, "ping", mention_id=agent_id, mention_name=agent_id
            )
            replies = await capture.wait_for_reply(mid, agent_id)
        replies.assert_contains_any(["echo:"])
        replies.assert_contains_any(["ping"])

        # A duplicate registration would 422 (same name) or mint a new id; the
        # identical id means the idempotency guard skipped registration.
        assert await provision_run(args) == agent_id
