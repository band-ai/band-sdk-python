"""Pytest configuration for band-mcp tests.

Fixtures from band-testing-python are auto-loaded via pytest entry point.
We override mock_api_client to add v0.0.4 split namespace properties.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from band_mcp.shared import AppContext


def _assert_no_method_name_collisions() -> None:
    """Verify method names are unique within agent and human namespace groups.

    The shared mock strategy in mock_api_client maps all agent namespaces to
    one MagicMock and all human namespaces to another. If two namespaces in the
    same group ever share a method name, tests would silently pass with wrong
    assertions.
    """
    from band_rest import RestClient

    try:
        client = RestClient(api_key="dummy", base_url="http://localhost")
    except Exception as exc:
        raise AssertionError(
            f"Could not instantiate RestClient for collision check: {exc}"
        ) from exc

    for prefix in ("agent_api_", "human_api_"):
        method_to_namespace: dict[str, str] = {}
        for attr_name in dir(client):
            if not attr_name.startswith(prefix):
                continue
            obj = getattr(client, attr_name)
            methods = [
                m
                for m in dir(obj)
                if not m.startswith("_") and callable(getattr(obj, m))
            ]
            for method in methods:
                if method in method_to_namespace:
                    raise AssertionError(
                        f"Method name collision: '{method}' exists in both "
                        f"'{method_to_namespace[method]}' and '{attr_name}'. "
                        f"The shared mock strategy in conftest.py is no longer safe. "
                        f"Split into per-namespace mock objects."
                    )
                method_to_namespace[method] = attr_name


@pytest.fixture(scope="session", autouse=True)
def _check_mock_safety() -> None:
    """Session-scoped guard against mock method-name collisions."""
    _assert_no_method_name_collisions()


@dataclass
class MockRequestContext:
    """Mock request context for testing."""

    lifespan_context: AppContext


class MockContext:
    """Mock MCP Context for testing with mocked API client.

    The mock client is mapped onto both ``human_rest`` and ``agent_rest``
    slots on the AppContext so tests that drive either surface see the same
    shared mock.
    """

    def __init__(self, client: Mock):
        self.request_context = MockRequestContext(
            lifespan_context=AppContext(human_rest=client, agent_rest=client)
        )


@pytest.fixture
def mock_api_client(mock_agent_api: MagicMock, mock_human_api: MagicMock) -> AsyncMock:
    """Create a mocked RestClient with v0.0.4 split namespace properties.

    Maps all new namespace properties to the shared mock_agent_api / mock_human_api
    MagicMock objects. Since method names are unique across namespaces, all existing
    test assertions work unchanged.

    NOTE: This strategy assumes method names remain unique across namespaces.
    If two namespaces ever share a method name, tests could silently pass with
    wrong assertions. In that case, split into per-namespace mock objects.
    """
    client = AsyncMock()

    # Agent namespaces
    client.agent_api_chats = mock_agent_api
    client.agent_api_identity = mock_agent_api
    client.agent_api_messages = mock_agent_api
    client.agent_api_events = mock_agent_api
    client.agent_api_participants = mock_agent_api
    client.agent_api_peers = mock_agent_api
    client.agent_api_context = mock_agent_api
    client.agent_api_contacts = mock_agent_api

    # Human namespaces
    client.human_api_agents = mock_human_api
    client.human_api_chats = mock_human_api
    client.human_api_messages = mock_human_api
    client.human_api_participants = mock_human_api
    client.human_api_profile = mock_human_api
    client.human_api_peers = mock_human_api
    client.human_api_contacts = mock_human_api

    return client


@pytest.fixture
def mock_ctx(mock_api_client: Mock) -> MockContext:
    """Create a mock Context with a mocked API client for unit tests."""
    return MockContext(client=mock_api_client)
