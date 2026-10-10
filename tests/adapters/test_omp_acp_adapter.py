"""Tests for ``OmpACPAdapter``."""

from __future__ import annotations

from pathlib import Path
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
from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.integrations.acp.types import ChunkType, CollectedChunk
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
from tests.integrations.acp.acp_toolkit import launch_for
from tests.integrations.acp.conftest import make_platform_message


def omp_in(
    workspace_root: Path, config: OmpACPAdapterConfig | None = None
) -> OmpACPAdapter:
    """An OMP adapter whose room workspaces live under ``workspace_root``."""
    return OmpACPAdapter(
        config, workspace_for_room=lambda room_id: str(workspace_root / room_id)
    )


class TestOmpACPAdapterModel:
    def test_model_is_an_omp_launch_flag_not_a_session_selection(self) -> None:
        adapter = OmpACPAdapter(OmpACPAdapterConfig(model="google/gemini-2.5-flash"))

        assert "--model=google/gemini-2.5-flash" in adapter._spawn_command(None)
        assert adapter.model_selection.is_empty

    def test_one_model_selects_the_flag_and_the_provider_key_env(self) -> None:
        adapter = OmpACPAdapter(
            OmpACPAdapterConfig(
                model="anthropic/claude-haiku-4-5",
                api_key="secret",
                env={"PI_CODING_AGENT_DIR": "/agent-home"},
            )
        )

        assert "--model=anthropic/claude-haiku-4-5" in adapter._spawn_command(None)
        assert adapter._spawn_env() == {
            "PI_CODING_AGENT_DIR": "/agent-home",
            "ANTHROPIC_API_KEY": "secret",
        }

    def test_api_key_without_model_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="api_key needs model"):
            OmpACPAdapterConfig(api_key="secret")

    def test_api_key_stays_out_of_the_config_repr(self) -> None:
        config = OmpACPAdapterConfig(model="openai/gpt-6-luna", api_key="secret")

        assert "secret" not in repr(config)


