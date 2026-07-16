"""Proves the band-python-kit image's baked SDK reaches a live Band platform.

Locally this targets whatever .env.test points at (dev, via the always-on
VPN); in CI it targets prod via secrets — the same convention every other
live E2E test in this repo follows (see tests/e2e/baseline/README.md).
Gated behind BOTH docker_build (needs a Docker daemon) and e2e (needs live
credentials) — see tests/conftest.py.

Deliberately outside tests/e2e/baseline/'s adapter matrix: there is no
framework adapter here, just the raw SDK running inside the actual
container, so @per_adapter/@with_adapters would not fit. Provisioning,
the container, and its echo agent are fixture-only (tests/docker/conftest.py
/ tests/docker/toolkit/live_agent.py) — this file is scenario only.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.toolkit.timeouts import backstop_timeout

# tests/e2e/baseline/conftest.py auto-applies effective_timeout() to every
# test under its own directory; this test lives in tests/docker/, outside
# that scope, so the same backstop has to be applied explicitly here. Extra
# margin (vs. the default 60s) covers docker build/run/exec overhead on top
# of a normal in-process live turn.
_LIVE_TEST_TIMEOUT = backstop_timeout(BaselineSettings().e2e_timeout, extra_s=90)


@pytest.mark.docker_build
@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.timeout(_LIVE_TEST_TIMEOUT)
async def test_containerized_agent_replies_over_live_platform(
    live_containerized_agent: tuple[ProvisionedAgent, str],
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """An agent running the baked SDK inside the real image, over the real
    platform, actually receives a message and replies to it."""
    agent, room_id = live_containerized_agent

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id, "ping", mention_id=agent.id, mention_name=agent.name
        )
        replies = await capture.wait_for_reply(mid, agent.id)

    # msg.content carries the platform's literal mention token ahead of the
    # text (e.g. "@[[agent-id]]/agent-name ping"), not just the bare text, so
    # match on the echo prefix and the original text separately.
    replies.assert_contains_any(["echo:"])
    replies.assert_contains_any(["ping"])
