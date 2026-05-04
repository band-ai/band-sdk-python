"""Tests for ACPServer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.integrations.acp.server import ACPServer
from thenvoi.integrations.acp.server_adapter import ThenvoiACPServerAdapter


class TestACPServerInitialize:
    """Tests for ACPServer.initialize()."""

    @pytest.mark.asyncio
    async def test_initialize_returns_protocol_version(self) -> None:
        """Should return the same protocol version as requested."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        response = await server.initialize(protocol_version=1)

        assert response.protocol_version == 1

    @pytest.mark.asyncio
    async def test_initialize_returns_agent_info(self) -> None:
        """Should return agent metadata, capabilities, and auth methods."""
        adapter = ThenvoiACPServerAdapter()
        await adapter.on_started("My Agent", "A test agent")
        server = ACPServer(adapter)

        response = await server.initialize(protocol_version=1)

        assert response.agent_info.name == "thenvoi-agent"
        assert response.agent_info.title == "My Agent"
        assert response.agent_info.version is not None
        assert response.agent_capabilities is not None
        assert response.agent_capabilities.load_session is True
        assert response.agent_capabilities.session_capabilities.list is not None
        assert response.agent_capabilities.session_capabilities.resume is not None
        assert response.agent_capabilities.prompt_capabilities.embedded_context is True
        assert response.agent_capabilities.field_meta["streaming"] is True
        assert response.agent_capabilities.field_meta["tools"] is True
        assert "default" in response.agent_capabilities.field_meta["modes"]
        assert response.auth_methods is not None
        assert any(method.id == "api_key" for method in response.auth_methods)

    @pytest.mark.asyncio
    async def test_initialize_with_client_info(self) -> None:
        """Should accept optional client_capabilities and client_info."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        response = await server.initialize(
            protocol_version=1,
            client_capabilities={"streaming": True},
            client_info={"name": "Zed", "version": "1.0"},
        )

        assert response.protocol_version == 1


class TestACPServerNewSession:
    """Tests for ACPServer.new_session()."""

    @pytest.mark.asyncio
    async def test_new_session_delegates_to_adapter(self) -> None:
        """Should delegate session creation to adapter."""
        adapter = ThenvoiACPServerAdapter()
        adapter.create_session = AsyncMock(return_value="session-abc123")
        server = ACPServer(adapter)

        response = await server.new_session(cwd="/workspace")

        assert response.session_id == "session-abc123"
        adapter.create_session.assert_called_once_with(
            cwd="/workspace", mcp_servers=None
        )

    @pytest.mark.asyncio
    async def test_new_session_with_mcp_servers(self) -> None:
        """Should accept optional mcp_servers parameter."""
        adapter = ThenvoiACPServerAdapter()
        adapter.create_session = AsyncMock(return_value="session-xyz")
        server = ACPServer(adapter)

        mcp_servers = [{"type": "stdio", "command": "some-mcp-server"}]
        response = await server.new_session(
            cwd="/workspace",
            mcp_servers=mcp_servers,
        )

        assert response.session_id == "session-xyz"
        adapter.create_session.assert_called_once_with(
            cwd="/workspace", mcp_servers=mcp_servers
        )


class TestACPServerPrompt:
    """Tests for ACPServer.prompt()."""

    @pytest.mark.asyncio
    async def test_prompt_extracts_text_and_delegates(self) -> None:
        """Should extract text from content blocks and delegate to adapter."""
        adapter = ThenvoiACPServerAdapter()
        adapter.handle_prompt = AsyncMock()
        server = ACPServer(adapter)

        prompt_blocks = [{"text": "Hello world"}]
        response = await server.prompt(prompt=prompt_blocks, session_id="session-1")

        assert response.stop_reason == "end_turn"
        adapter.handle_prompt.assert_called_once_with("session-1", "Hello world")

    @pytest.mark.asyncio
    async def test_prompt_multiple_text_blocks(self) -> None:
        """Should concatenate text from multiple blocks."""
        adapter = ThenvoiACPServerAdapter()
        adapter.handle_prompt = AsyncMock()
        server = ACPServer(adapter)

        prompt_blocks = [
            {"text": "First line"},
            {"text": "Second line"},
        ]
        await server.prompt(prompt=prompt_blocks, session_id="session-1")

        adapter.handle_prompt.assert_called_once_with(
            "session-1", "First line\nSecond line"
        )

    @pytest.mark.asyncio
    async def test_prompt_skips_non_text_blocks(self) -> None:
        """Should skip content blocks without text."""
        adapter = ThenvoiACPServerAdapter()
        adapter.handle_prompt = AsyncMock()
        server = ACPServer(adapter)

        prompt_blocks = [
            {"type": "text", "text": "Hello"},
            {"type": "image", "data": "base64..."},
            {"type": "text", "text": "World"},
        ]
        await server.prompt(prompt=prompt_blocks, session_id="session-1")

        adapter.handle_prompt.assert_called_once_with(
            "session-1", "Hello\n[Image]\nWorld"
        )

    @pytest.mark.asyncio
    async def test_prompt_with_object_blocks(self) -> None:
        """Should handle content blocks as objects (not just dicts)."""
        adapter = ThenvoiACPServerAdapter()
        adapter.handle_prompt = AsyncMock()
        server = ACPServer(adapter)

        # Simulate object-style content blocks (TextContentBlock)
        block = MagicMock()
        block.type = "text"
        block.text = "Object block text"

        await server.prompt(prompt=[block], session_id="session-1")

        adapter.handle_prompt.assert_called_once_with("session-1", "Object block text")


class TestACPServerCancel:
    """Tests for ACPServer.cancel()."""

    @pytest.mark.asyncio
    async def test_cancel_delegates_to_adapter(self) -> None:
        """Should delegate cancellation to adapter."""
        adapter = ThenvoiACPServerAdapter()
        adapter.cancel_prompt = AsyncMock()
        server = ACPServer(adapter)

        await server.cancel(session_id="session-1")

        adapter.cancel_prompt.assert_called_once_with("session-1")


class TestACPServerOnConnect:
    """Tests for ACPServer.on_connect()."""

    def test_on_connect_stores_client(self) -> None:
        """Should store client reference and pass to adapter."""
        adapter = ThenvoiACPServerAdapter()
        adapter.set_acp_client = MagicMock()
        server = ACPServer(adapter)

        mock_client = MagicMock()
        server.on_connect(mock_client)

        assert server._conn is mock_client
        adapter.set_acp_client.assert_called_once_with(mock_client)


class TestACPServerExtractText:
    """Tests for ACPServer._extract_text()."""

    def test_extract_text_empty(self) -> None:
        """Should return empty string for empty list."""
        assert ACPServer._extract_text([]) == ""

    def test_extract_text_single_dict(self) -> None:
        """Should extract text from single dict block."""
        assert ACPServer._extract_text([{"type": "text", "text": "Hello"}]) == "Hello"

    def test_extract_text_single_dict_no_type(self) -> None:
        """Should default to text type when type field is missing."""
        assert ACPServer._extract_text([{"text": "Hello"}]) == "Hello"

    def test_extract_text_single_object(self) -> None:
        """Should extract text from single object block."""
        block = MagicMock()
        block.type = "text"
        block.text = "Hello"
        assert ACPServer._extract_text([block]) == "Hello"

    def test_extract_text_mixed(self) -> None:
        """Should handle mix of dict and object blocks."""
        obj_block = MagicMock()
        obj_block.type = "text"
        obj_block.text = "Object"
        blocks = [{"type": "text", "text": "Dict"}, obj_block]
        assert ACPServer._extract_text(blocks) == "Dict\nObject"

    def test_extract_text_skips_empty(self) -> None:
        """Should skip blocks with no text."""
        blocks = [
            {"type": "text", "text": ""},
            {"type": "text", "text": "Hello"},
        ]
        assert ACPServer._extract_text(blocks) == "Hello"

    def test_extract_text_image_block_dict(self) -> None:
        """Should represent image blocks with URI."""
        blocks = [{"type": "image", "uri": "file:///screenshot.png"}]
        assert ACPServer._extract_text(blocks) == "[Image: file:///screenshot.png]"

    def test_extract_text_image_block_no_uri(self) -> None:
        """Should represent image blocks without URI."""
        blocks = [{"type": "image", "data": "base64..."}]
        assert ACPServer._extract_text(blocks) == "[Image]"

    def test_extract_text_resource_block_dict(self) -> None:
        """Should represent resource blocks with title and description."""
        blocks = [
            {
                "type": "resource",
                "title": "README.md",
                "uri": "file:///workspace/README.md",
                "description": "Project readme",
            }
        ]
        assert ACPServer._extract_text(blocks) == "[Resource: README.md] Project readme"

    def test_extract_text_resource_block_object(self) -> None:
        """Should handle resource blocks as objects."""
        block = MagicMock()
        block.type = "resource"
        block.title = "config.yaml"
        block.name = ""
        block.uri = "file:///config.yaml"
        block.description = ""
        assert ACPServer._extract_text([block]) == "[Resource: config.yaml]"

    def test_extract_text_mixed_content_types(self) -> None:
        """Should handle a mix of text, image, and resource blocks."""
        blocks = [
            {"type": "text", "text": "Check this file:"},
            {"type": "resource", "title": "main.py", "uri": "file:///main.py"},
            {"type": "text", "text": "And this image:"},
            {"type": "image", "uri": "file:///screenshot.png"},
        ]
        result = ACPServer._extract_text(blocks)
        assert "[Resource: main.py]" in result
        assert "[Image: file:///screenshot.png]" in result
        assert "Check this file:" in result


class TestACPServerLoadSession:
    """Tests for ACPServer.load_session()."""

    @pytest.mark.asyncio
    async def test_load_session_existing(self) -> None:
        """Should return LoadSessionResponse for existing session."""
        adapter = ThenvoiACPServerAdapter()
        adapter._session_to_room["session-1"] = "room-1"
        server = ACPServer(adapter)

        response = await server.load_session(cwd="/workspace", session_id="session-1")

        assert response is not None
        assert adapter._session_cwd["session-1"] == "/workspace"

    @pytest.mark.asyncio
    async def test_load_session_updates_mcp_context(self) -> None:
        """Should refresh stored MCP session context on load."""
        adapter = ThenvoiACPServerAdapter()
        adapter._session_to_room["session-1"] = "room-1"
        server = ACPServer(adapter)

        mcp_servers = [{"type": "stdio", "name": "filesystem"}]
        response = await server.load_session(
            cwd="/workspace",
            session_id="session-1",
            mcp_servers=mcp_servers,
        )

        assert response is not None
        assert adapter._session_mcp_servers["session-1"] == mcp_servers

    @pytest.mark.asyncio
    async def test_load_session_not_found(self) -> None:
        """Should return None for non-existent session."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        response = await server.load_session(cwd="/workspace", session_id="unknown")

        assert response is None


