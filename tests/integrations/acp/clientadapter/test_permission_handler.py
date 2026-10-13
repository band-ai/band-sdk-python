"""Tests for bidirectional permission proxying."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.schema import (
    PermissionOption,
)

from band.converters.parsing import parse_tool_call, parse_tool_result
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
    ACPPermissionRequest,
)
from band.integrations.acp.client_types import ACPClientSessionState
from band.integrations.acp.types import ACPToolCall
from band.testing import FakeAgentTools
from tests.integrations.acp.acp_toolkit.harness import (
    FakeSpawn,
)
from tests.integrations.acp.clientadapter.kit import (
    event_types,
    metadata_values,
    permission_events,
    room_runtime,
)
from tests.integrations.acp.conftest import make_platform_message


class TestACPClientAdapterPermissionHandler:
    """Tests for bidirectional permission proxying."""

    @pytest.mark.asyncio
    async def test_permission_handler_wired_on_message(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should set permission handler on client before sending prompt."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        # Permission handler should have been set for this session
        assert len(room_runtime(adapter_with_mocks)._client._permission_handlers) > 0

    @pytest.mark.asyncio
    async def test_permission_resolver_receives_only_advertised_choices(self) -> None:
        received: list[ACPPermissionRequest] = []

        async def resolve(request: ACPPermissionRequest) -> str:
            received.append(request)
            return "reject"

        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command="codex"), resolve_permission=resolve
        )
        option_id = await adapter._resolve_permission_option(
            call=ACPToolCall("call-1", "write_file", {}),
            options=(
                PermissionOption(optionId="allow", name="Allow", kind="allow_once"),
                PermissionOption(optionId="reject", name="Reject", kind="reject_once"),
            ),
            room_id="room-1",
            session_id="session-1",
        )

        assert option_id == "reject"
        assert received[0].room_id == "room-1"
        assert [option.option_id for option in received[0].options] == [
            "allow",
            "reject",
        ]

    @pytest.mark.asyncio
    async def test_permission_resolver_invalid_option_raises(self) -> None:
        async def resolve(_request: ACPPermissionRequest) -> str:
            return "missing"

        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command="codex"), resolve_permission=resolve
        )
        with pytest.raises(ValueError, match="unavailable option"):
            await adapter._resolve_permission_option(
                call=ACPToolCall("call-1", "write_file", {}),
                options=(
                    PermissionOption(optionId="allow", name="Allow", kind="allow_once"),
                ),
                room_id="room-1",
                session_id="session-1",
            )

    @pytest.mark.asyncio
    async def test_permission_resolver_deny_cancels_via_request_permission(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """A wired PermissionResolver deny must cancel through request_permission."""

        async def deny(_request: ACPPermissionRequest) -> None:
            return None

        adapter_with_mocks._resolve_permission = deny
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")
        captured: dict[str, object] = {}

        async def mock_prompt(**kwargs: object) -> None:
            tool_call = MagicMock()
            tool_call.title = "write_file"
            tool_call.tool_call_id = "tc-deny"
            tool_call.raw_input = {"path": "/tmp/x"}
            result = await room_runtime(adapter_with_mocks)._client.request_permission(
                options=[
                    {"optionId": "allow-once", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                ],
                session_id="acp-session-123",
                tool_call=tool_call,
            )
            captured.update(result)

        mock_spawn.conn.prompt = AsyncMock(side_effect=mock_prompt)
        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert captured == {"outcome": {"outcome": "cancelled"}}
        perm_events = permission_events(tools)
        assert event_types(perm_events) == ["tool_call", "tool_result"]
        assert perm_events[1]["metadata"]["permission_outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_permission_handler_skips_pair_for_approved_band_send_message(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """An approved band_send_message grants silently, like any other tool.

        Regression guard: band_send_message/band_send_event were formerly
        special-cased ("self-reporting") to post a synthetic permission pair
        since their execution events were suppressed. Now nothing is suppressed —
        if the tool executes, its own real tool_call/tool_result narrate it, so no
        pair should be posted here either.
        """
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        async def mock_prompt(**kwargs):
            tool_call = MagicMock()
            tool_call.title = "band_send_message"
            tool_call.tool_call_id = "tc-perm-1"

            result = await room_runtime(adapter_with_mocks)._client.request_permission(
                options=[
                    {"optionId": "allow-once", "name": "Allow", "kind": "allow_once"}
                ],
                session_id="acp-session-123",
                tool_call=tool_call,
            )
            assert result == {
                "outcome": {"outcome": "selected", "optionId": "allow-once"}
            }

        mock_spawn.conn.prompt = AsyncMock(side_effect=mock_prompt)

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert permission_events(tools) == []

    @pytest.mark.asyncio
    async def test_permission_handler_skips_pair_for_approved_ordinary_tool(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """An approved ordinary tool grants without posting a permission pair.

        The tool's own tool_call/tool_result already show the call, so a pair
        would duplicate it in the room. The grant is still returned to the agent.
        """
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        async def mock_prompt(**kwargs):
            tool_call = MagicMock()
            tool_call.title = "write_file"
            tool_call.tool_call_id = "tc-perm-1"

            result = await room_runtime(adapter_with_mocks)._client.request_permission(
                options=[
                    {"optionId": "allow-once", "name": "Allow", "kind": "allow_once"}
                ],
                session_id="acp-session-123",
                tool_call=tool_call,
            )
            # The grant is still returned even though no pair is posted.
            assert result == {
                "outcome": {"outcome": "selected", "optionId": "allow-once"}
            }

        mock_spawn.conn.prompt = AsyncMock(side_effect=mock_prompt)

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert permission_events(tools) == []

    @pytest.mark.asyncio
    async def test_permission_handler_selects_allow_option(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """Should auto-approve by selecting an offered allow option (not "allowed")."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        captured_result = {}

        async def mock_prompt(**kwargs):
            tool_call = MagicMock()
            tool_call.title = "read_file"
            tool_call.tool_call_id = "tc-read"

            result = await room_runtime(adapter_with_mocks)._client.request_permission(
                options=[
                    {"optionId": "p-once", "name": "Allow once", "kind": "allow_once"},
                    {"optionId": "p-rej", "name": "Reject", "kind": "reject_once"},
                ],
                session_id="acp-session-123",
                tool_call=tool_call,
            )
            captured_result.update(result)

        mock_spawn.conn.prompt = AsyncMock(side_effect=mock_prompt)

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert captured_result == {
            "outcome": {"outcome": "selected", "optionId": "p-once"}
        }

    @pytest.mark.asyncio
    async def test_permission_handler_cancels_without_allow_option(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """Should cancel (not guess) when the agent offers no allow option."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        captured_result = {}

        async def mock_prompt(**kwargs):
            tool_call = MagicMock()
            tool_call.title = "rm_rf"
            tool_call.tool_call_id = "tc-danger"
            tool_call.raw_input = {"path": "/tmp/important"}

            result = await room_runtime(adapter_with_mocks)._client.request_permission(
                options=[
                    {"optionId": "p-rej", "name": "Reject", "kind": "reject_once"},
                ],
                session_id="acp-session-123",
                tool_call=tool_call,
            )
            captured_result.update(result)

        mock_spawn.conn.prompt = AsyncMock(side_effect=mock_prompt)

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        assert captured_result == {"outcome": {"outcome": "cancelled"}}
        perm_events = permission_events(tools)
        assert event_types(perm_events) == ["tool_call", "tool_result"]
        assert metadata_values(perm_events, "tool_call_id") == [
            "tc-danger",
            "tc-danger",
        ]
        call = parse_tool_call(str(perm_events[0]["content"]))
        assert call is not None
        assert call.args == {"path": "/tmp/important"}
        result = parse_tool_result(str(perm_events[1]["content"]))
        assert result is not None
        assert result.output == "Permission cancelled"
        assert result.is_error
        assert perm_events[1]["metadata"]["permission_outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_denied_permission_pair_carries_the_canonical_tool_name(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """A denied ask naming a band tool under its MCP spelling must post its
        synthetic pair under the canonical name — the pair is the only record
        of the call, so it must speak the same vocabulary as real narration."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        async def mock_prompt(**kwargs):
            tool_call = MagicMock()
            tool_call.title = "band-band_send_event"
            tool_call.tool_call_id = "tc-band"
            await room_runtime(adapter_with_mocks)._client.request_permission(
                options=[
                    {"optionId": "p-rej", "name": "Reject", "kind": "reject_once"},
                ],
                session_id="acp-session-123",
                tool_call=tool_call,
            )

        mock_spawn.conn.prompt = AsyncMock(side_effect=mock_prompt)

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        perm_events = permission_events(tools)
        assert event_types(perm_events) == ["tool_call", "tool_result"]
        assert metadata_values(perm_events, "tool_name") == [
            "band_send_event",
            "band_send_event",
        ]
        call = parse_tool_call(str(perm_events[0]["content"]))
        assert call is not None and call.name == "band_send_event"

    @pytest.mark.asyncio
    async def test_permission_handler_uses_name_fallback(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """Should fall back to 'name' attr if 'title' is not available."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        async def mock_prompt(**kwargs):
            tool_call = MagicMock(spec=[])  # No attributes by default
            tool_call.name = "bash"
            tool_call.tool_call_id = "tc-bash"

            await room_runtime(adapter_with_mocks)._client.request_permission(
                options={},
                session_id="acp-session-123",
                tool_call=tool_call,
            )

        mock_spawn.conn.prompt = AsyncMock(side_effect=mock_prompt)

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        perm_events = permission_events(tools)
        assert event_types(perm_events) == ["tool_call", "tool_result"]
        assert metadata_values(perm_events, "tool_name") == ["bash", "bash"]
