"""Configured policy governs controls without consuming pending context."""

from __future__ import annotations

import pytest

from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.preprocessing.kit import ControlPolicy
from tests.runtime.deferral.kit import ApprovalAdapter
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    agent_hooks,
    rest_client_over,
)
from tests.runtime.scheduler.driver import attempt_delivery

# (hosting, content, rewritten, consumed) -> the ids the policy saw and the ids settled.
POLICY_OUTCOMES = [
    pytest.param("normal", "approve", None, False, ["C"], ["C"], id="normal-filtered"),
    pytest.param(
        "normal", "approve", "ordinary", False, ["C"], [], id="normal-transformed-away"
    ),
    pytest.param(
        "normal",
        "permit",
        "approve",
        True,
        ["C"],
        ["C"],
        id="normal-transformed-control",
    ),
    pytest.param("pending", "approve", None, False, [], [], id="pending-filtered"),
    pytest.param(
        "pending", "approve", "ordinary", False, [], [], id="pending-transformed-away"
    ),
    pytest.param(
        "pending",
        "permit",
        "approve",
        True,
        ["C"],
        ["C"],
        id="pending-transformed-control",
    ),
]


@pytest.mark.parametrize(
    ("hosting", "content", "rewritten", "consumed", "seen", "processed"),
    POLICY_OUTCOMES,
)
class TestConfiguredControlPolicy:
    async def test_configured_policy_governs_control_delivery(
        self,
        hosting: str,
        content: str,
        rewritten: str | None,
        consumed: bool,
        seen: list[str],
        processed: list[str],
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("C", content=content)
        adapter = ApprovalAdapter()
        adapter.reject = True
        policy = ControlPolicy(rewritten)
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = ExecutionContext(
                ROOM_ID,
                link,
                agent_id=AGENT_ID,
                **agent_hooks(adapter, preprocessor=policy),
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

        assert policy.seen == seen
        if hosting == "pending" and not consumed:
            assert peer.requested("processing") == []
        assert adapter.controls == (["C"] if consumed else [])
        assert not ctx.is_llm_initialized
        assert ctx.participants_changed()
        assert ctx.peek_pending_system_messages() == ["Contact update still owed"]
        assert peer.marked("processed") == processed
        assert peer.marked("failed") == []