class TestACPServerListSessions:
    """Tests for ACPServer.list_sessions()."""

    @pytest.mark.asyncio
    async def test_list_sessions_returns_active(self) -> None:
        """Should return all active sessions with correct cwd."""
        adapter = ThenvoiACPServerAdapter()
        adapter._session_to_room["session-1"] = "room-1"
        adapter._session_to_room["session-2"] = "room-2"
        adapter._session_cwd["session-1"] = "/workspace/project-a"
        adapter._session_cwd["session-2"] = "/workspace/project-b"
        server = ACPServer(adapter)

        response = await server.list_sessions()

        assert len(response.sessions) == 2
        session_ids = {s.session_id for s in response.sessions}
        assert session_ids == {"session-1", "session-2"}
        cwd_map = {s.session_id: s.cwd for s in response.sessions}
        assert cwd_map["session-1"] == "/workspace/project-a"
        assert cwd_map["session-2"] == "/workspace/project-b"

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self) -> None:
        """Should return empty list when no sessions."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        response = await server.list_sessions()

        assert response.sessions == []


class TestACPServerSetSessionMode:
    """Tests for ACPServer.set_session_mode()."""

    @pytest.mark.asyncio
    async def test_set_session_mode(self) -> None:
        """Should store mode in adapter state."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        response = await server.set_session_mode(mode_id="code", session_id="session-1")

        assert response is not None
        assert adapter._session_modes["session-1"] == "code"


