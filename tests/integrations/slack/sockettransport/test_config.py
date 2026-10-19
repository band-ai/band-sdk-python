"""Socket config behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.integrations.slack.adapter import SlackAdapter, SlackAdapterConfig
from band.integrations.slack.types import SlackApp
from tests.integrations.slack.socketkit import (
    _http_app,
    _socket_app,
)
from tests.integrations.slack.wrappingkit import (
    SlackReplyBrain,
)


class TestSocketConfig:
    def test_http_transport_requires_signing_secret(self):
        bad = SlackApp(slug="x", bot_token="xoxb", signing_secret="")
        with pytest.raises(ValueError, match="signing_secret .* missing for: x"):
            SlackAdapterConfig(apps=(bad,))

    def test_socket_transport_requires_app_token(self):
        bad = SlackApp(slug="x", bot_token="xoxb", signing_secret="ignored")
        with pytest.raises(ValueError, match="app_token .* missing for: x"):
            SlackAdapterConfig(apps=(bad,), transport="socket")

    @pytest.mark.parametrize("transport", ["http", "socket"])
    def test_every_transport_requires_bot_token(self, transport: str):
        bad = SlackApp(slug="x", bot_token="", signing_secret="s", app_token="xapp")
        with pytest.raises(ValueError, match="bot_token .* missing for: x"):
            SlackAdapterConfig(apps=(bad,), transport=transport)  # type: ignore[arg-type]

    def test_socket_transport_rejects_duplicate_app_slugs(self):
        """Socket mode keys apps by slug, so a duplicate would silently replace one."""
        with pytest.raises(ValueError, match="Duplicate SlackApp slug: dev"):
            SlackAdapterConfig(
                apps=(_socket_app("dev"), _socket_app("dev")), transport="socket"
            )

    def test_config_requires_at_least_one_app(self):
        with pytest.raises(ValueError, match="apps"):
            SlackAdapterConfig(apps=())

    def test_socket_transport_accepts_apps_without_signing_secret(self):
        """Signing secret is unused over the websocket — must not be required."""
        adapter = SlackAdapter(
            SlackAdapterConfig(apps=(_socket_app(),), transport="socket"),
            inner=SlackReplyBrain(),
            rest_client=MagicMock(),
            web_client_factory=lambda a: AsyncMock(),
        )
        assert adapter.transport == "socket"

    def test_unknown_transport_rejected(self):
        with pytest.raises(ValueError, match="transport"):
            SlackAdapterConfig(apps=(_http_app(),), transport="banana")  # type: ignore[arg-type]

    def test_router_unavailable_in_socket_mode(self):
        adapter = SlackAdapter(
            SlackAdapterConfig(apps=(_socket_app(),), transport="socket"),
            inner=SlackReplyBrain(),
            rest_client=MagicMock(),
            web_client_factory=lambda a: AsyncMock(),
        )
        with pytest.raises(RuntimeError, match="transport='http'"):
            _ = adapter.router
