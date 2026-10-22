from __future__ import annotations

import os
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import cast

import pytest

from remote_agent.transport import EngineProcess, TransportError
from tests.contracts.runtime.transportdeadline import EngineOptions, write_deadline
from tests.paths import CONTRACTS_ROOT


class TestTransport(unittest.TestCase):
    @pytest.mark.contract_linux
    def test_installed_engine_command_receives_literal_path_and_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "engine.py"
            shim.write_text(
                'import json, sys\nprint(json.dumps({"ready": True, "argv": sys.argv[1:]}), flush=True)\n'
                'for line in sys.stdin: print(line, end="", flush=True)\n'
            )
            contract = root / 'contract $(touch forbidden) "quoted".md'
            engine = EngineProcess(
                contract, command=[sys.executable, str(shim)], format="standard"
            )
            try:
                self.assertEqual(engine.identity["argv"], [str(contract), "standard"])
                self.assertEqual(
                    engine.request(op="literal", text="hi"),
                    {"op": "literal", "text": "hi"},
                )
            finally:
                engine.close()

    @pytest.mark.contract_linux
    def test_engine_command_rejects_shell_text_and_unknown_format(self) -> None:
        cases: list[EngineOptions] = [
            {"command": cast(list[str], "echo ready")},
            {"command": []},
            {"format": "other"},
        ]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(TransportError):
                EngineProcess("unused.band", **options)

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine
    def test_stopped_engine_cannot_block_forever_while_writing_a_large_checkpoint(
        self,
    ) -> None:
        contract = CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"
        engine = EngineProcess(contract)
        self.addCleanup(engine.close)
        engine.timeout = 10
        result = []

        def request() -> None:
            try:
                result.append(
                    engine.request(
                        op="definition", role="assistant", unused="x" * 262_144
                    )
                )
            except TransportError as error:
                result.append(error)

        os.kill(engine.process.pid, signal.SIGSTOP)
        _, stopped = os.waitpid(engine.process.pid, os.WUNTRACED)
        self.assertTrue(os.WIFSTOPPED(stopped), "The owned engine did not stop")
        thread = threading.Thread(target=request, daemon=True)
        try:
            with write_deadline() as deadline:
                thread.start()
                thread.join(5)
                self.assertFalse(
                    thread.is_alive(), "The expired transport did not finish"
                )
                self.assertTrue(
                    deadline.blocked, "The request did not reach its blocked write"
                )
            self.assertIsInstance(result[0], TransportError)
            self.assertIn("timeout", str(result[0]))
            self.assertTrue(engine.closed)
        finally:
            if engine.process.poll() is None:
                engine.process.kill()
                engine.process.wait(timeout=5)
            if thread.ident is not None:
                thread.join(5)
            self.assertFalse(
                thread.is_alive(), "The owned transport thread did not stop"
            )
            engine.close()
