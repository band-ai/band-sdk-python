"""A loopback MCP server cannot be built without a bearer token."""

from __future__ import annotations

import pytest

from band.integrations.mcp.local_server import LocalMCPServer
from tests.mcp.loopbackauth.kit import SERVER_NAME


class TestTokenRequired:
    def test_an_empty_token_is_rejected(self) -> None:
        """An empty token would match an empty bearer credential."""
        with pytest.raises(ValueError, match="auth_token"):
            LocalMCPServer(SERVER_NAME, [], auth_token="")
