from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

from remote_agent.commands import result_from_capture
from remote_agent.openaiprovider import OpenAIProvider
from remote_agent.program import Program
from remote_agent.providers import CommandProvider, CommandValidator
from remote_agent.transport import EngineProcess
from tests.contracts.runtime.childenvironment import ChildEnvironmentFixture
from tests.paths import CONTRACT_RUNTIME_ROOT


@pytest.mark.contract_linux
class TestChildEnvironment(ChildEnvironmentFixture):
    def test_engine_does_not_inherit_provider_credentials_or_ambient_imports(
        self,
    ) -> None:
        with patch.dict(os.environ, self.parent, clear=True):
            engine = EngineProcess(
                self.root / "unused.band", command=self.probe(engine=True)
            )
            try:
                self.assert_minimal(engine.identity)
                self.assertEqual(engine.request(op="probe"), {"op": "probe"})
            finally:
                engine.close()

    def test_command_provider_does_not_inherit_credentials_and_keeps_trusted_imports(
        self,
    ) -> None:
        provider = CommandProvider(self.probe(), self.root / "artifacts")
        with patch.dict(os.environ, self.parent, clear=True):
            self.assert_minimal(provider.respond(self.bundle), python=True)

    def test_validator_does_not_inherit_credentials_and_keeps_trusted_imports(
        self,
    ) -> None:
        validator = CommandValidator(self.probe())
        with patch.dict(os.environ, self.parent, clear=True):
            self.assert_minimal(
                validator(Program(self.artifact), self.bundle), python=True
            )

    def test_native_worker_gets_only_its_key_and_uses_offline_https_stub(self) -> None:
        provider = OpenAIProvider(self.root / "artifacts", model="test-model")
        # Use the actual openai_worker.main. Replace HTTPSConnection before import
        # so even RED can never dispatch a real API request.
        bootstrap = """
import http.client, os, sys
assert 'OTHER_PROVIDER_TOKEN' not in os.environ
assert 'HOME' not in os.environ
class Response:
    status = 401
    def getheader(self, name): return '2' if name == 'content-length' else None
    def read(self, size): return b'{}'
class Connection:
    def __init__(self, *args, **kwargs): pass
    def request(self, method, path, body, headers):
        assert headers['Authorization'] == 'Bearer synthetic-openai-key-canary'
    def getresponse(self): return Response()
    def close(self): pass
http.client.HTTPSConnection = Connection
sys.path.insert(0, sys.argv[1])
from remote_agent.openai_worker import main
main()
"""
        provider.argv = [
            sys.executable,
            "-I",
            "-B",
            "-c",
            bootstrap,
            str(CONTRACT_RUNTIME_ROOT.parent),
        ]
        request = provider.prepare("respond", self.bundle)
        with patch.dict(os.environ, self.parent, clear=True):
            result = result_from_capture(provider.execute(request, timeout=2))
        self.assertEqual(result["status"], 401)
        self.assertEqual(result["failure"], "OpenAI HTTP 401")
        self.assertTrue(result["complete"])
