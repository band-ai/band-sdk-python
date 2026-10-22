from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from remote_agent.controller import HybridAgent
from remote_agent.interfaces import ModelProvider
from remote_agent.invocations import InvocationJournal
from remote_agent.network import HTTPRoom
from remote_agent.program import Program
from remote_agent.store import Store
from remote_agent.transport import LocalGate, ReferenceRoom
from tests.contracts.runtime.timeoutprecision import TimeoutPrecisionFixture


class TestTimeoutPrecision(TimeoutPrecisionFixture):
    def test_http_worker_receives_exact_integer_milliseconds(self) -> None:
        client = HTTPRoom.__new__(HTTPRoom)
        client.url, client.token, client.ca_file = "http://127.0.0.1", "a" * 32, None
        client.authentication = "bearer"
        for milliseconds in (1005, 1001, 2002, 7, 30000):
            with self.subTest(milliseconds=milliseconds):
                client.timeout = milliseconds / 1000
                with patch(
                    "remote_agent.network.call",
                    return_value={"status": 200, "body": {}},
                ) as call:
                    client._request("GET", "/v1/identity")
                self.assertEqual(call.call_args.args[1]["timeout_ms"], milliseconds)

    @pytest.mark.contract_linux
    def test_program_invocation_records_the_exact_integer_milliseconds(self) -> None:
        for milliseconds in (1005, 1001, 2002, 7, 30000):
            with (
                self.subTest(milliseconds=milliseconds),
                Store(self.root / (str(milliseconds) + ".sqlite3")) as store,
            ):
                program = Program(self.artifact, timeout=milliseconds / 1000)
                with patch.object(
                    program,
                    "execute",
                    return_value={"exit_code": 0, "failure": None, "complete": True},
                ):
                    InvocationJournal(store).run(program, {"op": "describe"})
                recorded = [
                    r["data"] for r in store.records() if r["kind"] == "program_input"
                ]
                self.assertEqual(recorded[0]["timeout_ms"], milliseconds)

    @pytest.mark.contract_linux
    def test_controller_configuration_and_model_input_keep_exact_milliseconds(
        self,
    ) -> None:
        metadata = {"schema": "1", "initial": {}, "coverage": {"mode": "partial"}}
        identity = "timeout-test-contract"
        room = SimpleNamespace(
            identity=identity,
            session="s",
            idempotent=True,
            view=lambda *args: {"view": {}},
        )
        gate = SimpleNamespace(engine=SimpleNamespace(identity=identity))
        provider = SimpleNamespace(respond=lambda bundle: {"answer": "offline"})
        for milliseconds in (1005, 1001, 2002, 30000):
            with (
                self.subTest(milliseconds=milliseconds),
                Store(
                    self.root / ("controller-" + str(milliseconds) + ".sqlite3")
                ) as store,
            ):
                with patch.object(Program, "describe", return_value=metadata):
                    agent = HybridAgent(
                        store,
                        Program(self.artifact, timeout=milliseconds / 1000),
                        cast(ReferenceRoom, room),
                        cast(LocalGate, gate),
                        cast(ModelProvider, provider),
                        actor="agent",
                        artifact_directory=self.root / "artifacts",
                        model_timeout=milliseconds / 1000,
                    )
                try:
                    config = store.get("config")
                    for field in ("program_timeout_ms", "model_timeout_ms"):
                        with self.subTest(field=field):
                            self.assertEqual(config[field], milliseconds)
                    self.assertEqual(
                        agent._call_model("respond", {}, identifier="model-precision"),
                        {"answer": "offline"},
                    )
                    recorded = [
                        r["data"] for r in store.records() if r["kind"] == "model_input"
                    ]
                    self.assertEqual(recorded[0]["timeout_ms"], milliseconds)
                finally:
                    agent.close()
