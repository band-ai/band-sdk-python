from __future__ import annotations

import http.client
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import jsonschema

from remote_agent.authority import DurableRoom
from remote_agent.network import HTTPService, RoomAPI
from remote_agent.store import Store
from remote_agent.transport import EngineProcess
from tests.paths import CONTRACT_RUNTIME_TESTS, CONTRACTS_ROOT


class HTTPPeerFixture(unittest.TestCase):
    room: DurableRoom
    tokens: dict[str, dict[str, Any]]
    server: HTTPService

    def serve(self, **options: Any) -> HTTPService:
        server = HTTPService(RoomAPI(self.room), self.tokens, **options)
        self.addCleanup(server.close)
        return server

    def request(
        self,
        path: Any,
        data: Any = None,
        token: Any = "u" * 32,
        method: str = "POST",
        server: Any = None,
    ) -> tuple[int, Any]:
        server = server or self.server
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
        try:
            body = json.dumps(data).encode() if data is not None else b""
            connection.request(
                method,
                path,
                body=body,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()


class NetworkFixture(HTTPPeerFixture):
    def wire_schema(self, name: Any) -> Any:
        # The room runs hybrid-assistant, which waits for replies: the protocol/6 registry.
        schema = json.loads(
            (CONTRACTS_ROOT / "spec/schemas/protocol-replies.schema.json").read_text()
        )
        self.assertIn(name, schema["$defs"], "DF-05 standalone wire schema is missing")
        return jsonschema.Draft202012Validator(
            dict(schema, **{"$ref": "#/$defs/" + name})
        )

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="network-test-")
        cls.root = Path(cls.temp.name)
        cls.cert, cls.key = cls.root / "certificate.pem", cls.root / "key.pem"
        result = subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=IP:127.0.0.1,DNS:localhost",
                "-keyout",
                str(cls.key),
                "-out",
                str(cls.cert),
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Test certificate generation failed")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.folder.cleanup)
        self.store = Store(Path(self.folder.name) / "room.sqlite3")
        self.addCleanup(self.store.close)
        contract = CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"
        self.engine = EngineProcess(contract)
        self.addCleanup(self.engine.close)
        self.room = DurableRoom(
            self.store,
            self.engine,
            session="s",
            clock=lambda: 0,
            bindings={"requester": "user", "assistant": "agent"},
        )
        self.tokens = {
            "u" * 32: {"actor": "user", "permissions": ["room", "observe"]},
            "a" * 32: {"actor": "agent", "permissions": ["room"]},
            "o" * 32: {"actor": "operator", "permissions": ["observe"]},
        }
        self.server = self.serve()

    def question(self) -> dict[str, Any]:
        return {
            "id": "q1",
            "session": "s",
            "kind": "question",
            "payload": {"text": "ping"},
            "expected_version": 0,
        }


class AdversarialViewFixture(HTTPPeerFixture):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="df05-network-")
        self.addCleanup(self.folder.cleanup)
        self.store = Store(Path(self.folder.name) / "room.sqlite3")
        self.addCleanup(self.store.close)
        self.engine = EngineProcess(
            CONTRACT_RUNTIME_TESTS / "fixtures/adversarial-plan.band"
        )
        self.addCleanup(self.engine.close)
        self.room = DurableRoom(
            self.store,
            self.engine,
            session="df05",
            clock=lambda: 0,
            bindings={"lead": "ada", "owner": "rafael", "adversary": "victor"},
        )
        self.tokens = {
            letter * 32: {"actor": actor, "permissions": ["room"]}
            for letter, actor in (("l", "ada"), ("o", "rafael"), ("a", "victor"))
        }
        self.server = self.serve()

    def four_messages(self) -> None:
        messages = [
            (
                "rafael",
                "r1",
                "round1",
                {
                    "piece": "S3",
                    "brief": "inputs/S3.md",
                    "findings": "findings/S3-r1.md",
                    "text": "Blind architecture critique.",
                },
            ),
            ("victor", "ack1", "ack", {"request": "r1"}),
            (
                "victor",
                "c1",
                "critique",
                {
                    "request": "r1",
                    "file": "findings/S3-r1.md",
                    "sha256": "a" * 64,
                    "critical": 1,
                    "suggestion": 2,
                    "nit": 0,
                    "text": "One critical boundary and two alternatives.",
                },
            ),
            (
                "rafael",
                "r2",
                "round2",
                {
                    "critique": "c1",
                    "plan": "plans/S3.md",
                    "plan_sha256": "b" * 64,
                    "findings": "findings/S3-r2.md",
                    "text": "Attack the folded plan and keep the last word.",
                },
            ),
        ]
        for version, (actor, message_id, kind, payload) in enumerate(messages):
            result = self.room.publish(
                {
                    "id": message_id,
                    "session": "df05",
                    "kind": kind,
                    "expected_version": version,
                    "payload": payload,
                },
                actor,
            )
            self.assertEqual(result["status"], "accepted", result)


class LegacyViewAPI(RoomAPI):
    def dispatch(
        self, method: Any, path: Any, grant: Any, data: Any
    ) -> tuple[Any, ...]:
        if path == "/v1/view":
            if data != {}:
                raise AssertionError("Runner changed the legacy view request")
            return 200, self.room.view(grant["actor"])
        status, result = super().dispatch(method, path, grant, data)
        if path == "/v1/identity":
            result.pop("definition_profile", None)
        return status, result


class LegacyInboxAPI(RoomAPI):
    def dispatch(
        self, method: Any, path: Any, grant: Any, data: Any
    ) -> tuple[Any, ...]:
        status, result = super().dispatch(method, path, grant, data)
        if path == "/v1/identity":
            result.pop("inbox_profile", None)
        return status, result