class TestACPServerResumeSession:
    """Tests for ACPServer.resume_session()."""

    @pytest.mark.asyncio
    async def test_resume_session_updates_context(self) -> None:
        """Should refresh stored cwd and MCP servers on resume."""
        adapter = ThenvoiACPServerAdapter()
        adapter._session_to_room["session-1"] = "room-1"
        server = ACPServer(adapter)

        mcp_servers = [{"type": "stdio", "name": "filesystem"}]
        response = await server.resume_session(
            cwd="/workspace",
            session_id="session-1",
            mcp_servers=mcp_servers,
        )

        assert response is not None
        assert adapter._session_cwd["session-1"] == "/workspace"
        assert adapter._session_mcp_servers["session-1"] == mcp_servers


class TestACPServerSetSessionModel:
    """Tests for ACPServer.set_session_model()."""

    @pytest.mark.asyncio
    async def test_set_session_model(self) -> None:
        """Should store model in adapter state."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        response = await server.set_session_model(
            model_id="gpt-4o", session_id="session-1"
        )

        assert response is not None
        # set_session_model stores for future use; verify it doesn't raise


class TestACPServerAuthenticate:
    """Tests for ACPServer.authenticate()."""

    @pytest.mark.asyncio
    async def test_authenticate_valid_key(self) -> None:
        """Should return AuthenticateResponse for valid key."""
        adapter = ThenvoiACPServerAdapter()
        mock_rest = MagicMock()
        mock_rest.agent_api_identity.get_agent_me = AsyncMock()
        adapter._rest = mock_rest
        server = ACPServer(adapter)

        response = await server.authenticate(method_id="api_key")

        assert response is not None

    @pytest.mark.asyncio
    async def test_authenticate_invalid_key(self) -> None:
        """Should return None for invalid key."""
        adapter = ThenvoiACPServerAdapter()
        mock_rest = MagicMock()
        mock_rest.agent_api_identity.get_agent_me = AsyncMock(
            side_effect=RuntimeError("Unauthorized")
        )
        adapter._rest = mock_rest
        server = ACPServer(adapter)

        response = await server.authenticate(method_id="api_key")

        assert response is None

    @pytest.mark.asyncio
    async def test_authenticate_unknown_method(self) -> None:
        """Should return None for unsupported auth method."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        response = await server.authenticate(method_id="oauth")

        assert response is None


