"""Configured policy governs controls without consuming pending context."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.agent import Agent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.preprocessing.kit import ControlPolicy
from tests.runtime.deferral.kit import ApprovalAdapter
from tests.runtime.helpers import AGENT_ID, ROOM_ID, LifecyclePlatform, rest_client_over
from tests.runtime.scheduler.driver import attempt_delivery


@pytest.mark.parametrize("hosting", ["normal", "pending"])
@pytest.mark.parametrize(
    ("content", "rewritten", "consumed"),
    [
        ("approve", None, False),
        ("approve", "ordinary", False),
        ("permit", "approve", True),
    ],
    ids=["filtered", "transformed-away", "transformed-control"],
)
class TestConfiguredControlPolicy:
    async def test_configured_policy_governs_control_delivery(
        self, hosting: str, content: str, rewritten: str | None, consumed: bool
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("C", content=content)
        adapter = ApprovalAdapter()
        adapter.reject = True
        policy = ControlPolicy(rewritten)
        agent = Agent(
            runtime=MagicMock(agent_id=AGENT_ID), adapter=adapter, preprocessor=policy
        )
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = ExecutionContext(
                ROOM_ID,
                link,
                agent._on_execute,
                agent_id=AGENT_ID,
                on_message_control=agent._on_control,
                is_message_control=agent._is_control,
                config=SessionConfig(
                    enable_context_hydration=False, enable_working_state=False
                ),
            )
            ctx.inject_system_message("Contact update still owed")
            event = make_message_event(room_id=ROOM_ID, msg_id="C", content=content)
            if hosting == "normal":
                await attempt_delivery(ctx, event)
            else:
                await attempt_delivery(ctx, event, priority=True)
            await ctx.stop()

        assert policy.seen == (["C"] if hosting == "normal" or consumed else [])
        if hosting == "pending" and not consumed:
            assert peer.requested("processing") == []
        assert adapter.controls == (["C"] if consumed else [])
        assert not ctx.is_llm_initialized
        assert ctx.participants_changed()
        assert ctx.peek_pending_system_messages() == ["Contact update still owed"]
        expected_processed = (
            ["C"] if consumed or (hosting == "normal" and rewritten is None) else []
        )
        assert peer.marked("processed") == expected_processed
        assert peer.marked("failed") == []
