from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


class BandDeploymentFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="band-deployment-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def private(self, name: Any, value: Any) -> str:
        path = self.root / name
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        path.chmod(0o600)
        return str(path)

    def model(self, source: Any) -> Any:
        script = self.private("provider.py", source)
        return self.model_command([sys.executable, script])

    def model_command(self, command: list[str]) -> str:
        secret = self.private("model.key", "model-test-key-not-a-real-credential")
        return self.private(
            "model.json",
            {
                "profile": "band-contract/structured-model/1",
                "command": command,
                "credentials": {"MODEL_API_KEY": secret},
            },
        )

    def host_config(self) -> dict[str, Any]:
        return {
            "profile": "band-contract/program-gateway/1",
            "state": str(self.root / "gateway"),
            "platform_url": "https://band.example",
            "author_ids": ["00000000-0000-0000-0000-000000000001"],
            "agent_keys": {
                "Agent:00000000-0000-0000-0000-000000000002": self.private(
                    "agent.key", "a" * 32
                )
            },
            "engine_command": [sys.executable, "-c", 'raise SystemExit("test only")'],
            "model_config": self.model(
                'raise RuntimeError("must not invoke during configuration")'
            ),
            "token_file": self.private("platform.key", "p" * 32),
            "port": 4530,
            "auto_qualification": True,
        }
