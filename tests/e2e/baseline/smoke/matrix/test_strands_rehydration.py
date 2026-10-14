"""A cold adapter recalls a message authored by its LangGraph peer."""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, Lane, lane, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.rehydration import rehydrate_foreign_peer
from tests.e2e.baseline.smoke.samples.sample_agents import REPLY_PROMPT
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


class TestStrandsForeignPeerRehydration:
    @lane(Lane.STRANDS)
    @per_adapter(Adapter.STRANDS, peer=Adapter.LANGGRAPH, prompt=REPLY_PROMPT)
    @pytest.mark.timeout(extra=300)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_rehydrates_foreign_peer_message(
        self,
        cell: AdapterCell,
        peer: AdapterCell,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        baseline_settings: BaselineSettings,
    ) -> None:
        await rehydrate_foreign_peer(
            cell, peer, resource_manager, user_ops, reply_capture, baseline_settings
        )