class TestACPServerCloseSession:
    """Tests for ACPServer.close_session()."""

    @pytest.mark.asyncio
    async def test_close_session_cleans_up_room_and_returns_none(self) -> None:
        adapter = ThenvoiACPServerAdapter()
        adapter._session_to_room["session-1"] = "room-1"
        adapter.on_cleanup = AsyncMock()
        server = ACPServer(adapter)

        response = await server.close_session(session_id="session-1")

        assert response is None
        adapter.on_cleanup.assert_called_once_with("room-1")

    @pytest.mark.asyncio
    async def test_close_session_unknown_session_returns_none(self) -> None:
        adapter = ThenvoiACPServerAdapter()
        adapter.on_cleanup = AsyncMock()
        server = ACPServer(adapter)

        response = await server.close_session(session_id="unknown")

        assert response is None
        adapter.on_cleanup.assert_not_called()


class TestACPServerExtMethod:
    """Tests for ACPServer.ext_method()."""

    @pytest.mark.asyncio
    async def test_ext_method_returns_error_for_unknown(self) -> None:
        """Should return error for unknown extension method."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        result = await server.ext_method("thenvoi/unknown", {"key": "val"})

        assert "error" in result


class TestACPServerExtNotification:
    """Tests for ACPServer.ext_notification()."""

    @pytest.mark.asyncio
    async def test_ext_notification_does_not_crash(self) -> None:
        """Should handle extension notifications gracefully."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        # Should not raise
        await server.ext_notification("thenvoi/status", {"status": "ok"})


