from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections.abc import Callable
from functools import partial
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from remote_agent.band.author_service import generate, transport
from remote_agent.band.authoring import Journal, canonical
from remote_agent.band.studio import StudioAgent
from tests.contracts.runtime.httppeer import HTTPPeer
from tests.paths import CONTRACTS_ROOT

JOB = {
    "operation": "generate",
    "history": [{"id": "m1", "role": "human", "text": "Preserve ping and close."}],
}


def result(*, large: bool = False) -> dict[str, Any]:
    fixture = (
        CONTRACTS_ROOT
        / "spec/conformance/case-v2-frontmatter-and-standard-discovery.json"
    )
    source = json.loads(fixture.read_text())["requests"][0]["source"]["content"]
    return {
        "source": source,
        "unresolved": [],
        "derivation": (
            [{"evidence": "m1", "rule": "\U0001f600" * 1000}] * 90
            if large
            else [
                {"evidence": "m1", "rule": "Preserve the existing behavior: caf\u00e9."}
            ]
        ),
    }


class AuthorWireCapacityFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="author-wire-capacity-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.requests, self.claims, self.jobs, self.events = [], [], [], []
        self.reply: Callable[[bytes], tuple[int, dict[str, Any] | bytes]] = (
            lambda body: (
                200,
                {"id": "saved", "attempt": 0, "status": "ready"},
            )
        )

        peer = HTTPPeer(partial(AuthorWireHandler, self))
        self.addCleanup(peer.close)
        peer.start()
        self.server = peer.server
        key = self.root / "author.key"
        key.write_text("k" * 32)
        key.chmod(0o600)
        self.config = {
            "platform_url": "http://127.0.0.1:" + str(self.server.server_port),
            "key_file": str(key),
        }
        self.journal = self.open_journal()

    def open_journal(self) -> Any:
        journal = Journal(self.root / "state")
        self.addCleanup(journal.close)
        return journal

    def agent(self) -> Any:
        return StudioAgent(
            self.journal, transport(self.config), on_hold=self.events.append
        )

    def claim(self) -> None:
        self.jobs.extend(
            [
                {"id": "saved", "attempt": 0, "request": JOB},
                {"id": "later", "attempt": 0, "request": JOB},
            ]
        )
        self.agent().poll()

    def provider(self, value: Any) -> Any:
        output = self.root / "provider-output.json"
        output.write_text(canonical(value))
        calls = self.root / "provider-calls"
        script = self.root / "provider.py"
        script.write_text("""import pathlib, sys
sys.stdin.buffer.read()
with pathlib.Path(sys.argv[2]).open('a') as stream: stream.write('call\\n')
sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())
""")
        model = self.root / "model.json"
        model.write_text(
            json.dumps(
                {
                    "profile": "band-contract/structured-model/1",
                    "command": [sys.executable, str(script), str(output), str(calls)],
                    "credentials": {},
                }
            )
        )
        model.chmod(0o600)
        configuration = dict(
            self.config, model_config=str(model), model_timeout_seconds=10
        )
        return lambda job: generate(
            self.root, "Use the supplied grammar.", job, configuration
        )


class AuthorWireHandler(BaseHTTPRequestHandler):
    def __init__(
        self, owner: AuthorWireCapacityFixture, *args: Any, **kwargs: Any
    ) -> None:
        self.owner = owner
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("x-api-key") != "k" * 32:
            self.send_error(403)
            return
        if self.path.endswith("/claim"):
            self.owner.claims.append(json.loads(body)["id"])
            status, value = (
                200,
                {"job": self.owner.jobs.pop(0) if self.owner.jobs else None},
            )
        else:
            self.owner.requests.append(body)
            status, value = self.owner.reply(json.loads(body))
        encoded = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The bounded reader intentionally stops at its response limit.
