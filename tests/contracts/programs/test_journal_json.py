"""Keep private journal responses within their value and byte constraints."""

from __future__ import annotations

import httpx
import pytest

from band.contracts.errors import ContractCapacityError, ContractProtocolError
from band.contracts.programs import ContractProgramHostClient
from band.contracts.wire import MAX_WIRE_BYTES
from tests.contracts.programs.peer import ProgramPeer


class TestJournalJson:
    @pytest.mark.parametrize(
        "encoded",
        [
            b'{"value":1,"value":2}',
            b'{"value":1.5}',
            b'{"value":NaN}',
            b'{"value":9007199254740992}',
            b'{"value":"\\ud800"}',
            b"[]",
            b'{"value":' + b"[" * 10_000 + b"0" + b"]" * 10_000 + b"}",
        ],
    )
    async def test_host_rejects_invalid_json_before_accepting_a_response(
        self, encoded: bytes
    ) -> None:
        peer = ProgramPeer()
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=encoded)
                )
            ) as http,
            ContractProgramHostClient(
                peer.connection, scope=peer.scope, http=http
            ) as client,
        ):
            with pytest.raises(ContractProtocolError):
                await client.journal()

    async def test_host_keeps_the_original_response_byte_limit(self) -> None:
        peer = ProgramPeer()
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, content=b" " * (MAX_WIRE_BYTES + 1)
                    )
                )
            ) as http,
            ContractProgramHostClient(
                peer.connection, scope=peer.scope, http=http
            ) as client,
        ):
            with pytest.raises(ContractCapacityError, match="response"):
                await client.journal()
