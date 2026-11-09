"""Every upgrade authenticates with its own client's credential, in one header."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from band.testing import fake_phoenix_server
from tests.websocket.client.kit import VSN_ONLY, Credential, credentials_sent

SECRET = "synthetic-handshake-credential"
# A guard against a hung reconnect, not part of the claim.
RECONNECT_GUARD_S = 10


class TestCredentialHandshake:
    async def test_reconnects_and_reentry_send_the_same_header_credential(
        self, credential: Credential
    ) -> None:
        reconnected = asyncio.Event()

        async def on_reconnect() -> None:
            reconnected.set()

        async with fake_phoenix_server() as server:
            client = credential.client(server.url, SECRET, on_reconnect=on_reconnect)
            async with client:
                await server.close_connection(code=1012)
                await asyncio.wait_for(reconnected.wait(), RECONNECT_GUARD_S)
            async with client:
                pass

        assert [
            (credentials_sent(upgrade), upgrade.query) for upgrade in server.upgrades
        ] == [({credential.header: SECRET}, VSN_ONLY)] * 3

    async def test_concurrent_clients_each_send_their_own_credential(
        self, credential: Credential
    ) -> None:
        secrets = ["synthetic-first-credential", "synthetic-second-credential"]
        async with fake_phoenix_server() as server, AsyncExitStack() as clients:
            await asyncio.gather(
                *(
                    clients.enter_async_context(credential.client(server.url, secret))
                    for secret in secrets
                )
            )

        assert sorted(
            credentials_sent(upgrade)[credential.header] for upgrade in server.upgrades
        ) == sorted(secrets)
