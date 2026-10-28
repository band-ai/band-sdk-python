"""Report provider identity and unsupported metadata without exposing payloads."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import pytest
from acp.helpers import start_tool_call
from acp.schema import ToolCallUpdate

from band.adapters.antigravity_acp import TESTED_ANTIGRAVITY_VERSION
from band.core.types import ApprovalMode
from band.integrations.acp.permissions import ALLOW_ONCE_KIND, REJECT_ONCE_KIND
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.adapters.antigravity_acp.kit import AntigravityRoom, mcp_permission
from tests.adapters.antigravity_acp.peer import PEER_AGENT_NAME
from tests.adapters.antigravity_acp.peerconfig import PeerSettings
from tests.integrations.acp.acp_toolkit.agent import allow_or_reject

ALLOW_ID, REJECT_ID = (option.option_id for option in allow_or_reject())


class TestAntigravityDiagnostics:
    @pytest.mark.parametrize(
        ("approval_mode", "expected", "kind"),
        [
            ("auto_accept", ALLOW_ID, ALLOW_ONCE_KIND),
            ("auto_decline", REJECT_ID, REJECT_ONCE_KIND),
        ],
    )
    async def test_native_permission_logs_keep_sensitive_commands_private(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        caplog: pytest.LogCaptureFixture,
        approval_mode: ApprovalMode,
        expected: str,
        kind: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        secret = "sensitive-command-argument"
        command = f"printf {secret}"
        peer = await open_peer(
            PeerSettings(
                permission=ToolCallUpdate(
                    tool_call_id="shell",
                    title=command,
                    raw_input={"CommandLine": command},
                )
            ),
            config={"approval_mode": approval_mode},
        )

        await peer.send()

        assert peer.permission_choices() == [expected]
        assert [
            (record.name, record.levelno, record.getMessage())
            for record in caplog.records
            if "permission:" in record.getMessage().lower()
            or record.getMessage().startswith("Permission request:")
        ] == [
            (
                "band.adapters.antigravity_acp",
                logging.INFO,
                f"Antigravity tool permission: {kind}",
            ),
            (
                "band.integrations.acp.client_adapter",
                logging.INFO,
                "Permission request: selected",
            ),
        ]
        assert secret not in caplog.text

    async def test_valid_mcp_metadata_does_not_report_shape_drift(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING, logger="band.adapters.antigravity_acp")
        peer = await open_peer(
            PeerSettings(
                permission=mcp_permission(BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE)
            )
        )

        await peer.send()

        assert [
            record.getMessage()
            for record in caplog.records
            if record.name == "band.adapters.antigravity_acp"
        ] == []

    async def test_permission_and_narration_warn_only_once_per_process(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING, logger="band.adapters.antigravity_acp")
        secret = "a-payload-that-must-not-be-logged"
        malformed = {"mcp": {"server": "band", "tool": 3}}
        peer = await open_peer(
            PeerSettings(
                permission=ToolCallUpdate(
                    tool_call_id="permission",
                    title="A tool",
                    raw_input={"arguments": {"token": secret}},
                    field_meta=malformed,
                ),
                narration=start_tool_call(
                    "narration", "A tool", raw_input={"arguments": {"token": secret}}
                ).model_copy(update={"field_meta": malformed}),
            )
        )

        await peer.send()
        await peer.send()

        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.name == "band.adapters.antigravity_acp"
        ]
        assert len(warnings) == 1
        assert TESTED_ANTIGRAVITY_VERSION in warnings[0]
        assert secret not in warnings[0]

    async def test_a_new_process_gets_its_own_metadata_warning(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING, logger="band.adapters.antigravity_acp")
        peer = await open_peer(
            PeerSettings(
                permission=ToolCallUpdate(
                    tool_call_id="bad",
                    title="A tool",
                    raw_input={"arguments": {}},
                    field_meta={"mcp": None},
                )
            )
        )

        await peer.send()
        await peer.adapter.cleanup_all()
        await peer.adapter.on_started("Antigravity", "A test agent")
        await peer.send()

        assert [
            record.name
            for record in caplog.records
            if record.name == "band.adapters.antigravity_acp"
        ] == ["band.adapters.antigravity_acp", "band.adapters.antigravity_acp"]

    async def test_the_advertised_server_name_and_version_are_logged(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="band.integrations.acp.client_runtime")
        peer = await open_peer()

        await peer.send()

        assert [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("ACP agent reports")
        ] == [
            f"ACP agent reports name={PEER_AGENT_NAME} version={TESTED_ANTIGRAVITY_VERSION}"
        ]
