"""Matrix smokes: build every adapter, and drive each one end to end.

Both fan across the full registry via ``@per_adapter()``. Under the baseline's
fail-never-skip policy a cell whose requirement is absent **fails with the reason**
(an absent provider key, CLI, or server) — a red cell means "this backend isn't wired
up", never a hidden skip. No single lane turns the whole matrix green (crewai needs the
dev-crewai lane; codex/opencode/letta need their backend), so run a subset with ``-k``
or rely on CI's multiple lanes.
"""

from __future__ import annotations

import pytest

from band.core.simple_adapter import SimpleAdapter

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    ResourceManager,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter()
def test_build_adapter_constructs_each(cell: AdapterCell) -> None:
    """``AdapterCell.build`` produces a ready ``SimpleAdapter`` for every cell.

    Requests ``cell`` (no provisioning), so it is the cheap wiring check before the
    live smoke below; construction makes no network call (clients are built lazily on
    first turn).
    """
    assert isinstance(cell.build(), SimpleAdapter)


@per_adapter()
@pytest.mark.flaky(
    reruns=2, rerun_except=["AssertionError"]
)  # retry a transient live-turn timeout; assertion failures fail loud
@pytest.mark.asyncio(loop_scope="session")
async def test_per_adapter_replies(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Each adapter in the matrix processes a mention and replies.

    Proves the registry drives every framework end to end: ``agent`` is the
    provisioned, running agent for the cell; mention it, barrier on the trigger being
    processed, then assert a reply landed.
    """
    room_id = await resource_manager.provision_room(
        title=f"e2e-matrix-{agent.adapter_id}", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        trigger = await user_ops.send_message(
            room_id,
            "Please reply with a short greeting.",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await capture.wait_for_reply(trigger, agent.id)
    replies.assert_present(what=f"a reply from {agent.adapter_id}")
