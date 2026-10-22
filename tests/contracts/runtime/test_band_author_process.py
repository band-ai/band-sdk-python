from __future__ import annotations

import json
import selectors
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from remote_agent.band.configuration import module_command
from tests.contracts.runtime.authorservicepeer import AuthorCompletionHandler
from tests.contracts.runtime.httppeer import HTTPPeer


@pytest.mark.contract_runtime
class TestBandAuthorProcess(unittest.TestCase):
    def test_restart_retries_the_exact_completion_without_another_model_call(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="band-author-process-") as directory:
            root = Path(directory)
            responses = []
            completed = threading.Event()
            first_completion = threading.Event()
            claimed = threading.Event()
            credential = "author-test-only-key-" * 3
            job = {
                "id": "job-one",
                "attempt": 1,
                "request": {
                    "operation": "generate",
                    "history": [
                        {
                            "id": "m1",
                            "role": "requester",
                            "text": "Normalize these labels",
                        }
                    ],
                },
            }
            peer = HTTPPeer(
                partial(
                    AuthorCompletionHandler,
                    credential=credential,
                    job=job,
                    responses=responses,
                    completed=completed,
                    first_completion=first_completion,
                    claimed=claimed,
                )
            )
            self.addCleanup(peer.close)
            peer.start()
            server = peer.server

            def private(name: Any, text: Any) -> str:
                path = root / name
                path.write_text(text if isinstance(text, str) else json.dumps(text))
                path.chmod(0o600)
                return str(path)

            calls = root / "model-calls"
            provider = private(
                "provider.py",
                """import json,sys
from pathlib import Path
request=json.load(sys.stdin)
assert 'Normalize these labels' in request['prompt']
assert request['schema']['properties']['derivation']['items']['properties']['evidence']['enum']==['m1']
with Path(sys.argv[1]).open('a') as log: log.write('call\\n')
print(json.dumps({'source':'typed test artifact','derivation':[{'evidence':'m1','rule':'Normalize labels'}],'unresolved':[]}))
""",
            )
            model = private(
                "model.json",
                {
                    "profile": "band-contract/structured-model/1",
                    "command": [sys.executable, provider, str(calls)],
                    "credentials": {},
                },
            )
            config = private(
                "author.json",
                {
                    "profile": "band-contract/author-service/1",
                    "state": str(root / "state"),
                    "platform_url": "http://127.0.0.1:" + str(server.server_port),
                    "key_file": private("agent.key", credential),
                    "model_config": model,
                    "max_model_calls": 1,
                    "poll_interval_ms": 1000,
                },
            )
            processes = []

            def stop(process: Any) -> None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
                process.stderr.close()

            def start() -> Any:
                process = subprocess.Popen(
                    module_command("author_service", "--config", config),
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                assert process.stdout is not None and process.stderr is not None
                processes.append(process)
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5), "author readiness deadline")
                    line = process.stdout.readline()
                    self.assertEqual(
                        json.loads(line), {"event": "protocol_author_ready"}
                    )
                return process

            try:
                first = start()
                self.assertTrue(
                    first_completion.wait(5),
                    "expected authenticated completion on the actual author route",
                )
                stop(first)
                second = start()
                self.assertTrue(
                    completed.wait(5),
                    "durable completion was not retried after restart",
                )
                stop(second)
                self.assertEqual(responses, [responses[0], responses[0]])
                self.assertEqual(calls.read_text().splitlines(), ["call"])
                self.assertEqual(json.loads(responses[0])["attempt"], 1)
            finally:
                for process in processes:
                    stop(process)
                peer.close()
