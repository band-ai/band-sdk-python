"""Drive an author process against a loopback authority with a lost acknowledgment."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO

from tests.paths import REPO_ROOT


class AuthorPeer(ThreadingHTTPServer):
    """Accept one job and retain its first completion without an acknowledgment."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), AuthorHandler)
        self.key = "author-process-test-key-" * 3
        self.job = {
            "id": "job-one",
            "attempt": 1,
            "request": {
                "operation": "generate",
                "history": [
                    {
                        "id": "message-one",
                        "role": "requester",
                        "text": "Normalize these labels",
                    }
                ],
            },
        }
        self.claimed = threading.Event()
        self.first_completion = threading.Event()
        self.acknowledged = threading.Event()
        self.responses: list[bytes] = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("Author peer did not stop")


class AuthorHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *_args: Any) -> None:
        return

    def do_POST(self) -> None:
        peer = self.server
        assert isinstance(peer, AuthorPeer)
        if self.headers.get("x-api-key") != peer.key:
            self.send_error(403)
            return
        body = self.rfile.read(int(self.headers["content-length"]))
        if self.path == "/api/v1/agent/contracts/authoring/claim":
            result = {"job": None if peer.claimed.is_set() else peer.job}
            peer.claimed.set()
        elif self.path == "/api/v1/agent/contracts/authoring/job-one/result":
            peer.responses.append(body)
            if len(peer.responses) == 1:
                peer.first_completion.set()
                self.close_connection = True
                return
            result = {"id": "job-one", "status": "ready"}
            peer.acknowledged.set()
        else:
            self.send_error(404)
            return
        encoded = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def private_file(root: Path, name: str, value: str | dict[str, Any]) -> Path:
    path = root / name
    path.write_text(
        value if isinstance(value, str) else json.dumps(value), encoding="utf-8"
    )
    path.chmod(0o600)
    return path


def author_configuration(root: Path, peer: AuthorPeer) -> tuple[Path, Path]:
    calls = root / "model-calls"
    provider = private_file(
        root,
        "provider.py",
        "import json, sys\n"
        "from pathlib import Path\n"
        "request = json.load(sys.stdin)\n"
        "assert 'Normalize these labels' in request['prompt']\n"
        "assert request['schema']['properties']['derivation']['items']['properties']['evidence']['enum'] == ['message-one']\n"
        "with Path(sys.argv[1]).open('a', encoding='utf-8') as log: log.write('call\\n')\n"
        "sys.stdout.write(json.dumps({'source': 'typed test artifact', 'derivation': [{'evidence': 'message-one', 'rule': 'Normalize labels'}], 'unresolved': []}) + '\\n')\n",
    )
    model = private_file(
        root,
        "model.json",
        {
            "profile": "band-contract/structured-model/1",
            "command": [sys.executable, str(provider), str(calls)],
            "credentials": {},
        },
    )
    configuration = private_file(
        root,
        "author.json",
        {
            "profile": "band-contract/author-service/1",
            "state": str(root / "state"),
            "platform_url": f"http://127.0.0.1:{peer.server_port}",
            "key_file": str(private_file(root, "agent.key", peer.key)),
            "model_config": str(model),
            "max_model_calls": 1,
            "poll_interval_ms": 1000,
        },
    )
    return configuration, calls


def read_ready(stream: BinaryIO, messages: queue.Queue[bytes]) -> None:
    messages.put(stream.readline())


class AuthorProcess:
    def __init__(self, root: Path, configuration: Path) -> None:
        bootstrap = (
            "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
            "runpy.run_module('remote_agent.band.author_service', run_name='__main__')"
        )
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                bootstrap,
                str(REPO_ROOT / "src"),
                "--config",
                str(configuration),
            ],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if self.process.stdout is None:
            raise RuntimeError("Author output pipe is missing")
        self.messages: queue.Queue[bytes] = queue.Queue()
        self.reader = threading.Thread(
            target=read_ready, args=(self.process.stdout, self.messages), daemon=True
        )
        self.reader.start()

    def readiness(self) -> dict[str, Any]:
        try:
            line = self.messages.get(timeout=8)
        except queue.Empty as error:
            raise AssertionError("Author did not report readiness") from error
        if not line:
            _, diagnostic = self.process.communicate(timeout=5)
            raise AssertionError(
                "The SDK author process exited before accepting a job: "
                + diagnostic.decode(errors="replace")[-2000:]
            )
        return json.loads(line)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        if self.reader.is_alive():
            raise RuntimeError("Author output reader did not stop")