class TestACPServerCursorExtensions:
    """Tests for Cursor-specific ACP extension handling."""

    @pytest.mark.asyncio
    async def test_cursor_ask_question_selects_first_option(self) -> None:
        """Should auto-select the first option for cursor/ask_question."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        result = await server.ext_method(
            "cursor/ask_question",
            {
                "options": [
                    {"optionId": "opt1", "name": "Option A"},
                    {"optionId": "opt2", "name": "Option B"},
                ],
            },
        )

        assert result["outcome"]["type"] == "selected"
        assert result["outcome"]["optionId"] == "opt1"

    @pytest.mark.asyncio
    async def test_cursor_ask_question_cancels_when_no_options(self) -> None:
        """Should cancel if no options are provided."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        result = await server.ext_method("cursor/ask_question", {"options": []})

        assert result["outcome"]["type"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cursor_create_plan_auto_approves(self) -> None:
        """Should auto-approve cursor/create_plan."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        result = await server.ext_method("cursor/create_plan", {"plan": "Do stuff"})

        assert result["outcome"]["type"] == "approved"

    @pytest.mark.asyncio
    async def test_cursor_login_auth_method(self) -> None:
        """Should accept cursor_login as an auth method."""
        adapter = ThenvoiACPServerAdapter()
        adapter.verify_credentials = AsyncMock(return_value=True)
        server = ACPServer(adapter)

        result = await server.authenticate(method_id="cursor_login")

        assert result is not None
        adapter.verify_credentials.assert_called_once()

    @pytest.mark.asyncio
    async def test_cursor_update_todos_notification(self) -> None:
        """Should handle cursor/update_todos without crashing."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        # No active session — should just log and return
        await server.ext_notification(
            "cursor/update_todos",
            {
                "sessionId": "nonexistent",
                "todos": [
                    {"content": "Read code", "completed": True},
                    {"content": "Write tests", "completed": False},
                ],
            },
        )

    @pytest.mark.asyncio
    async def test_cursor_task_notification(self) -> None:
        """Should handle cursor/task without crashing."""
        adapter = ThenvoiACPServerAdapter()
        server = ACPServer(adapter)

        await server.ext_notification(
            "cursor/task",
            {"sessionId": "nonexistent", "result": "Task completed"},
        )


class TestACPServerExtractTextEdgeCases:
    """Additional edge case tests for ACPServer._extract_text()."""

    def test_image_dict_block_with_uri(self) -> None:
        """Should format image with URI."""
        blocks = [{"type": "image", "uri": "https://example.com/img.png"}]
        assert ACPServer._extract_text(blocks) == "[Image: https://example.com/img.png]"

    def test_image_dict_block_without_uri(self) -> None:
        """Should format image without URI."""
        blocks = [{"type": "image"}]
        assert ACPServer._extract_text(blocks) == "[Image]"

    def test_resource_dict_block(self) -> None:
        """Should format resource with title and description."""
        blocks = [
            {
                "type": "resource",
                "title": "readme.md",
                "uri": "file:///readme.md",
                "description": "Project readme",
            }
        ]
        result = ACPServer._extract_text(blocks)
        assert "[Resource: readme.md]" in result
        assert "Project readme" in result

    def test_resource_dict_block_fallback_to_uri(self) -> None:
        """Should use URI when title is missing."""
        blocks = [{"type": "resource", "uri": "file:///data.json"}]
        assert "file:///data.json" in ACPServer._extract_text(blocks)

    def test_unknown_block_type_skipped(self) -> None:
        """Should skip unknown block types."""
        blocks = [{"type": "audio", "data": "..."}]
        assert ACPServer._extract_text(blocks) == ""

    def test_mixed_text_and_image_blocks(self) -> None:
        """Should handle mixed block types with newline separation."""
        blocks = [
            {"type": "text", "text": "Hello"},
            {"type": "image", "uri": "img.png"},
            {"type": "text", "text": "World"},
        ]
        result = ACPServer._extract_text(blocks)
        assert "Hello" in result
        assert "[Image: img.png]" in result
        assert "World" in result

    def test_object_image_block(self) -> None:
        """Should format object-style image blocks."""
        block = MagicMock()
        block.type = "image"
        block.uri = "https://example.com/photo.jpg"
        result = ACPServer._extract_text([block])
        assert "[Image: https://example.com/photo.jpg]" in result

    def test_object_resource_block(self) -> None:
        """Should format object-style resource blocks."""
        block = MagicMock()
        block.type = "resource"
        block.title = "data.csv"
        block.name = ""
        block.uri = "file:///data.csv"
        block.description = "Dataset"
        result = ACPServer._extract_text([block])
        assert "[Resource: data.csv]" in result
        assert "Dataset" in result
