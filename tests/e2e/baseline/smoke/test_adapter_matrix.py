"""Matrix smokes: build every adapter, and drive each one end to end.

The matrix is the registry rendered as ``pytest.param``s (``adapter_params()``),
so these run across the full discovered adapter set. Under the baseline's
fail-never-skip policy a cell whose requirement is absent **fails with the reason**
(an absent provider key, CLI, or server) — a red cell means "this backend isn't
wired up", never a hidden skip. No single lane turns the whole matrix green
(crewai needs the dev-crewai lane; codex/opencode/letta need their backend), so run
a subset with ``-k`` or rely on CI's multiple lanes.
"""

from __future__ import annotations


import pytest

from band.core.simple_adapter import SimpleAdapter

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.assertions import assert_present
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

from tests.e2e.baseline.agents import MatrixAgent, adapter_params
from tests.e2e.baseline.smoke.sample_agents import build_agent


@pytest.mark.parametrize("adapter_id", adapter_params())
def test_build_adapter_constructs_each(
    adapter_id: str, baseline_settings: BaselineSettings
) -> None:
    """``build_adapter`` produces a ready ``SimpleAdapter`` for every cell.

    Construction makes no network call (clients are built lazily on first turn),
    so this is the cheap wiring check before the live smoke below.
    """
    adapter = build_agent(adapter_id, baseline_settings)
    assert isinstance(adapter, SimpleAdapter)


@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_matrix_agent_replies(
    matrix_agent: MatrixAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Each adapter in the matrix processes a mention and replies.

    Proves the registry drives every framework end to end: provision + run the
    agent (via the parametrized ``matrix_agent`` fixture), mention it,
    barrier on the trigger being processed, then assert a reply landed.
    """
    adapter_id, provisioned = matrix_agent
    room_id = await resource_manager.provision_room(
        title=f"e2e-matrix-{adapter_id}", participants=[provisioned.id]
    )
    async with reply_capture(room_id) as capture:
        trigger = await user_ops.send_message(
            room_id,
            "Please reply with a short greeting.",
            mention_id=provisioned.id,
            mention_name=provisioned.name,
        )
        await capture.wait_for_processed(trigger, provisioned.id)
    assert_present(capture.messages, what=f"a reply from {adapter_id}")
