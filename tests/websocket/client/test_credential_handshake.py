"""Every upgrade authenticates with its own client's credential, in one header."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from band.testing import fake_phoenix_server
from tests.websocket.client.credentials import Credential, credentials_sent

SECRET = "synthetic-handshake-credential"


class TestCredentialHandshake:
    async def test_reentry_sends_the_same_header_credential(
        self, credential: Credential
    ) -> None:
        async with fake_phoenix_server() as server:
            client = credential.client(server.url, SECRET)
            async with client:
                pass
            async with client:
                pass

        assert [
            (credentials_sent(upgrade), upgrade.query) for upgrade in server.upgrades
        ] == [({credential.header: SECRET}, credential.query)] * 2

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
