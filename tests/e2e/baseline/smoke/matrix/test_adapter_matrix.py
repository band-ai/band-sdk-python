"""Matrix smokes: build every adapter, and drive each one end to end.

Both tests fan across the full registry via the ``adapter_id`` fixture (and, for
the live one, ``matrix_agent``). Under the baseline's fail-never-skip policy a cell
whose requirement is absent **fails with the reason** (an absent provider key, CLI,
or server) — a red cell means "this backend isn't wired up", never a hidden skip.
No single lane turns the whole matrix green (crewai needs the dev-crewai lane;
codex/opencode/letta need their backend), so run a subset with ``-k`` or rely on
CI's multiple lanes.
"""

from __future__ import annotations

import pytest

from band.core.simple_adapter import SimpleAdapter

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import build_agent
from tests.e2e.baseline.toolkit.assertions import assert_present
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


def test_build_adapter_constructs_each(
    adapter_id: str, baseline_settings: BaselineSettings
) -> None:
    """``build_adapter`` produces a ready ``SimpleAdapter`` for every cell.

    Requests only ``adapter_id`` (no provisioning), so it is the cheap wiring check
    before the live smoke below; construction makes no network call (clients are
    built lazily on first turn).
    """
    adapter = build_agent(adapter_id, baseline_settings)
    assert isinstance(adapter, SimpleAdapter)


@pytest.mark.asyncio(loop_scope="session")
async def test_matrix_agent_replies(
    adapter_id: str,
    matrix_agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Each adapter in the matrix processes a mention and replies.

    Proves the registry drives every framework end to end: ``matrix_agent`` is the
    provisioned, running agent for the cell; mention it, barrier on the trigger
    being processed, then assert a reply landed.
    """
    room_id = await resource_manager.provision_room(
        title=f"e2e-matrix-{adapter_id}", participants=[matrix_agent.id]
    )
    async with reply_capture(room_id) as capture:
        trigger = await user_ops.send_message(
            room_id,
            "Please reply with a short greeting.",
            mention_id=matrix_agent.id,
            mention_name=matrix_agent.name,
        )
        await capture.wait_for_processed(trigger, matrix_agent.id)
    assert_present(capture.messages, what=f"a reply from {adapter_id}")
