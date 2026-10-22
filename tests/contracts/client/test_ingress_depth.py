"""Keep request and callback ingress at its original 128-container boundary."""

from __future__ import annotations

import json

import httpx
import pytest

from band.contracts.errors import ContractProtocolError
from band.contracts.transport import ContractTransport
from band.contracts.wire import JsonObject, encode_json
from remote_agent.protocol import decode
from tests.contracts.client.kit import RoomPeer
from tests.contracts.runtime.setql8 import nested


class TestIngressDepth:
    @pytest.mark.parametrize("levels", [127, 128])
    async def test_ordinary_http_responses_keep_the_callback_depth_limit(
        self, levels: int
    ) -> None:
        value = nested(levels)
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=json.dumps(value))
                )
            ) as http,
            ContractTransport(RoomPeer().connection(), http=http) as client,
        ):
            if levels == 127:
                assert await client.request("GET", "/status") == value
            else:
                with pytest.raises(ContractProtocolError, match="nesting"):
                    await client.request("GET", "/status")

    def test_outbound_request_depth_is_not_relaxed_for_journal_responses(self) -> None:
        accepted = nested(127)
        assert json.loads(encode_json(accepted)) == accepted

        with pytest.raises(ContractProtocolError, match="nesting"):
            encode_json(nested(128))

    def test_callback_depth_is_not_relaxed_for_journal_responses(self) -> None:
        accepted: JsonObject = {
            "type": "handled",
            "state": nested(126),
            "message": None,
        }
        assert decode(json.dumps(accepted)) == accepted
        rejected: JsonObject = {
            "type": "handled",
            "state": nested(127),
            "message": None,
        }

        with pytest.raises(ValueError, match="128 levels"):
            decode(json.dumps(rejected))
