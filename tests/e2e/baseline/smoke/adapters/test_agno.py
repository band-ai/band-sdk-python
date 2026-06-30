"""Agno showcase smokes — the toolkit driving the Agno adapter live.

Agno is a ``core``-lane matrix adapter, so these are deliberately Agno-focused
(unlike the generic matrix, which runs one scenario across every adapter): they
exercise what is specific to Agno — its *native* tools. A ``ToolSpec`` handed to
the registry's Agno builder is translated into a plain Python callable on the
``agno.Agent``, which the band adapter captures and re-offers alongside the
platform tools on each run. These prove that path fires end to end against a live
platform.

Bound to ``@with_agents(Adapter.AGNO)``, so they run in the ``core`` lane (and the
full local matrix) and skip-with-reason elsewhere.

Run with:
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=core uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_agno.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, with_agents
from tests.e2e.baseline.smoke.samples.sample_tools import (
    ACCESS_CODES,
    EXECUTION_REPORTING,
    LOOKUP,
    LOOKUP_AND_WEATHER_PROMPT,
    LOOKUP_PROMPT,
    LOOKUP_TOOL,
    WEATHER,
    WEATHER_TOOL,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@with_agents(
    Adapter.AGNO, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT, **EXECUTION_REPORTING
)
@pytest.mark.flaky(reruns=2)  # a live agent turn occasionally times out; retry it
@pytest.mark.asyncio(loop_scope="session")
async def test_agno_executes_native_tool(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A ``ToolSpec`` given to Agno becomes a native agno callable that fires.

    The lookup value is opaque (it can't be guessed), so a correct code in the
    reply proves the native tool actually ran; the ``tool_call`` event confirms it
    fired with the expected argument.
    """
    room_id = await resource_manager.provision_room(
        title="e2e-agno-native-tool", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            "look up the access code for key 'alpha'",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)

    calls.assert_fired(LOOKUP, with_args={"key": "alpha"})
    capture.messages.assert_contains_any([ACCESS_CODES["alpha"]])


@with_agents(
    Adapter.AGNO,
    tools=[LOOKUP_TOOL, WEATHER_TOOL],
    prompt=LOOKUP_AND_WEATHER_PROMPT,
    **EXECUTION_REPORTING,
)
@pytest.mark.flaky(reruns=2)  # a live agent turn occasionally times out; retry it
@pytest.mark.asyncio(loop_scope="session")
async def test_agno_handles_multiple_native_tools(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Agno selects between two native tools in a single turn, firing both.

    A compound request needs a different tool for each half; both ``tool_call``
    events must show up, proving Agno dispatched to the right callable each time.
    (Only the opaque code is asserted in the reply text — forecasts get
    paraphrased, so the fired events are the reliable proof.)
    """
    room_id = await resource_manager.provision_room(
        title="e2e-agno-multi-tool", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            "look up the access code for key 'beta' and get the weather for Zorath",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)

    calls.assert_fired(LOOKUP, with_args={"key": "beta"})
    calls.assert_fired(WEATHER, with_args={"place": "Zorath"})
    capture.messages.assert_contains_any([ACCESS_CODES["beta"]])
