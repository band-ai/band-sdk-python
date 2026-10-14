"""The scripted ACP peer records the permission outcome actually selected."""

from __future__ import annotations

from typing import Literal

import pytest
from acp.schema import PermissionOption

from band.integrations.acp.client_adapter import ACPClientAdapter, ACPPermissionRequest
from band.integrations.acp.permissions import ALLOW_ALWAYS_KIND, ALLOW_ONCE_KIND
from band.runtime.tools import BandTool
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import (
    AcpSession,
    fake_agent_config,
    started_acp_adapter,
)


@pytest.mark.parametrize(
    ("kind", "option_id", "selected", "approved"),
    [
        (ALLOW_ONCE_KIND, "custom-once", True, True),
        (ALLOW_ALWAYS_KIND, "custom-always", True, True),
        ("reject_once", "allow-1-denied", True, False),
        (ALLOW_ONCE_KIND, "custom-once", False, False),
    ],
    ids=["allow-once", "allow-always", "reject", "cancel"],
)
async def test_peer_approval_matches_the_selected_offered_option(
    kind: Literal["allow_once", "allow_always", "reject_once"],
    option_id: str,
    selected: bool,
    approved: bool,
) -> None:
    async def choose(request: ACPPermissionRequest) -> str | None:
        return request.options[0].option_id if selected else None

    agent = (
        FakeACPAgent()
        .will_ask_permission(
            options=[PermissionOption(kind=kind, name="Choice", option_id=option_id)]
        )
        .will_call_mcp_tool(
            "settle", BandTool.NO_REPLY, arguments={"reason": "permission checked"}
        )
    )
    adapter = ACPClientAdapter(
        fake_agent_config(inject_band_tools=True), resolve_permission=choose
    )
    async with started_acp_adapter(adapter, agent):
        await AcpSession(adapter, agent).send("ask for permission")

    assert agent.approved is approved
