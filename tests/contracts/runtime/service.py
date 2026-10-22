from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from tests.contracts.runtime.processgate import ProcessGate
from tests.contracts.runtime.program import SOURCE
from tests.paths import CONTRACT_RUNTIME_ROOT, CONTRACTS_ROOT, REPO_ROOT


class ServiceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="contract-service-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = CONTRACTS_ROOT
        self.contract = self.root / "session.band"
        shutil.copy2(
            self.repo / "contract-lab/contracts/hybrid-assistant.band", self.contract
        )
        self.processes = []
        self.addCleanup(self.cleanup)
        executable = shutil.which("elixir")
        assert executable is not None, (
            "The contract_elixir capability must be available"
        )
        command = [executable, "--erl", "+S 2:2"]
        for ebin in sorted((self.repo / "contract-lab/_build/dev/lib").glob("*/ebin")):
            command.extend(["-pa", str(ebin)])
        command.append(str(self.repo / "contract-lab/examples/engine_endpoint.exs"))
        self.engine = {"contract": str(self.contract), "command": command}
        self.tokens = self.private(
            "tokens.json",
            {
                "u" * 32: {"actor": "user", "permissions": ["room", "observe"]},
                "a" * 32: {"actor": "agent", "permissions": ["room"]},
            },
        )

    def private(self, name: Any, value: Any) -> str:
        path = self.root / name
        path.write_text(json.dumps(value) if not isinstance(value, str) else value)
        path.chmod(0o600)
        return str(path)

    def room_config(self) -> dict[str, Any]:
        return {
            "profile": "band-contract/service/1",
            "kind": "room",
            "engine": self.engine,
            "store": {"path": str(self.root / "room/state.sqlite3")},
            "http": {"host": "127.0.0.1", "port": 0, "tokens_file": self.tokens},
            "poll_interval_ms": 100,
            "room": {
                "session": "s",
                "bindings": {"requester": "user", "assistant": "agent"},
            },
        }

    def agent_config(self, url: Any, provider: Any = None) -> dict[str, Any]:
        source = self.private("program.py", SOURCE)
        tokens = self.private(
            "agent-tokens.json",
            {"o" * 32: {"actor": "operator", "permissions": ["observe"]}},
        )
        token = self.private("room-token", "a" * 32 + "\n")
        return {
            "profile": "band-contract/service/1",
            "kind": "agent",
            "engine": self.engine,
            "store": {"path": str(self.root / "agent/state.sqlite3")},
            "http": {"host": "127.0.0.1", "port": 0, "tokens_file": tokens},
            "poll_interval_ms": 100,
            "agent": {
                "actor": "agent",
                "program_source": source,
                "artifact_directory": str(self.root / "agent/artifacts"),
                "room": {"url": url, "token_file": token},
                "max_repairs": 0,
                "model_timeout_ms": 2000,
                "provider": {
                    "command": provider
                    or [
                        sys.executable,
                        "-c",
                        "raise RuntimeError('unexpected model call')",
                    ]
                },
            },
        }

    def launch(
        self,
        config: dict[str, Any],
        name: str,
        *,
        clock: Path | None = None,
        stopping: ProcessGate | None = None,
    ) -> tuple[subprocess.Popen[bytes], str]:
        config_path = self.private(name + ".json", config)
        stderr = self.enterContext((self.root / (name + ".stderr")).open("wb"))
        command = [sys.executable, "-m", "remote_agent", "serve", config_path]
        if clock is not None or stopping is not None:
            command = [
                sys.executable,
                "-m",
                "tests.contracts.runtime.serviceprocess",
                config_path,
            ]
            if clock is not None:
                command += ["--clock", str(clock)]
            if stopping is not None:
                command += [
                    "--stop-port",
                    str(stopping.port),
                    "--stop-token",
                    stopping.token,
                ]
        environment = dict(
            os.environ,
            PYTHONPATH=os.pathsep.join(
                (str(CONTRACT_RUNTIME_ROOT.parent), str(REPO_ROOT))
            ),
        )
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=stderr, env=environment
        )
        self.processes.append((process, stderr))
        assert process.stdout is not None, (
            "The service must have a captured output pipe"
        )
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(12), "Service startup did not finish")
            line = process.stdout.readline()
        self.assertTrue(line, (self.root / (name + ".stderr")).read_text())
        ready = json.loads(line)
        self.assertEqual(ready["event"], "service_ready", ready)
        return process, ready["url"]

    def stop(self, process: Any) -> None:
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=6), 0)

    def cleanup(self) -> None:
        for process, stderr in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            stderr.close()

    def question(
        self, identifier: str = "q1", version: int = 0, text: str = "ping"
    ) -> dict[str, Any]:
        return {
            "id": identifier,
            "session": "s",
            "kind": "question",
            "payload": {"text": text},
            "expected_version": version,
        }

    def run_command(self, command: Any, config: Any) -> Any:
        config_path = self.private(command + "-config.json", config)
        return subprocess.run(
            [sys.executable, "-m", "remote_agent", command, config_path],
            capture_output=True,
            text=True,
            timeout=15,
            env=dict(os.environ, PYTHONPATH=str(CONTRACT_RUNTIME_ROOT.parent)),
            check=False,
        )

    def await_version(self, client: Any, version: Any) -> Any:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            view = client.view("user")["view"]
            if view["version"] == version:
                return view
        self.fail("Agent did not process the durable inbox")
