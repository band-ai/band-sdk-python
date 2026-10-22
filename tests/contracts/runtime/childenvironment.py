from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.program import Artifact


class ChildEnvironmentFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="child-env-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact = Artifact.publish("# env probe only\n", self.root / "artifacts")
        self.bundle = {"program": self.artifact.to_dict()}
        # clear=True prevents actual host secrets from entering even a RED child.
        self.parent = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "TZ": "UTC",
            "OPENAI_API_KEY": "synthetic-openai-key-canary",
            "OTHER_PROVIDER_TOKEN": "synthetic-other-token-canary",
            "HOME": str(self.root / "host-home"),
            "PYTHONPATH": str(self.root / "untrusted-imports"),
        }

    def probe(self, *, engine: bool = False) -> list[Any]:
        script = self.root / ("engine.py" if engine else "command.py")
        # Never echo environment values, even if a guard regresses.
        source = (
            "import json,os,sys\n"
            "result={'keys': sorted(os.environ), 'runtime_import': False}\n"
            "try:\n    import remote_agent.sdk\n    result['runtime_import']=True\n"
            "except ImportError:\n    pass\n"
        )
        if engine:
            source += (
                "result['ready']=True\nprint(json.dumps(result),flush=True)\n"
                "for line in sys.stdin: print(line,end='',flush=True)\n"
            )
        else:
            source += "json.load(sys.stdin)\nprint(json.dumps(result))\n"
        script.write_text(source)
        return [sys.executable, str(script)]

    def assert_minimal(self, result: Any, *, python: bool = False) -> None:
        expected = {"PATH", "LANG", "TZ"} | ({"PYTHONPATH"} if python else set())
        self.assertEqual(set(result["keys"]), expected)
        if python:
            self.assertTrue(
                result["runtime_import"], "Trusted runtime imports must still work"
            )
