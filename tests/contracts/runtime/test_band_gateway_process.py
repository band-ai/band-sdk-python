from __future__ import annotations

import http.client
import json
import selectors
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

import pytest

from remote_agent.band.configuration import module_command


@pytest.mark.contract_runtime
class TestBandGatewayProcess(unittest.TestCase):
    def test_configured_non_demo_port_authentication_and_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="band-gateway-process-") as directory:
            root = Path(directory)

            def private(name: Any, value: Any) -> str:
                path = root / name
                path.write_text(value if isinstance(value, str) else json.dumps(value))
                path.chmod(0o600)
                return str(path)

            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            token = "gateway-test-only-" * 3
            model = private(
                "model.json",
                {
                    "profile": "band-contract/structured-model/1",
                    "command": [
                        sys.executable,
                        "-c",
                        'raise RuntimeError("unexpected model call")',
                    ],
                    "credentials": {},
                },
            )
            config = private(
                "gateway.json",
                {
                    "profile": "band-contract/program-gateway/1",
                    "state": str(root / "state"),
                    "platform_url": "https://band.example",
                    "author_ids": [str(uuid.uuid4())],
                    "agent_keys": {},
                    "engine_command": [
                        sys.executable,
                        "-c",
                        'raise RuntimeError("unexpected engine call")',
                    ],
                    "model_config": model,
                    "token_file": private("platform.token", token),
                    "port": port,
                },
            )
            process = subprocess.Popen(
                module_command("serve_programs", "--config", config),
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert process.stdout is not None and process.stderr is not None
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5), "gateway readiness deadline")
                    self.assertEqual(
                        json.loads(process.stdout.readline()),
                        {"event": "program_host_ready", "port": port},
                    )
                room, session = str(uuid.uuid4()), str(uuid.uuid4())
                value = {
                    "profile": "band-contract/program-host-request/1",
                    "room": room,
                    "session": session,
                    "actor": "User:" + str(uuid.uuid4()),
                    "operation": "status",
                    "request": {},
                    "context": {
                        "source": {},
                        "config": {"bindings": {}},
                        "state": {"status": "active"},
                        "history": [],
                    },
                }
                for credential, expected in [
                    (None, 401),
                    ("incorrect-token", 401),
                    (token, 200),
                ]:
                    with self.subTest(authorized=expected == 200):
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", port, timeout=5
                        )
                        try:
                            headers = {"content-type": "application/json"}
                            if credential is not None:
                                headers["authorization"] = "Bearer " + credential
                            connection.request(
                                "POST", "/v1/operations", json.dumps(value), headers
                            )
                            response = connection.getresponse()
                            body = json.loads(response.read())
                            self.assertEqual(response.status, expected)
                            if expected == 200:
                                self.assertEqual(
                                    (body["room"], body["session"]), (room, session)
                                )
                                self.assertIs(body["available"], True)
                        finally:
                            connection.close()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
                process.stderr.close()
            self.assertEqual(process.returncode, 0)
