"""Credential modes for the WebSocketClient tests: how a client of each mode
is built, and what its upgrades must carry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from phoenix_channels_python_client import ProtocolVersion
from phoenix_channels_python_client.authentication import AuthenticationHeader

from band.client.streaming import WebSocketClient
from band.testing.phoenix_server import Upgrade
from tests.identifiers import AGENT_ID

VSN = {"vsn": ProtocolVersion.V2.vsn}


@dataclass(frozen=True)
class Credential:
    """One way a client authenticates: the upgrade header its credential rides
    in, and the whole query each of its upgrades carries."""

    header: AuthenticationHeader
    query: Mapping[str, str]

    def client(self, ws_url: str, secret: str, **options: Any) -> WebSocketClient:
        """A client authenticating with ``secret`` in this mode."""
        match self.header:
            case AuthenticationHeader.API_KEY:
                return WebSocketClient(ws_url, secret, AGENT_ID, **options)
            case AuthenticationHeader.TOKEN:
                return WebSocketClient(ws_url, access_token=secret, **options)


# An agent's API key names its agent in the query; a user's token names nothing.
CREDENTIALS = [
    pytest.param(
        Credential(AuthenticationHeader.API_KEY, {**VSN, "agent_id": AGENT_ID}),
        id="api-key",
    ),
    pytest.param(Credential(AuthenticationHeader.TOKEN, VSN), id="access-token"),
]


def credentials_sent(upgrade: Upgrade) -> dict[str, str]:
    """The authentication headers an upgrade carried."""
    return {
        header: upgrade.headers[header]
        for header in AuthenticationHeader
        if header in upgrade.headers
    }
