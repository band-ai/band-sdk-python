from __future__ import annotations

import unittest
from functools import partial

import pytest

from remote_agent.network import HTTPRoom
from remote_agent.transport import TransportError
from tests.contracts.runtime.httpauthentication import AuthenticationHandler
from tests.contracts.runtime.httppeer import HTTPPeer


class TestHTTPAuthentication(unittest.TestCase):
    @pytest.mark.contract_linux
    def test_band_credentials_use_only_the_api_key_header_on_every_request(
        self,
    ) -> None:
        observed = []
        legacy_view = {"view": {"definition": {}}}
        peer = HTTPPeer(
            partial(AuthenticationHandler, observed=observed, legacy_view=legacy_view)
        )
        self.addCleanup(peer.close)
        peer.start()
        server = peer.server
        try:
            client = HTTPRoom(
                "http://127.0.0.1:" + str(server.server_port),
                "k" * 32,
                authentication="band-api-key",
                require_inbox=True,
            )
            self.assertEqual(client.view("Agent:actor"), legacy_view)
            self.assertEqual(len(observed), 2)
            for headers in observed:
                self.assertEqual(headers["X-API-Key"], "k" * 32)
                self.assertNotIn("Authorization", headers)
        finally:
            peer.close()

    def test_unknown_authentication_scheme_is_refused_before_any_connection(
        self,
    ) -> None:
        with self.assertRaisesRegex(TransportError, "authentication"):
            HTTPRoom("http://127.0.0.1:1", "k" * 32, authentication="arbitrary-header")
