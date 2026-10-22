from __future__ import annotations

import json
import os
import socket
import subprocess
import sysconfig
import venv
from unittest.mock import patch

import pytest

from remote_agent.program import Artifact
from tests.contracts.runtime.sandbox import PREAMBLE, SandboxFixture
from tests.paths import CONTRACT_RUNTIME_ROOT


@pytest.mark.contract_isolation
class TestSandbox(SandboxFixture):
    def test_provider_environment_is_not_inherited_by_generated_programs(self) -> None:
        with patch.dict(
            os.environ, {"REMOTE_AGENT_TEST_SECRET": "synthetic-canary-only"}
        ):
            result = self.run_probe(
                "import os\nreturn Handled({'secret': os.environ.get('REMOTE_AGENT_TEST_SECRET')})"
            )
        self.assertIsNone(result["secret"])

    def test_host_files_and_journal_directory_are_absent_from_program_namespace(
        self,
    ) -> None:
        canary = self.directory / "journal-private-canary"
        canary.write_text("synthetic private data")
        result = self.run_probe(
            "from pathlib import Path\nreturn Handled({'visible': Path("
            + repr(str(canary))
            + ").exists()})"
        )
        self.assertFalse(result["visible"])

    def test_program_cannot_rewrite_its_artifact_or_create_host_files(self) -> None:
        marker = self.directory / "unexpected-write"
        result = self.run_probe(
            "from pathlib import Path\ntry:\n    Path("
            + repr(str(marker))
            + ").write_text('changed')\n    blocked = False\nexcept OSError:\n    blocked = True\nreturn Handled({'blocked': blocked})"
        )
        self.assertTrue(result["blocked"])
        self.assertFalse(marker.exists())

    def test_program_cannot_connect_to_a_host_loopback_listener(self) -> None:
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        body = (
            "import socket\ntry:\n    connection = socket.create_connection(('127.0.0.1', "
            + str(port)
            + "), timeout=0.2)\n    connection.close()\n    blocked = False\nexcept OSError:\n    blocked = True\nreturn Handled({'blocked': blocked})"
        )
        self.assertTrue(self.run_probe(body)["blocked"])

    def test_program_cannot_spawn_an_executable(self) -> None:
        body = "import subprocess\ntry:\n    subprocess.run(['/usr/bin/true'], check=True)\n    blocked = False\nexcept OSError:\n    blocked = True\nreturn Handled({'blocked': blocked})"
        self.assertTrue(self.run_probe(body)["blocked"])

    def test_program_has_a_real_memory_limit(self) -> None:
        body = "try:\n    allocation = bytearray(300 * 1024 * 1024)\n    blocked = False\nexcept MemoryError:\n    blocked = True\nreturn Handled({'blocked': blocked})"
        self.assertTrue(self.run_probe(body)["blocked"])

    def test_host_processes_are_not_visible_in_the_program_pid_namespace(self) -> None:
        body = (
            "from pathlib import Path\nreturn Handled({'visible': Path('/proc/"
            + str(os.getpid())
            + "/status').exists()})"
        )
        self.assertFalse(self.run_probe(body)["visible"])

    def test_program_uses_normal_stdlib_without_ambient_pythonpath(self) -> None:
        body = "import hashlib,json,math\nreturn Handled({'hash': hashlib.sha256(b'known').hexdigest(), 'sum': math.fsum([1,2]) == 3})"
        result = self.run_probe(body)
        self.assertEqual(len(result["hash"]), 64)
        self.assertTrue(result["sum"])

    def test_copied_virtual_environment_uses_the_system_python_inside_the_sandbox(
        self,
    ) -> None:
        environment = self.directory / "venv"
        venv.create(environment, symlinks=False)
        artifact = Artifact.publish(PREAMBLE, self.directory / "artifacts")
        # The host needs SDK dependencies; the generated callback still starts
        # its isolated system interpreter without this site-packages directory.
        bootstrap = (
            "import json,sys;sys.path[:0]=[sys.argv[1],sys.argv[2]];"
            "from remote_agent.program import Artifact,Program;"
            "print(json.dumps(Program(Artifact(**json.loads(sys.argv[3]))).describe({})))"
        )
        result = subprocess.run(
            [
                str(environment / "bin/python"),
                "-I",
                "-c",
                bootstrap,
                str(CONTRACT_RUNTIME_ROOT.parent),
                sysconfig.get_path("purelib"),
                json.dumps(artifact.to_dict()),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["initial"], {})
