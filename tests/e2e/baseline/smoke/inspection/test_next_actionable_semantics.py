"""Guards the /next actionable-set semantics the stop->play feature rests on.

The stop path leaves the interrupted message in 'processing' and play replays
it *solely* through /next (``_resync_pending_messages``). That is only correct
if the platform's ``/next`` (``Chat.get_next_actionable_message``) returns a
'processing' message — i.e. excludes only 'processed'. The unit replay test
mocks /next, so it cannot cover this cross-system half; this does.

Fails loudly if the platform ever tightens /next to also exclude 'processing':
stopped messages would then be silently dropped on play.

The invariant is the platform's, not any framework's, so the test runs no adapter
and is pinned to one lane — otherwise it would re-prove the same platform fact in
every lane's job.
"""

from __future__ import annotations

import asyncio

import pytest

from band.platform.link import BandLink
from band.runtime.types import PlatformMessage

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Nothing is *delivered* when a message becomes actionable, so ``/next`` (a plain
# REST read) has no platform event behind it and the capture waiters don't apply —
# a bounded poll is the only barrier available here.
POLL_INTERVAL_S = 0.5


async def _poll_next(
    link: BandLink, room_id: str, expect_id: str, *, deadline_s: float
) -> PlatformMessage | None:
    """Poll ``/next`` until it hands back ``expect_id``, or ``deadline_s`` elapses."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_s
    while loop.time() < deadline:
        message = await link.get_next_message(room_id)
        if message is not None and message.id == expect_id:
            return message
        await asyncio.sleep(POLL_INTERVAL_S)
    return None


@lane(Lane.CORE)  # adapter-agnostic platform invariant; prove it once per run
async def test_next_includes_processing_messages(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    baseline_settings: BaselineSettings,
) -> None:
    agent = await resource_manager.provision_agent("nextsemantics")
    room_id = await resource_manager.provision_room(participants=[agent.id])
    mid = await user_ops.mention(room_id, agent, "probe")

    link = BandLink(
        agent_id=agent.id,
        api_key=agent.api_key,
        ws_url=baseline_settings.endpoints.ws_url,
        rest_url=baseline_settings.endpoints.rest_url,
    )

    # Baseline: /next hands back the fresh, unprocessed message.
    before = await _poll_next(
        link, room_id, mid, deadline_s=baseline_settings.e2e_timeout
    )
    assert before is not None, "sanity: /next never returned the fresh message"
    assert before.id == mid

    # Put it in 'processing' — exactly the state stop leaves behind (nothing further).
    assert await link.mark_processing(room_id, mid), "mark_processing failed"

    # The invariant: a 'processing' message is still actionable via /next, so
    # play's resync replays it instead of dropping it.
    after = await link.get_next_message(room_id)
    assert after is not None and after.id == mid, (
        "/next EXCLUDES 'processing' messages — stop->play via _resync_pending_messages "
        "would silently DROP the stopped message. The cross-system invariant in "
        "ExecutionContext._abort_cycle is broken."
    )
