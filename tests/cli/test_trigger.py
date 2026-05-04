from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.cli.trigger import (
    _format_api_error,
    build_parser,
    find_peer_by_handle,
    main,
    run,
)
from thenvoi_rest.core.api_error import ApiError


# --- Helpers ---


def _make_peer(
    *,
    peer_id: str = "peer-1",
    name: str = "Test Agent",
    handle: str = "owner/agent",
):
    """Create a mock peer object."""
    peer = MagicMock()
    peer.id = peer_id
    peer.name = name
    peer.handle = handle
    return peer


def _make_args(**overrides) -> argparse.Namespace:
    """Create a Namespace with sensible defaults for run()."""
    defaults = {
        "api_key": "test-api-key",
        "rest_url": "https://app.thenvoi.com/",
        "auth_mode": "agent",
        "target_handle": "@owner/agent",
        "message": "Hello agent",
        "timeout": 120,
        "verbose": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _fake_asyncio_run(return_value=None, side_effect=None):
    """Create a fake asyncio.run that closes the coroutine to avoid warnings.

    This means main() tests don't exercise the real async path — that's intentional;
    the async flow is covered separately by TestRun which awaits run() directly.
    """

    def _run(coro):
        coro.close()
        if side_effect is not None:
            raise side_effect
        return return_value

    return _run


def _make_peers_response(peers, total_pages=1, page=1):
    """Create a mock list-peers response."""
    resp = MagicMock()
    resp.data = peers
    resp.metadata = SimpleNamespace(total_pages=total_pages, page=page)
    return resp


def _make_chat_response(room_id="room-123"):
    """Create a mock create-chat response."""
    resp = MagicMock()
    resp.data = SimpleNamespace(id=room_id)
    return resp


def _make_mock_client():
    """Create a mock AsyncRestClient with async-compatible aclose."""
    mock_client = AsyncMock()
    # Match the real Fern chain: AsyncClientWrapper → AsyncHttpClient → httpx.AsyncClient
    mock_client._client_wrapper.httpx_client.httpx_client.aclose = AsyncMock()
    return mock_client


# --- build_parser tests ---


class TestBuildParser:
    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("THENVOI_API_KEY", "env-key")
        monkeypatch.setenv("THENVOI_TARGET_HANDLE", "@env/agent")
        monkeypatch.setenv("THENVOI_MESSAGE", "env message")
        monkeypatch.setenv("THENVOI_AUTH_MODE", "user")

        parser = build_parser()
        args = parser.parse_args([])

        assert args.api_key == "env-key"
        assert args.target_handle == "@env/agent"
        assert args.message == "env message"
        assert args.auth_mode == "user"

    def test_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("THENVOI_API_KEY", "env-key")

        parser = build_parser()
        args = parser.parse_args(["--api-key", "cli-key"])

        assert args.api_key == "cli-key"

    def test_default_rest_url(self):
        parser = build_parser()
        args = parser.parse_args([])

        assert args.rest_url == "https://app.thenvoi.com/"

    def test_default_auth_mode(self):
        parser = build_parser()
        args = parser.parse_args([])

        assert args.auth_mode == "agent"


# --- find_peer_by_handle tests ---


class TestFindPeerByHandle:
    @pytest.mark.asyncio
    async def test_finds_peer_agent_mode(self):
        peer = _make_peer(handle="owner/agent")
        client = AsyncMock()
        client.agent_api_peers.list_agent_peers.return_value = _make_peers_response(
            [peer]
        )

        result = await find_peer_by_handle(client, "@owner/agent", "agent")

        assert result is not None
        assert result["id"] == "peer-1"
        assert result["handle"] == "owner/agent"
        client.agent_api_peers.list_agent_peers.assert_called_once()

    @pytest.mark.asyncio
    async def test_finds_peer_user_mode(self):
        peer = _make_peer(handle="owner/agent")
        client = AsyncMock()
        client.human_api_peers.list_my_peers.return_value = _make_peers_response([peer])

        result = await find_peer_by_handle(client, "@owner/agent", "user")

        assert result is not None
        assert result["id"] == "peer-1"
        client.human_api_peers.list_my_peers.assert_called_once()

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self):
        peer = _make_peer(handle="Owner/Agent")
        client = AsyncMock()
        client.agent_api_peers.list_agent_peers.return_value = _make_peers_response(
            [peer]
        )

        result = await find_peer_by_handle(client, "@owner/agent", "agent")

        assert result is not None
        assert result["handle"] == "Owner/Agent"

    @pytest.mark.asyncio
    async def test_strips_at_prefix(self):
        peer = _make_peer(handle="owner/agent")
        client = AsyncMock()
        client.agent_api_peers.list_agent_peers.return_value = _make_peers_response(
            [peer]
        )

        result = await find_peer_by_handle(client, "@owner/agent", "agent")

        assert result is not None

    @pytest.mark.asyncio
    async def test_works_without_at_prefix(self):
        peer = _make_peer(handle="owner/agent")
        client = AsyncMock()
        client.agent_api_peers.list_agent_peers.return_value = _make_peers_response(
            [peer]
        )

        result = await find_peer_by_handle(client, "owner/agent", "agent")

        assert result is not None

    @pytest.mark.asyncio
    async def test_matches_when_api_returns_at_prefixed_handle(self):
        """Regression: backend may return '@owner/agent' instead of 'owner/agent'."""
        peer = _make_peer(handle="@owner/agent")
        client = AsyncMock()
        client.agent_api_peers.list_agent_peers.return_value = _make_peers_response(
            [peer]
        )

        result = await find_peer_by_handle(client, "@owner/agent", "agent")

        assert result is not None
        assert result["id"] == "peer-1"

    @pytest.mark.asyncio
    async def test_matches_when_api_returns_at_prefix_and_input_has_none(self):
        """Regression: input without @ should still match API response with @."""
        peer = _make_peer(handle="@owner/agent")
        client = AsyncMock()
        client.agent_api_peers.list_agent_peers.return_value = _make_peers_response(
            [peer]
        )

        result = await find_peer_by_handle(client, "owner/agent", "agent")

        assert result is not None
        assert result["id"] == "peer-1"

    @pytest.mark.asyncio
    async def test_paginates_to_find_peer(self):
        other_peer = _make_peer(peer_id="other", name="Other", handle="other/agent")
        target_peer = _make_peer(peer_id="target", name="Target", handle="owner/target")

        client = AsyncMock()
        client.agent_api_peers.list_agent_peers.side_effect = [
            _make_peers_response([other_peer], total_pages=2, page=1),
            _make_peers_response([target_peer], total_pages=2, page=2),
        ]

        result = await find_peer_by_handle(client, "@owner/target", "agent")

        assert result is not None
        assert result["id"] == "target"
        assert client.agent_api_peers.list_agent_peers.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        peer = _make_peer(handle="other/agent")
        client = AsyncMock()
        client.agent_api_peers.list_agent_peers.return_value = _make_peers_response(
            [peer]
        )

        result = await find_peer_by_handle(client, "@owner/missing", "agent")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self):
        client = AsyncMock()
        client.agent_api_peers.list_agent_peers.return_value = _make_peers_response([])

        result = await find_peer_by_handle(client, "@owner/agent", "agent")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_data_is_none(self):
        resp = MagicMock()
        resp.data = None
        client = AsyncMock()
        client.agent_api_peers.list_agent_peers.return_value = resp

        result = await find_peer_by_handle(client, "@owner/agent", "agent")

        assert result is None


