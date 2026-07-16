"""Proves the band-python-kit image's baked SDK reaches a live Band platform.

Locally this targets whatever .env.test points at (dev, via the always-on
VPN); in CI it targets prod via secrets — the same convention every other
live E2E test in this repo follows (see tests/e2e/baseline/README.md).
Gated behind BOTH docker_build (needs a Docker daemon) and e2e (needs live
credentials) — see tests/conftest.py.

Deliberately outside tests/e2e/baseline/'s adapter matrix: there is no
framework adapter here, just the raw SDK running inside the actual
container, so @per_adapter/@with_adapters would not fit. Provisioning and
the reply barrier reuse the baseline toolkit's fixtures directly instead.
"""

from __future__ import annotations

import pytest

from tests.docker.toolkit.docker_cli import Container, Image
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.toolkit.timeouts import backstop_timeout

SDK_PYTHON = "$BAND_SDK_PYTHON"

# tests/e2e/baseline/conftest.py auto-applies effective_timeout() to every
# test under its own directory; this test lives in tests/docker/, outside
# that scope, so the same backstop has to be applied explicitly here. Extra
# margin (vs. the default 60s) covers docker build/run/exec overhead on top
# of a normal in-process live turn.
_LIVE_TEST_TIMEOUT = backstop_timeout(BaselineSettings().e2e_timeout, extra_s=90)

# Core band-sdk only (SimpleAdapter, no framework) — matches the image's
# core-only default build (no SDK_EXTRA). Echoes every message, including the
# first: is_session_bootstrap is True for a room's first message, but this
# test's whole scenario *is* one message in a brand-new room, so there's no
# "later, real" turn to defer replying to.
ECHO_AGENT_SCRIPT = """
import asyncio
import os

from band import Agent
from band.core.simple_adapter import SimpleAdapter


class EchoAdapter(SimpleAdapter[str]):
    async def on_message(
        self, msg, tools, history, participants_msg, contacts_msg,
        *, is_session_bootstrap, room_id,
    ):
        await tools.send_message(f"echo: {msg.content}", mentions=[msg.sender_id])


async def main() -> None:
    agent = Agent.create(
        adapter=EchoAdapter(),
        agent_id=os.environ["BAND_AGENT_ID"],
        api_key=os.environ["BAND_API_KEY"],
        ws_url=os.environ["BAND_WS_URL"],
        rest_url=os.environ["BAND_REST_URL"],
    )
    await agent.run()


asyncio.run(main())
"""


@pytest.mark.docker_build
@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.timeout(_LIVE_TEST_TIMEOUT)
async def test_containerized_agent_replies_over_live_platform(
    band_python_kit_image: Image,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """An agent running the baked SDK inside the real image, over the real
    platform, actually receives a message and replies to it."""
    agent = await resource_manager.provision_agent("live-container")
    room_id = await resource_manager.provision_room(participants=[agent.id])

    with Container.run(
        band_python_kit_image,
        name_prefix="band-python-kit-live-test",
        env={
            "BAND_AGENT_ID": agent.id,
            "BAND_API_KEY": agent.api_key,
            "BAND_WS_URL": baseline_settings.endpoints.ws_url,
            "BAND_REST_URL": baseline_settings.endpoints.rest_url,
        },
    ) as container:
        container.run_python_background(
            ECHO_AGENT_SCRIPT, interpreter=SDK_PYTHON, user="agent"
        )

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
