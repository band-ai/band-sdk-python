from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from functools import partial
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from tests.contracts.runtime.httppeer import HTTPPeer
from tests.paths import CONTRACT_RUNTIME_ROOT


class RoomCLIFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="room-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = CONTRACT_RUNTIME_ROOT.parent
        self.token = "u" * 32
        self.token_file = self.root / "token"
        self.token_file.write_text(self.token + "\n")
        self.token_file.chmod(0o600)
        self.requests, self.proposals, self.polls = [], [], []
        self.disconnect = False
        self.inbox_failure = False
        self.echo_secret = False
        self.definition: dict[str, Any] = {"rules": ["question"]}
        self.definition_identity = {"digest": "d"}
        self.rejection_reason = None
        self.view_failure = False
        self.legal: list[dict[str, Any]] = [{"kind": "question"}]
        self.server = self.serve()

    def serve(self) -> Any:
        peer = HTTPPeer(partial(RoomCLIHandler, self))
        self.addCleanup(peer.close)
        peer.start()
        return peer.server

    def cli(self, *args: Any, url: Any = None) -> Any:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "remote_agent.room_cli",
                url or "http://127.0.0.1:" + str(self.server.server_port),
                str(self.token_file),
                "--state-dir",
                str(self.root / "state"),
                *args,
            ],
            env=dict(os.environ, PYTHONPATH=str(self.runtime)),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def success(self, *args: Any, **kwargs: Any) -> Any:
        result = self.cli(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def text_definition(
        self,
        *,
        byte_min: int = 0,
        byte_max: int = 64,
        grapheme_min: int = 0,
        grapheme_max: int = 1,
    ) -> None:
        self.definition = {
            "types": {
                "question": {
                    "fields": {
                        "text": {
                            "kind": "text",
                            "encoding": "utf-8",
                            "bytes": {"min": byte_min, "max": byte_max},
                            "graphemes": {"min": grapheme_min, "max": grapheme_max},
                        }
                    }
                }
            }
        }


class RoomCLIHandler(BaseHTTPRequestHandler):
    def __init__(self, fixture: RoomCLIFixture, *args: Any, **kwargs: Any) -> None:
        self.fixture = fixture
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.dispatch()

    def do_POST(self) -> None:
        self.dispatch()

    def dispatch(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.fixture.requests.append((self.command, self.path, raw))
        actor = (
            "other"
            if self.headers.get("Authorization") == "Bearer " + "a" * 32
            else "user"
        )
        identity: dict[str, Any] = {
            "session": "s",
            "actor": actor,
            "identity": {"digest": "d"},
            "profile": "contract-room/http/1",
            "idempotent": True,
            "inbox_profile": "contract-room/inbox/1",
        }
        status = 200
        if self.path == "/v1/identity":
            result = identity
        elif self.path == "/v1/definition":
            result = {key: identity[key] for key in ("session", "actor", "identity")}
            result.update(
                definition=self.fixture.definition,
                identity=self.fixture.definition_identity,
                profile="contract-room/definition/1",
            )
        elif self.path == "/v1/view":
            status = 503 if self.fixture.view_failure else 200
            result = {
                "view": {
                    "state": "ready",
                    "legal": self.fixture.legal,
                    "obligations": [],
                    "definition": {"rules": ["question"]},
                }
            }
        elif self.path == "/v1/proposals":
            self.fixture.proposals.append(raw)
            if self.fixture.disconnect:
                self.fixture.disconnect = False
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            message = json.loads(raw)
            rejected = message["kind"] == "reject"
            duplicate = (
                len(self.fixture.proposals) > 1 and raw == self.fixture.proposals[-2]
            )
            result = {
                "status": "rejected"
                if rejected
                else "duplicate"
                if duplicate
                else "accepted",
                "id": message["id"],
                "actor": actor,
                "session": "s",
                "recipients": ["room-peer"],
            }
            if rejected and self.fixture.rejection_reason:
                result.update(
                    reason=self.fixture.rejection_reason, detail={"unchanged": True}
                )
        elif self.path == "/v1/inbox":
            page = json.loads(raw)
            self.fixture.polls.append(page)
            status = 409 if self.fixture.inbox_failure else 200
            result = {
                "cursor": 7,
                "events": [] if page["after"] == 7 else [{"id": "event"}],
            }
        else:
            status, result = 404, {"error": "unknown"}
        if self.fixture.echo_secret:
            result = {"error": self.fixture.token}
            status = 403
        encoded = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
