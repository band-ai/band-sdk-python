"""Config + REST/WS client fixtures for E2E tests.

Session-scoped singletons: the E2E settings, the agent/user REST clients, and
the User WebSocket observer. Also tracks rooms created during the run for the
end-of-session summary.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Generator

import pytest
from band_rest import AsyncRestClient

from band.client.streaming import WebSocketClient

from tests.e2e.conftest import E2ESettings
from tests.e2e.helpers import TrackingWebSocketClient

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def e2e_config() -> E2ESettings:
    """Provide E2E settings to tests (session-scoped singleton)."""
    return E2ESettings()


@pytest.fixture(scope="session")
def e2e_created_room_ids() -> list[str]:
    """Session-scoped mutable list tracking room IDs created during the E2E run.

    A mutable container is needed because session-scoped fixtures (like the
    room allocator) append to this list during the run, and the room summary
    fixture reads it at teardown.  Using a list (not a set) preserves
    creation order for the summary log.
    """
    return []


@pytest.fixture(scope="session", autouse=True)
def e2e_room_summary(e2e_created_room_ids: list[str]) -> Generator[None, None, None]:
    """Log a summary of rooms created during the E2E test session.

    Rooms persist on the platform (no delete API for agents), so this
    summary helps operators track accumulation across runs.
    """
    yield
    if e2e_created_room_ids:
        logger.info(
            "E2E session created %d room(s) that will persist: %s",
            len(e2e_created_room_ids),
            ", ".join(e2e_created_room_ids),
        )


@pytest.fixture(scope="session")
def e2e_session_client(
    e2e_config: E2ESettings,
) -> AsyncRestClient:
    """Session-scoped REST client shared across all E2E fixtures.

    Avoids creating multiple short-lived AsyncRestClient instances in each
    session-scoped fixture. AsyncRestClient has no close() method — the
    underlying httpx client is managed internally.
    """
    if not e2e_config.band_api_key:
        pytest.skip("BAND_API_KEY not set")

    return AsyncRestClient(
        api_key=e2e_config.band_api_key,
        base_url=e2e_config.band_base_url,
    )


@pytest.fixture(scope="session")
def e2e_user_client(
    e2e_config: E2ESettings,
) -> AsyncRestClient:
    """Session-scoped REST client authenticated as the User.

    Used by ``send_trigger_message`` so the trigger comes from the User
    (not the agent). The agent runtime skips self-authored messages, so
    using the agent client would silently fail to trigger processing.
    """
    if not e2e_config.band_api_key_user:
        pytest.skip("BAND_API_KEY_USER not set (needed for user REST client)")

    return AsyncRestClient(
        api_key=e2e_config.band_api_key_user,
        base_url=e2e_config.band_base_url,
    )


@pytest.fixture
def api_client(
    e2e_user_client: AsyncRestClient,
) -> AsyncRestClient:
    """Function-scoped alias for the user REST client.

    Tests inject ``api_client`` to send trigger messages. This now
    resolves to the **user**-scoped client so the agent runtime correctly
    processes the incoming message.
    """
    return e2e_user_client


@pytest.fixture(scope="session")
def e2e_session_client_2(
    e2e_config: E2ESettings,
) -> AsyncRestClient:
    """Session-scoped REST client for the *second* test agent.

    Multi-agent E2E tests need a distinct agent identity (different API key)
    so two agents can coexist in the same room. Skips cleanly when the second
    agent is not provisioned in .env.test.
    """
    if not e2e_config.band_api_key_2:
        pytest.skip("BAND_API_KEY_2 not set (needed for multi-agent E2E tests)")

    return AsyncRestClient(
        api_key=e2e_config.band_api_key_2,
        base_url=e2e_config.band_base_url,
    )


@pytest.fixture(scope="session")
async def ws_client(
    e2e_config: E2ESettings,
) -> AsyncGenerator[TrackingWebSocketClient, None]:
    """Session-scoped WebSocket client for observing agent responses.

    Connects as the **User** (via ``band_api_key_user``) rather than
    the agent. The platform enforces one WS connection per agent, so a
    second agent connection would kill the Agent's own connection. The
    User is a room participant and receives the same ``message_created``
    events, making it a safe observer that coexists with the Agent.

    Session-scoped to avoid creating/tearing down a WS connection per test,
    which adds latency and can cause flakiness.

    Wraps the raw WebSocketClient in a TrackingWebSocketClient that tracks
    joined channels and explicitly leaves them on teardown.
    """
    if not e2e_config.band_api_key_user:
        pytest.skip("BAND_API_KEY_USER not set (needed for WS observer)")

    ws = WebSocketClient(
        ws_url=e2e_config.band_ws_url,
        api_key=e2e_config.band_api_key_user,
        agent_id=None,  # User connection, not agent
    )

    async with ws:
        tracking_ws = TrackingWebSocketClient(ws)
        yield tracking_ws
        await tracking_ws.cleanup_channels()
