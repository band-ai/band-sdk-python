from __future__ import annotations

import http.client
import json
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from remote_agent.interfaces import HTTPBackend
from remote_agent.network import HTTPService, strict_json
from remote_agent.protocol import decode
from tests.contracts.runtime.setql2 import nested


class TestJsonDepthBoundary(unittest.TestCase):
    def test_protocol_counts_containers_not_deepest_scalar(self) -> None:
        for objects in (False, True):
            for empty in (False, True):
                with self.subTest(objects=objects, empty=empty):
                    value = nested(128, objects=objects, empty=empty)
                    self.assertEqual(decode(json.dumps(value)), value)
                    with self.assertRaisesRegex(ValueError, "128 levels"):
                        decode(json.dumps(nested(129, objects=objects, empty=empty)))

    def test_http_strict_json_obeys_the_same_boundary(self) -> None:
        for empty in (False, True):
            value = nested(128, objects=True, empty=empty)
            self.assertEqual(strict_json(json.dumps(value)), value)
            with self.assertRaisesRegex(ValueError, "128 levels"):
                strict_json(json.dumps(nested(129, objects=True, empty=empty)))

    @pytest.mark.contract_posix
    def test_real_http_rejects_excessive_nesting_before_backend_dispatch(self) -> None:
        dispatch = Mock(return_value=(200, {"ok": True}))
        service = HTTPService(
            cast(HTTPBackend, SimpleNamespace(dispatch=dispatch)),
            {"r" * 32: {"actor": "room", "permissions": ["events"]}},
        )
        self.addCleanup(service.close)
        connection = http.client.HTTPConnection(
            "127.0.0.1", service.server.server_address[1], timeout=5
        )
        self.addCleanup(connection.close)
        for levels, status in ((128, 200), (129, 400), (960, 400)):
            with self.subTest(levels=levels):
                dispatch.reset_mock()
                encoded = '{"x":' * levels + "0" + "}" * levels
                connection.request(
                    "POST",
                    "/events",
                    encoded,
                    headers={
                        "Authorization": "Bearer " + "r" * 32,
                        "Content-Type": "application/json",
                    },
                )
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, status)
                self.assertEqual(dispatch.call_count, int(status == 200))
        connection.request("GET", "/health/live")
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 200)