# --- run() tests ---


class TestRun:
    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self):
        args = _make_args(api_key=None)
        with pytest.raises(ValueError, match="API key is required"):
            await run(args)

    @pytest.mark.asyncio
    async def test_missing_target_handle_raises(self):
        args = _make_args(target_handle=None)
        with pytest.raises(ValueError, match="Target handle is required"):
            await run(args)

    @pytest.mark.asyncio
    async def test_missing_message_raises(self):
        args = _make_args(message=None)
        with pytest.raises(ValueError, match="Message is required"):
            await run(args)

    @pytest.mark.asyncio
    async def test_peer_not_found_raises(self):
        args = _make_args()

        with (
            patch(
                "thenvoi.cli.trigger.find_peer_by_handle",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("thenvoi.cli.trigger.AsyncRestClient") as MockClient,
        ):
            MockClient.return_value = _make_mock_client()
            with pytest.raises(ValueError, match="not found"):
                await run(args)

    @pytest.mark.asyncio
    async def test_strips_trailing_slash_from_rest_url(self):
        args = _make_args(rest_url="https://app.thenvoi.com/")
        peer = {"id": "peer-1", "name": "Test Agent", "handle": "owner/agent"}

        with (
            patch(
                "thenvoi.cli.trigger.find_peer_by_handle",
                new_callable=AsyncMock,
                return_value=peer,
            ),
            patch("thenvoi.cli.trigger.AsyncRestClient") as MockClient,
        ):
            mock_client = _make_mock_client()
            MockClient.return_value = mock_client
            mock_client.agent_api_chats.create_agent_chat.return_value = (
                _make_chat_response()
            )

            await run(args)

            MockClient.assert_called_once_with(
                api_key="test-api-key", base_url="https://app.thenvoi.com"
            )

    @pytest.mark.asyncio
    async def test_agent_mode_full_flow(self):
        args = _make_args(auth_mode="agent")
        peer = {"id": "peer-1", "name": "Test Agent", "handle": "owner/agent"}

        with (
            patch(
                "thenvoi.cli.trigger.find_peer_by_handle",
                new_callable=AsyncMock,
                return_value=peer,
            ),
            patch("thenvoi.cli.trigger.AsyncRestClient") as MockClient,
        ):
            mock_client = _make_mock_client()
            MockClient.return_value = mock_client
            mock_client.agent_api_chats.create_agent_chat.return_value = (
                _make_chat_response("room-abc")
            )

            room_id = await run(args)

        assert room_id == "room-abc"
        mock_client.agent_api_chats.create_agent_chat.assert_called_once()
        mock_client.agent_api_participants.add_agent_chat_participant.assert_called_once()
        mock_client.agent_api_messages.create_agent_chat_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_mode_full_flow(self):
        args = _make_args(auth_mode="user")
        peer = {"id": "peer-1", "name": "Test Agent", "handle": "owner/agent"}

        with (
            patch(
                "thenvoi.cli.trigger.find_peer_by_handle",
                new_callable=AsyncMock,
                return_value=peer,
            ),
            patch("thenvoi.cli.trigger.AsyncRestClient") as MockClient,
        ):
            mock_client = _make_mock_client()
            MockClient.return_value = mock_client
            mock_client.human_api_chats.create_my_chat_room.return_value = (
                _make_chat_response("room-xyz")
            )

            room_id = await run(args)

        assert room_id == "room-xyz"
        mock_client.human_api_chats.create_my_chat_room.assert_called_once()
        mock_client.human_api_participants.add_my_chat_participant.assert_called_once()
        mock_client.human_api_messages.send_my_chat_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_failure_logs_orphan_room(self):
        args = _make_args(auth_mode="agent")
        peer = {"id": "peer-1", "name": "Test Agent", "handle": "owner/agent"}

        with (
            patch(
                "thenvoi.cli.trigger.find_peer_by_handle",
                new_callable=AsyncMock,
                return_value=peer,
            ),
            patch("thenvoi.cli.trigger.AsyncRestClient") as MockClient,
            patch("thenvoi.cli.trigger.logger") as mock_logger,
        ):
            mock_client = _make_mock_client()
            MockClient.return_value = mock_client
            mock_client.agent_api_chats.create_agent_chat.return_value = (
                _make_chat_response("orphan-room")
            )
            mock_client.agent_api_participants.add_agent_chat_participant.side_effect = RuntimeError(
                "connection failed"
            )

            with pytest.raises(RuntimeError, match="connection failed"):
                await run(args)

        mock_logger.warning.assert_called_once_with(
            "Failed after creating room %s — room may need manual cleanup",
            "orphan-room",
        )

    @pytest.mark.asyncio
    async def test_api_error_on_create_chat_shows_clean_message(self):
        args = _make_args(auth_mode="agent")
        peer = {"id": "peer-1", "name": "Test Agent", "handle": "owner/agent"}

        body = SimpleNamespace(
            error=SimpleNamespace(
                message="Chat room limit reached. Upgrade to create more."
            )
        )
        api_err = ApiError(status_code=403, body=body)

        with (
            patch(
                "thenvoi.cli.trigger.find_peer_by_handle",
                new_callable=AsyncMock,
                return_value=peer,
            ),
            patch("thenvoi.cli.trigger.AsyncRestClient") as MockClient,
        ):
            mock_client = _make_mock_client()
            MockClient.return_value = mock_client
            mock_client.agent_api_chats.create_agent_chat.side_effect = api_err

            with pytest.raises(RuntimeError, match="Chat room limit reached"):
                await run(args)

    @pytest.mark.asyncio
    async def test_api_error_on_send_message_shows_clean_message(self):
        args = _make_args(auth_mode="agent")
        peer = {"id": "peer-1", "name": "Test Agent", "handle": "owner/agent"}

        body = SimpleNamespace(error=SimpleNamespace(message="Forbidden"))
        api_err = ApiError(status_code=403, body=body)

        with (
            patch(
                "thenvoi.cli.trigger.find_peer_by_handle",
                new_callable=AsyncMock,
                return_value=peer,
            ),
            patch("thenvoi.cli.trigger.AsyncRestClient") as MockClient,
        ):
            mock_client = _make_mock_client()
            MockClient.return_value = mock_client
            mock_client.agent_api_chats.create_agent_chat.return_value = (
                _make_chat_response("room-abc")
            )
            mock_client.agent_api_messages.create_agent_chat_message.side_effect = (
                api_err
            )

            with pytest.raises(RuntimeError, match="Forbidden"):
                await run(args)


# --- _format_api_error tests ---


class TestFormatApiError:
    def test_extracts_message_from_body(self):
        body = SimpleNamespace(
            error=SimpleNamespace(message="Chat room limit reached.")
        )
        err = ApiError(status_code=403, body=body)
        result = _format_api_error(err, "create chatroom")
        assert result == "Failed to create chatroom: Chat room limit reached."

    def test_falls_back_to_status_code(self):
        err = ApiError(status_code=500, body=None)
        result = _format_api_error(err, "send message")
        assert result == "Failed to send message: HTTP 500"

    def test_falls_back_when_no_error_attr(self):
        body = SimpleNamespace()
        err = ApiError(status_code=422, body=body)
        result = _format_api_error(err, "add participant")
        assert result == "Failed to add participant: HTTP 422"


# --- main() tests ---


class TestMain:
    def test_exits_0_on_success(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            [
                "thenvoi-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
            ],
        )
        with (
            patch(
                "thenvoi.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(return_value="room-ok"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0

    def test_exits_1_on_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("THENVOI_API_KEY", raising=False)
        monkeypatch.setattr("sys.argv", ["thenvoi-trigger"])

        with (
            patch(
                "thenvoi.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(
                    side_effect=ValueError("API key is required")
                ),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1

    def test_exits_1_on_unexpected_error(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            [
                "thenvoi-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
            ],
        )
        with (
            patch(
                "thenvoi.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(side_effect=Exception("boom")),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1

    def test_exits_1_on_timeout(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            [
                "thenvoi-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
                "--timeout",
                "30",
            ],
        )
        import asyncio as _asyncio

        with (
            patch(
                "thenvoi.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(side_effect=_asyncio.TimeoutError()),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1

    def test_timeout_error_message(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            [
                "thenvoi-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
                "--timeout",
                "45",
            ],
        )
        import asyncio as _asyncio

        with (
            patch(
                "thenvoi.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(side_effect=_asyncio.TimeoutError()),
            ),
            pytest.raises(SystemExit),
        ):
            main()

        assert "timed out after 45 seconds" in capsys.readouterr().err

    def test_writes_room_id_to_stdout(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            [
                "thenvoi-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
            ],
        )
        with (
            patch(
                "thenvoi.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(return_value="room-xyz"),
            ),
            pytest.raises(SystemExit),
        ):
            main()

        assert capsys.readouterr().out.strip() == "room-xyz"

    def test_writes_error_to_stderr(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            [
                "thenvoi-trigger",
                "--api-key",
                "k",
                "--target-handle",
                "@a/b",
                "--message",
                "hi",
            ],
        )
        with (
            patch(
                "thenvoi.cli.trigger.asyncio.run",
                side_effect=_fake_asyncio_run(side_effect=ValueError("bad input")),
            ),
            pytest.raises(SystemExit),
        ):
            main()

        assert "bad input" in capsys.readouterr().err
