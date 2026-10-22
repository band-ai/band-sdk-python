from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.commands import call
from remote_agent.network import HTTPRoom


@pytest.mark.contract_linux
class TestHTTPWorkerEnvironment(unittest.TestCase):
    def test_http_worker_has_shared_minimal_environment_and_fixed_path(self) -> None:
        bootstrap = """
import email.message, http.client, json, os
class Response:
    status = 200
    def __init__(self):
        self.body = json.dumps({'keys': sorted(os.environ),
                                'path': os.environ.get('PATH'),
                                'tz': os.environ.get('TZ')}).encode()
        self.headers = email.message.Message()
        self.headers['Content-Length'] = str(len(self.body))
        self.headers['Content-Type'] = 'application/json'
    def read(self, size): return self.body[:size]
class Connection:
    def __init__(self, **kwargs): pass
    def request(self, method, path, body, headers):
        assert headers['Authorization'] == 'Bearer ' + 'a' * 32
    def getresponse(self): return Response()
    def close(self): pass
http.client.HTTPConnection = Connection
"""

        def offline_call(argv: Any, request: Any, **kwargs: Any) -> Any:
            # Retain the real worker bootstrap, argv isolation flags and env.
            # Only the connection is stubbed; no socket or credential file is used.
            argv = list(argv)
            argv[4] = bootstrap + argv[4]
            return call(argv, request, **kwargs)

        room = HTTPRoom.__new__(HTTPRoom)
        room.url, room.token = "http://127.0.0.1:1", "a" * 32
        room.authentication, room.ca_file, room.timeout = "bearer", None, 2
        parent = {
            "PATH": "/synthetic-untrusted-bin",
            "LANG": "C.UTF-8",
            "TZ": "Pacific/Honolulu",
            "HOME": "/synthetic-host-home",
            "OPENAI_API_KEY": "synthetic-provider-canary",
            "PYTHONPATH": "/synthetic-import-canary",
        }
        with (
            patch.dict(os.environ, parent, clear=True),
            patch("remote_agent.network.call", side_effect=offline_call),
        ):
            result = room._request("GET", "/v1/identity")
        self.assertEqual(set(result["keys"]), {"PATH", "LANG", "TZ"})
        self.assertEqual(result["path"], "/usr/bin:/bin")
        self.assertEqual(result["tz"], "UTC")
