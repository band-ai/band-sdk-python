"""Configured policy governs controls without consuming pending context."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from band.agent import Agent
from band.core.types import AgentInput
from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from band.runtime.types import SessionConfig
from tests.conftest import make_message_event
from tests.runtime.helpers import AGENT_ID, ROOM_ID, LifecyclePlatform, rest_client_over
from tests.runtime.test_deferral import ApprovalAdapter


class ControlPolicy(DefaultPreprocessor):
    def __init__(self, rewritten: str | None) -> None:
        self.rewritten = rewritten
        self.seen: list[str] = []

    async def process(
        self, ctx: ExecutionContext, event: PlatformEvent, agent_id: str
    ) -> AgentInput | None:
        inp = await super().process(ctx, event, agent_id)
        if inp is None:
            return None
        self.seen.append(inp.msg.id)
        if self.rewritten is None:
            return None
        return replace(inp, msg=replace(inp.msg, content=self.rewritten))


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
async def test_configured_policy_governs_control_delivery(
    hosting: str, content: str, rewritten: str | None, consumed: bool
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
            config=SessionConfig(
                enable_context_hydration=False, enable_working_state=False
            ),
        )
        ctx.inject_system_message("Contact update still owed")
        event = make_message_event(room_id=ROOM_ID, msg_id="C", content=content)
        if hosting == "normal":
            await ctx._process_event(event)
        else:
            await ctx._try_control(event)
        await ctx.stop()

    assert policy.seen == ["C"]
    assert adapter.controls == (["C"] if consumed else [])
    assert not ctx.is_llm_initialized
    assert ctx.participants_changed()
    assert ctx.peek_pending_system_messages() == ["Contact update still owed"]
    expected_processed = (
        ["C"] if consumed or (hosting == "normal" and rewritten is None) else []
    )
    assert peer.marked("processed") == expected_processed
    assert peer.marked("failed") == []
