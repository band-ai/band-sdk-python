"""A refused credential ends the session, at entry or on a reconnect, without
another attempt."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from phoenix_channels_python_client import SocketError

from band.client.streaming import WebSocketDisconnectReason, WebSocketUpgradeError
from band.testing import fake_phoenix_server
from tests.sessionpolicies import BEYOND_ANY_RETRY_S
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.credentials import Credential
from tests.websocket.client.kit import FORBIDDEN, observe_retries

SECRET = "synthetic-refused-credential"


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestCredentialRefusal:
    async def test_a_refused_credential_ends_the_entry_after_one_upgrade(
        self, credential: Credential
    ) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            client = credential.client(server.url, SECRET)
            with (
                server.refusing_upgrades(HTTPStatus.FORBIDDEN),
                pytest.raises(WebSocketUpgradeError) as refused,
            ):
                await client.__aenter__()
            await clock.elapse(BEYOND_ANY_RETRY_S)

        assert refused.value.status_code == HTTPStatus.FORBIDDEN
        assert client.last_disconnect_reason == FORBIDDEN
        assert len(server.upgrades) == 1

    async def test_a_credential_refused_on_reconnect_ends_the_session_once(
        self, credential: Credential
    ) -> None:
        verdicts: list[WebSocketDisconnectReason] = []
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            client = credential.client(server.url, SECRET, on_terminal=verdicts.append)
            async with (
                client,
                background(client.run_forever(install_signal_handlers=False)) as run,
            ):
                with (
                    observe_retries() as retries,
                    server.refusing_upgrades(HTTPStatus.FORBIDDEN),
                ):
                    await server.abort_connection()
                    await clock.elapse(await retries.get())
                    with pytest.raises(SocketError, match="403") as failure:
                        await run
                    await clock.elapse(BEYOND_ANY_RETRY_S)

        assert SECRET not in str(failure.value)
        assert verdicts == [FORBIDDEN]
        assert len(server.upgrades) == 2
