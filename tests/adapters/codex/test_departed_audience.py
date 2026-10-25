"""Codex's native completion and decisions with an exhausted room audience."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex.types import CodexApprovalMethod
from band.runtime.tools.agent import AgentTools
from tests.adapters.codex.kit import ROOM_ID, send_bootstrap
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_request,
    make_codex_adapter,
    turn_completed,
)
from tests.core.automaticdelivery.kit import AGENT, MembershipPeer
from tests.runtime.helpers import rest_client_over


class TestDepartedAudience:
    @pytest.mark.parametrize(
        ("mode", "decision"),
        [("manual", "decline"), ("auto_accept", "accept"), ("auto_decline", "decline")],
    )
    async def test_native_policy_finishes_without_posting_to_departed_sender(
        self, mode: str, decision: str
    ) -> None:
        client = FakeCodexClient(
            events=[
                event_request(
                    10, CodexApprovalMethod.COMMAND_EXECUTION, {"command": "npm test"}
                ),
                turn_completed(),
            ]
        )
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(approval_mode=mode)
        )
        await adapter.on_started("Codex", "A coding agent")
        peer = MembershipPeer([AGENT])
        try:
            async with rest_client_over(peer.answer) as rest:
                tools = AgentTools(ROOM_ID, rest)
                await send_bootstrap(adapter, tools=tools)
            assert client.responses == [(10, {"decision": decision})]
            assert peer.posts == []
            assert tools.turn.complete
            assert not tools.turn.replied
        finally:
            await adapter.cleanup_all()

    async def test_successful_silent_native_completion_settles_departed_audience(
        self,
    ) -> None:
        adapter = make_codex_adapter(FakeCodexClient(events=[turn_completed()]))
        await adapter.on_started("Codex", "A coding agent")
        peer = MembershipPeer([AGENT])
        try:
            async with rest_client_over(peer.answer) as rest:
                tools = AgentTools(ROOM_ID, rest)
                await send_bootstrap(adapter, tools=tools)
            assert peer.posts == []
            assert tools.turn.complete
            assert not tools.turn.replied
            assert peer.reads == 1
        finally:
            await adapter.cleanup_all()
