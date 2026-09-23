"""Tests for ``OmpACPAdapter``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.schema import (
    AcceptElicitationResponse,
    ClientCapabilities,
    DeclineElicitationResponse,
    ElicitationFormSessionMode,
    ElicitationSchema,
    ElicitationStringPropertySchema,
)
from pydantic import BaseModel

from band.adapters.omp_acp import (
    OmpACPAdapter,
    OmpACPAdapterConfig,
    OmpACPCollectingClient,
)
from band.integrations.acp.client_adapter import ACPPermissionRequest
from band.integrations.acp.client_types import ACPClientSessionState
from band.integrations.acp.session_config import ACPConfigRequest
from band.integrations.omp import (
    OMP_APPROVAL_FORM_TOOL_NAME,
    OMP_APPROVAL_MODE_ALWAYS_ASK,
    OMP_APPROVAL_MODE_FLAG,
    OMP_APPROVAL_MODE_WRITE,
    OMP_APPROVE_OPTION_ID,
    OMP_FORM_APPROVE,
    OMP_FORM_DENY,
    OMP_YOLO_FLAG,
    XD_MCP_PREFIX,
)
from band.testing import FakeAgentTools
from tests.integrations.acp.conftest import make_platform_message


class TestOmpACPAdapterConstruction:
    def test_default_command_gets_final_always_ask(self) -> None:
        adapter = OmpACPAdapter()
        assert adapter._command[-2:] == [
            OMP_APPROVAL_MODE_FLAG,
            OMP_APPROVAL_MODE_ALWAYS_ASK,
        ]

    def test_rejects_unsafe_command_in_config(self) -> None:
        with pytest.raises(ValueError):
            OmpACPAdapter(OmpACPAdapterConfig(command=("omp", "acp", OMP_YOLO_FLAG)))

    def test_unsafe_approval_mode_in_command_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            OmpACPAdapter(
                OmpACPAdapterConfig(
                    command=(
                        "omp",
                        "acp",
                        "--config",
                        "unsafe.json",
                        OMP_APPROVAL_MODE_FLAG,
                        OMP_APPROVAL_MODE_WRITE,
                    )
                )
            )

    def test_finalize_appends_trailing_always_ask(self) -> None:
        adapter = OmpACPAdapter(
            OmpACPAdapterConfig(command=("omp", "acp", "--config", "safe.json"))
        )
        assert adapter._command[-2:] == [
            OMP_APPROVAL_MODE_FLAG,
            OMP_APPROVAL_MODE_ALWAYS_ASK,
        ]

    def test_client_capabilities_are_form_elicitation_only(self) -> None:
        adapter = OmpACPAdapter()
        caps = adapter._client_capabilities
        assert isinstance(caps, ClientCapabilities)
        assert caps.elicitation is not None
        assert caps.elicitation.form is not None
        assert caps.fs is not None and caps.fs.read_text_file is False
        assert caps.fs.write_text_file is False
        assert caps.terminal is False

    def test_uses_unstable_protocol_for_builtin_spawn(self) -> None:
        adapter = OmpACPAdapter()
        assert adapter._use_unstable_protocol is True
        assert adapter._pass_builtin_transport_options is True

    def test_custom_spawn_is_rejected_for_room_process_isolation(self) -> None:
        async def custom_spawn(*_args, **_kwargs):
            raise AssertionError("not called in this construction test")

        with pytest.raises(ValueError, match="room process isolation"):
            OmpACPAdapter(
                OmpACPAdapterConfig(),
                spawn_process=custom_spawn,  # type: ignore[arg-type]
            )

    def test_session_config_and_permission_resolvers_forwarded(self) -> None:
        async def resolver(_request: ACPConfigRequest) -> dict[str, str]:
            return {}

        async def permission(_request: ACPPermissionRequest) -> str | None:
            return None

        adapter = OmpACPAdapter(
            OmpACPAdapterConfig(
                resolve_session_config=resolver,
                resolve_permission=permission,
            )
        )
        assert adapter._resolve_session_config is resolver
        assert adapter._resolve_permission is permission

    def test_custom_tools_and_mcp_forwarded(self) -> None:
        class EchoInput(BaseModel):
            text: str

        def _echo(text: str) -> str:
            return text

        adapter = OmpACPAdapter(
            OmpACPAdapterConfig(mcp_servers=[{"name": "peer"}]),
            additional_tools=[(EchoInput, _echo)],
        )
        assert adapter._custom_tools
        assert adapter._mcp_servers == [{"name": "peer"}]

    def test_runtime_client_factory_is_omp_collecting_client(self) -> None:
        adapter = OmpACPAdapter()
        client = adapter._runtime_client_factory()
        assert isinstance(client, OmpACPCollectingClient)


class TestOmpDeviceCallNormalization:
    def test_collecting_client_rewrites_device_write(self) -> None:
        client = OmpACPCollectingClient(
            own_tool_names=frozenset({"band_send_message"}),
        )
        update = MagicMock()
        update.session_update = "tool_call"
        update.title = "write"
        update.tool_call_id = "tc-1"
        update.raw_input = {
            "path": f"xd://{XD_MCP_PREFIX}band_send_message",
            "content": '{"chat_id":"r1","content":"hello"}',
        }
        update.status = "in_progress"
        chunk = client._tool_call_chunk(update)
        assert chunk.tool is not None
        assert chunk.tool.name == "band_send_message"
        assert chunk.tool.arguments["content"] == "hello"

    def test_collecting_client_rewrites_mcp_title(self) -> None:
        client = OmpACPCollectingClient(
            own_tool_names=frozenset({"band_send_message"}),
        )
        update = MagicMock()
        update.session_update = "tool_call"
        update.title = f"{XD_MCP_PREFIX}band_send_message"
        update.tool_call_id = "tc-title"
        update.raw_input = {"chat_id": "r1", "content": "hello"}
        update.status = "in_progress"
        chunk = client._tool_call_chunk(update)
        assert chunk.tool is not None
        assert chunk.tool.name == "band_send_message"


class TestOmpElicitationHandler:
    @pytest.mark.asyncio
    async def test_approve_form_declines_without_permission_resolver(self) -> None:
        adapter = OmpACPAdapter()
        emitter = MagicMock()
        emitter.open_permission = AsyncMock()
        handler = adapter._make_elicitation_handler(emitter, "room-1", "sess-1")
        schema = {
            "properties": {
                "choice": {"enum": [OMP_FORM_APPROVE, OMP_FORM_DENY]},
            }
        }

        response = await handler(
            message="Allow destructive action?",
            mode="form",
            requested_schema=schema,
        )

        assert isinstance(response, DeclineElicitationResponse)
        emitter.open_permission.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_approve_form_accepts_when_resolver_approves(self) -> None:
        adapter = OmpACPAdapter()

        async def approve(_request: ACPPermissionRequest) -> str:
            return OMP_APPROVE_OPTION_ID

        adapter._resolve_permission = approve
        handler = adapter._make_elicitation_handler(MagicMock(), "room-1", "sess-1")
        schema = {
            "properties": {
                "choice": {"enum": [OMP_FORM_APPROVE, OMP_FORM_DENY]},
            }
        }
        response = await handler(
            message="Allow destructive action?",
            mode="form",
            requested_schema=schema,
        )
        assert isinstance(response, AcceptElicitationResponse)
        assert response.content == {"choice": OMP_FORM_APPROVE}

    @pytest.mark.asyncio
    async def test_form_scope_is_read_from_acp_mode_object(self) -> None:
        """Live ACP packs session_id + schema into ``mode``, not kwargs."""

        adapter = OmpACPAdapter()

        async def approve(_request: ACPPermissionRequest) -> str:
            return OMP_APPROVE_OPTION_ID

        adapter._resolve_permission = approve
        client = adapter._runtime_client_factory()
        handler = adapter._make_elicitation_handler(MagicMock(), "room-1", "sess-live")
        assert handler is not None
        client.set_elicitation_handler("sess-live", handler)
        mode = ElicitationFormSessionMode(
            session_id="sess-live",
            tool_call_id=None,
            requested_schema=ElicitationSchema(
                type="object",
                properties={
                    "value": ElicitationStringPropertySchema(
                        type="string",
                        enum=[OMP_FORM_APPROVE, OMP_FORM_DENY],
                    )
                },
                required=["value"],
            ),
        )
        response = await client.create_elicitation(
            "Allow destructive action?",
            mode,
        )
        assert isinstance(response, AcceptElicitationResponse)
        assert response.content == {"value": OMP_FORM_APPROVE}

    @pytest.mark.asyncio
    async def test_malformed_form_declines(self) -> None:
        adapter = OmpACPAdapter()
        handler = adapter._make_elicitation_handler(MagicMock(), "room-1", "sess-1")
        response = await handler(
            message="?",
            mode="form",
            requested_schema={"properties": {}},
        )
        assert isinstance(response, DeclineElicitationResponse)

    @pytest.mark.asyncio
    async def test_denial_uses_unique_elicitation_ids(self) -> None:
        adapter = OmpACPAdapter()

        async def deny(_request: ACPPermissionRequest) -> None:
            return None

        adapter._resolve_permission = deny
        emitter = MagicMock()
        emitter.open_permission = AsyncMock()
        handler = adapter._make_elicitation_handler(emitter, "room-1", "sess-1")
        schema = {"properties": {"choice": {"enum": [OMP_FORM_APPROVE, OMP_FORM_DENY]}}}
        first = await handler(
            message="?",
            mode="form",
            requested_schema=schema,
        )
        second = await handler(
            message="?",
            mode="form",
            requested_schema=schema,
        )
        assert isinstance(first, DeclineElicitationResponse)
        assert isinstance(second, DeclineElicitationResponse)
        calls = emitter.open_permission.await_args_list
        assert (
            calls[0].kwargs["call"].tool_call_id != calls[1].kwargs["call"].tool_call_id
        )
        assert calls[0].kwargs["call"].name == OMP_APPROVAL_FORM_TOOL_NAME
        assert calls[0].kwargs["outcome"] == "cancelled"
        assert calls[1].kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_denial_via_create_elicitation_narrates_cancelled(self) -> None:
        adapter = OmpACPAdapter()

        async def deny(_request: ACPPermissionRequest) -> None:
            return None

        adapter._resolve_permission = deny
        emitter = MagicMock()
        emitter.open_permission = AsyncMock()
        client = adapter._runtime_client_factory()
        handler = adapter._make_elicitation_handler(emitter, "room-1", "sess-deny")
        assert handler is not None
        client.set_elicitation_handler("sess-deny", handler)
        mode = ElicitationFormSessionMode(
            session_id="sess-deny",
            tool_call_id=None,
            requested_schema=ElicitationSchema(
                type="object",
                properties={
                    "value": ElicitationStringPropertySchema(
                        type="string",
                        enum=[OMP_FORM_APPROVE, OMP_FORM_DENY],
                    )
                },
                required=["value"],
            ),
        )
        response = await client.create_elicitation("Allow?", mode)
        assert isinstance(response, DeclineElicitationResponse)
        calls = emitter.open_permission.await_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["outcome"] == "cancelled"
        assert calls[0].kwargs["call"].name == OMP_APPROVAL_FORM_TOOL_NAME


class TestOmpElicitationHandlerWiring:
    @pytest.mark.asyncio
    async def test_elicitation_handler_wired_on_message(self) -> None:
        """``on_message`` must register the OMP form elicitation handler."""
        adapter = OmpACPAdapter()
        adapter._inject_band_tools = False
        runtime = await adapter._runtime_for("room-123")
        runtime._conn = AsyncMock()
        mock_session = MagicMock()
        mock_session.session_id = "acp-session-123"
        runtime._conn.new_session = AsyncMock(return_value=mock_session)
        runtime._conn.prompt = AsyncMock()
        runtime._client = adapter._runtime_client_factory()

        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")
        await adapter.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )
        assert "acp-session-123" in runtime._client._elicitation_handlers


class TestOmpDeterministicMcpReply:
    @pytest.mark.asyncio
    async def test_normalized_tool_call_and_text_without_model(self) -> None:
        """Device-write normalization yields a Band tool name for narration."""
        client = OmpACPCollectingClient(own_tool_names=frozenset({"band_send_message"}))
        client.set_sink("sess", AsyncMock())
        update = MagicMock()
        update.session_update = "tool_call"
        update.title = "write"
        update.tool_call_id = "tc-band"
        update.raw_input = {
            "path": f"xd://{XD_MCP_PREFIX}band_send_message",
            "content": '{"chat_id":"room-1","content":"done"}',
        }
        update.status = "completed"
        await client.session_update("sess", update)
        chunks = client.get_collected_chunks("sess")
        assert chunks[0].tool is not None
        assert chunks[0].tool.name == "band_send_message"

        text_update = MagicMock()
        text_update.session_update = "agent_message_chunk"
        text_update.content = MagicMock(text="hello from omp")
        await client.session_update("sess", text_update)
        await client.flush("sess")
        assert client.get_collected_text("sess") == "hello from omp"
