"""Choose one-shot permission options at the actual native callback boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from acp.schema import PermissionOption, ToolCallUpdate

from band.adapters.antigravity_acp import INVOKE_SUBAGENT_TITLE
from band.core.types import ApprovalMode
from band.integrations.acp.client_adapter import ACPPermissionRequest
from band.integrations.acp.permissions import ALLOW_ALWAYS_KIND
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.adapters.antigravity_acp.control import PermissionControl
from tests.adapters.antigravity_acp.kit import (
    AntigravityRoom,
    ScheduleInput,
    local_schedule,
    mcp_permission,
)
from tests.adapters.antigravity_acp.peerconfig import PeerSettings
from tests.integrations.acp.acp_toolkit.agent import allow_or_reject

ALLOW_ID, REJECT_ID = (option.option_id for option in allow_or_reject())


class TestAntigravityPermissions:
    @pytest.mark.parametrize("approval_mode", ["auto_accept", "auto_decline"])
    async def test_an_additional_schedule_tool_keeps_the_band_tool_policy(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        approval_mode: ApprovalMode,
    ) -> None:
        peer = await open_peer(
            PeerSettings(permission=mcp_permission(BAND_MCP_SERVER_NAME, "schedule")),
            config={"approval_mode": approval_mode},
            additional_tools=[(ScheduleInput, local_schedule)],
        )

        await peer.send()

        assert peer.permission_choices() == [ALLOW_ID]

    async def test_a_caller_permission_resolver_overrides_the_backend_default(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        async def decline(request: ACPPermissionRequest) -> str:
            del request
            return REJECT_ID

        peer = await open_peer(
            PeerSettings(
                permission=mcp_permission(BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE)
            ),
            resolve_permission=decline,
        )

        await peer.send()

        assert peer.permission_choices() == [REJECT_ID]

    @pytest.mark.parametrize("approval_mode", ["auto_accept", "auto_decline"])
    async def test_own_band_tools_are_available_during_a_native_turn(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        approval_mode: ApprovalMode,
    ) -> None:
        peer = await open_peer(
            PeerSettings(
                permission=mcp_permission(BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE)
            ),
            config={"approval_mode": approval_mode},
        )

        tools = await peer.send()

        assert peer.permission_choices() == [ALLOW_ID]
        assert tools.chat == ["A reply from Antigravity."]

    @pytest.mark.parametrize(
        ("approval_mode", "expected"),
        [("auto_accept", ALLOW_ID), ("auto_decline", REJECT_ID)],
    )
    async def test_requested_native_tools_follow_approval_mode_and_denial_continues(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        approval_mode: ApprovalMode,
        expected: str,
    ) -> None:
        peer = await open_peer(
            PeerSettings(
                permission=ToolCallUpdate(
                    tool_call_id="shell",
                    title="printf C3PROBE",
                    raw_input={"CommandLine": "printf C3PROBE", "WaitMsBeforeAsync": 0},
                )
            ),
            config={"approval_mode": approval_mode},
        )

        tools = await peer.send()

        assert peer.permission_choices() == [expected]
        assert tools.chat == ["A reply from Antigravity."]

    @pytest.mark.parametrize("approval_mode", ["auto_accept", "auto_decline"])
    async def test_native_subagent_requests_are_declined(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        approval_mode: ApprovalMode,
    ) -> None:
        peer = await open_peer(
            PeerSettings(
                permission=ToolCallUpdate(
                    tool_call_id="subagent",
                    title=INVOKE_SUBAGENT_TITLE,
                    raw_input={"Subagents": []},
                )
            ),
            config={"approval_mode": approval_mode},
        )

        tools = await peer.send()

        assert peer.permission_choices() == [REJECT_ID]
        assert tools.chat == ["A reply from Antigravity."]

    async def test_external_mcp_schedule_is_not_a_native_background_tool(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        peer = await open_peer(
            PeerSettings(permission=mcp_permission("external", "schedule"))
        )

        await peer.send()

        assert peer.permission_choices() == [ALLOW_ID]

    async def test_permission_after_the_native_turn_is_declined(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        permission_control: PermissionControl,
    ) -> None:
        peer = await open_peer(
            PeerSettings(
                permission=mcp_permission(BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE),
                permission_on_prompt=False,
                control_port=permission_control.port,
            )
        )
        await peer.send()

        await permission_control.request()

        assert peer.permission_choices() == [REJECT_ID]

    async def test_allow_always_is_never_a_fallback(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        peer = await open_peer(
            PeerSettings(
                permission=mcp_permission(BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE),
                options=[
                    PermissionOption(
                        option_id="always", name="Always", kind=ALLOW_ALWAYS_KIND
                    )
                ],
            )
        )

        await peer.send()

        assert peer.permission_choices() == [None]
