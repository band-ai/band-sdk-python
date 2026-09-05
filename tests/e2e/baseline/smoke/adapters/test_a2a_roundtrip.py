"""Full A2A round-trip smoke: Band Agent A -> ``A2AAdapter`` -> a live
gateway -> Band Agent B (Anthropic), and the reply flowing all the way back.

Reuses ``test_a2a_gateway.py``'s target-peer + gateway setup, but swaps the
raw a2a-sdk reference client for a live ``A2AAdapter`` as the caller --
proving the realistic Band-to-Band usage pattern end to end: a Band room
message forwarded over real A2A JSON-RPC to a gateway-exposed Band peer, with
that peer's real LLM reply relayed all the way back into Agent A's own room.

``@with_adapters(Adapter.ANTHROPIC)`` (for the target peer B) also derives
this smoke's home lane (``core``), so no ``@lane`` pin is needed -- the
gateway and the caller ``A2AAdapter`` are themselves protocol bridges
(``NON_AGENT_ADAPTERS``) built bespoke, same as ``test_a2a_gateway.py``.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_a2a_roundtrip.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from band.integrations.a2a import A2AAdapter
from band.integrations.a2a.gateway import A2AGatewayAdapter

from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.ports import reserve_port

_SHORT = "You are a friendly assistant in a chat room. Reply in one short sentence."


@with_adapters(Adapter.ANTHROPIC, prompt=_SHORT)
@flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
# Two relayed hops (Agent A -> gateway -> Agent B) plus one real LLM turn on
# top of a gateway + two adapter cold starts: 240s outer, leaving 120s
# overhead beyond the 2x e2e_timeout barrier deadline below.
@pytest.mark.timeout(extra=240)
@pytest.mark.asyncio(loop_scope="session")
async def test_band_to_band_round_trip_over_real_a2a(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """Band Agent A relays a room message through a live A2A gateway to Band
    Agent B (a real Anthropic turn) and posts B's reply back into A's room."""
    port = reserve_port()
    gateway = A2AGatewayAdapter(gateway_url=f"http://127.0.0.1:{port}", port=port)

    async with running_provisioned_agent(gateway, resource_manager, label="a2a-gw"):
        caller = A2AAdapter(
            remote_url=f"http://127.0.0.1:{port}/agents/{agent.id}", streaming=True
        )
        async with running_provisioned_agent(
            caller, resource_manager, label="a2a-caller"
        ) as caller_agent:
            room_id = await resource_manager.provision_room(
                title="e2e-a2a-roundtrip", participants=[caller_agent.id]
            )
            async with reply_capture(room_id) as capture:
                mid = await user_ops.send_message(
                    room_id,
                    "Please say hello.",
                    mention_id=caller_agent.id,
                    mention_name=caller_agent.name,
                )
                replies = await capture.wait_for_reply(
                    mid,
                    caller_agent.id,
                    deadline_s=baseline_settings.e2e_timeout * 2,
                )

    replies.assert_present(
        what="a reply relayed over a live Band-to-Band A2A round trip"
    )
