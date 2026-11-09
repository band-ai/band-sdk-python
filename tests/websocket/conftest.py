"""Fixtures shared by the WebSocket transport tests."""

from __future__ import annotations

import pytest

from tests.websocket.client.credentials import CREDENTIALS, Credential


@pytest.fixture(params=CREDENTIALS)
def credential(request: pytest.FixtureRequest) -> Credential:
    """Each way a client authenticates."""
    return request.param