class TestOmpACPAdapterConfig:
    @pytest.mark.parametrize(
        "command",
        [
            ("omp", "acp", OMP_YOLO_FLAG),
            (
                "omp",
                "acp",
                "--config",
                "unsafe.json",
                OMP_APPROVAL_MODE_FLAG,
                OMP_APPROVAL_MODE_WRITE,
            ),
        ],
        ids=["yolo-flag", "write-approval-mode"],
    )
    def test_unsafe_approval_flags_in_the_command_are_rejected(
        self, command: tuple[str, ...]
    ) -> None:
        # Even yolo, the one full-access mode, is only reachable via approval_mode.
        with pytest.raises(ValueError, match="Unsafe OMP"):
            OmpACPAdapterConfig(command=command, approval_mode="yolo")

    @pytest.mark.parametrize(
        "settings",
        [{"approval_mode": "write"}, {"use_unstable_protocol": False}],
        ids=["unknown-approval-mode", "stable-protocol-without-approval-forms"],
    )
    def test_settings_omp_cannot_run_with_are_rejected(
        self, settings: dict[str, object]
    ) -> None:
        with pytest.raises(ValueError, match=next(iter(settings))):
            OmpACPAdapterConfig.model_validate(settings)

    def test_cwd_and_a_workspace_resolver_are_exclusive(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not both"):
            OmpACPAdapter(
                OmpACPAdapterConfig(cwd=str(tmp_path)),
                workspace_for_room=lambda room_id: room_id,
            )

    def test_a_custom_spawn_is_rejected_for_room_process_isolation(self) -> None:
        async def custom_spawn(*_args, **_kwargs):
            raise AssertionError("not called in this construction test")

        with pytest.raises(ValueError, match="room process isolation"):
            OmpACPAdapter(spawn_process=custom_spawn)


class TestOmpACPAdapterLaunch:
    @pytest.mark.asyncio
    async def test_the_command_ends_with_the_selected_approval_mode(
        self, tmp_path: Path
    ) -> None:
        config = OmpACPAdapterConfig(
            command=("omp", "acp", "--config", "safe.json"), approval_mode="yolo"
        )

        launch = await launch_for(omp_in(tmp_path, config))

        assert launch.command[-2:] == (OMP_APPROVAL_MODE_FLAG, "yolo")

    @pytest.mark.asyncio
    async def test_defaults_to_always_ask(self, tmp_path: Path) -> None:
        launch = await launch_for(omp_in(tmp_path))

        assert launch.command[-2:] == (
            OMP_APPROVAL_MODE_FLAG,
            OMP_APPROVAL_MODE_ALWAYS_ASK,
        )

    @pytest.mark.asyncio
    async def test_cwd_becomes_a_room_workspace_root(self, tmp_path: Path) -> None:
        adapter = OmpACPAdapter(OmpACPAdapterConfig(cwd=str(tmp_path)))

        launch = await launch_for(adapter, "room-a")

        assert f"--cwd={tmp_path / 'room-a'}" in launch.command

    @pytest.mark.asyncio
    async def test_asks_for_form_elicitation_over_the_unstable_protocol(
        self, tmp_path: Path
    ) -> None:
        launch = await launch_for(omp_in(tmp_path))

        caps = launch.client_capabilities
        assert launch.use_unstable_protocol is True
        assert isinstance(caps, ClientCapabilities)
        assert caps.elicitation is not None
        assert caps.elicitation.form is not None
        assert caps.fs is not None and caps.fs.read_text_file is False
        assert caps.fs.write_text_file is False
        assert caps.terminal is False

    def test_custom_tools_are_registered(self) -> None:
        class EchoInput(BaseModel):
            text: str

        def _echo(text: str) -> str:
            return text

        adapter = OmpACPAdapter(additional_tools=[(EchoInput, _echo)])

        assert "echo" in adapter._own_tool_names

    def test_runtime_client_factory_is_omp_collecting_client(self) -> None:
        adapter = OmpACPAdapter()
        client = adapter._runtime_client_factory()
        assert isinstance(client, OmpACPCollectingClient)


class TestOmpWorkspaceSpawn:
    """omp's Bun runtime hangs (or degrades into an endless permission-request
    retry loop) on its first real turn when the *subprocess itself* is given
    an explicit cwd -- CPython's subprocess machinery only uses the fast
    posix_spawn() path when cwd is None, and falls back to a fork()+chdir()
    path otherwise that breaks omp. omp must instead take its per-room
    workspace via its own --cwd flag, with the subprocess-level cwd left
    unset."""

    def test_workspace_becomes_an_omp_cwd_flag_not_a_subprocess_cwd(self) -> None:
        adapter = OmpACPAdapter()

        assert adapter._spawn_command("/rooms/room-a") == [
            "omp",
            "acp",
            "--cwd=/rooms/room-a",
            *adapter._spawn_command(None)[2:],
        ]
        assert adapter._spawn_cwd("/rooms/room-a") is None

    def test_no_workspace_leaves_the_command_untouched(self) -> None:
        adapter = OmpACPAdapter()

        assert "--cwd" not in " ".join(adapter._spawn_command(None))
        assert adapter._spawn_cwd(None) is None

    def test_runtime_omits_subprocess_cwd_but_keeps_the_omp_cwd_flag(
        self, tmp_path: Path
    ) -> None:
        adapter = OmpACPAdapter()

        runtime = adapter._build_runtime(str(tmp_path))

        assert runtime._cwd is None
        assert f"--cwd={tmp_path}" in runtime._command


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
    async def test_approve_form_accepts_without_permission_resolver(self) -> None:
        """No configured resolver must auto-approve (mirrors
        _make_permission_handler's default), not silently decline every
        OMP tool call."""
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

        assert isinstance(response, AcceptElicitationResponse)
        assert response.content == {"choice": OMP_FORM_APPROVE}
        emitter.open_permission.assert_not_awaited()

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
        adapter = OmpACPAdapter(OmpACPAdapterConfig(inject_band_tools=False))
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

    @pytest.mark.asyncio
    async def test_reading_band_tool_docs_cannot_complete_the_turn(self) -> None:
        """Reading device documentation is observation, even for a reply tool."""
        client = OmpACPCollectingClient(own_tool_names=frozenset({"band_send_message"}))
        client.set_sink("sess", AsyncMock())
        update = MagicMock()
        update.session_update = "tool_call"
        update.title = "read"
        update.tool_call_id = "tc-docs"
        update.raw_input = {"path": f"xd://{XD_MCP_PREFIX}band_send_message"}
        update.status = "completed"
        await client.session_update("sess", update)
        tools = FakeAgentTools()

        emitter = RoomTurnEmitter(
            tools,
            session_id="sess",
            room_id="room-1",
            records_tool_effects=True,
        )
        async with emitter:
            for chunk in client.get_collected_chunks("sess"):
                await emitter.emit(chunk)
            await emitter.emit(
                CollectedChunk(chunk_type=ChunkType.TEXT, content="The answer.")
            )

        assert tools.chat == []
        assert not tools.turn.complete
