"""Guards the /next actionable-set semantics the stop->play feature rests on.

The stop path leaves the interrupted message in 'processing' and play replays
it *solely* through /next (``_resync_pending_messages``). That is only correct
if the platform's ``/next`` (``Chat.get_next_actionable_message``) returns a
'processing' message — i.e. excludes only 'processed'. The unit replay test
mocks /next, so it cannot cover this cross-system half; this does.

Fails loudly if the platform ever tightens /next to also exclude 'processing':
stopped messages would then be silently dropped on play.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.platform.link import BandLink
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _poll_next(link: BandLink, room_id: str, expect_id: str):
    """Give the platform a moment to make a fresh message actionable."""
    for _ in range(20):
        msg = await link.get_next_message(room_id)
        if msg is not None and msg.id == expect_id:
            return msg
        await asyncio.sleep(0.5)
    return None


async def test_next_includes_processing_messages(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    baseline_settings: BaselineSettings,
) -> None:
    agent = await resource_manager.provision_agent("nextsemantics")
    room_id = await resource_manager.provision_room(participants=[agent.id])
    mid = await user_ops.send_message(
        room_id, "probe", mention_id=agent.id, mention_name=agent.name
    )

    link = BandLink(
        agent_id=agent.id,
        api_key=agent.api_key,
        ws_url=baseline_settings.endpoints.ws_url,
        rest_url=baseline_settings.endpoints.rest_url,
    )

    # Baseline: /next hands back the fresh, unprocessed message.
    before = await _poll_next(link, room_id, mid)
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
