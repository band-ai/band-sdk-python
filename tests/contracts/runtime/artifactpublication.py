from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import unittest
from typing import Any
from unittest.mock import patch

from remote_agent.band.generic import GenericRuntimeDriver
from remote_agent.storage import digest
from tests.contracts.runtime.bandrolequality import SOURCE, policy
from tests.paths import CONTRACT_RUNTIME_ROOT, REPO_ROOT


class ReachedService(Exception):
    pass


def start_generic(root: Any) -> str:
    """Exercise real role qualification and stop before starting a service."""
    key = root / "agent.key"
    key.write_text("k" * 32)
    key.chmod(0o600)
    quality = policy()
    value = {
        "room": "room",
        "session": "session",
        "actor": "User:owner",
        "request": {"mode": "program", "journal": True},
        "context": {
            "source": {"sha256": "b" * 64, "content": "pinned contract"},
            "config": {"bindings": {"normalizer": "Agent:n"}},
        },
    }
    assessment = {
        "role": "normalizer",
        "source": SOURCE,
        "quality": quality,
        "quality_sha256": digest(quality),
        "allowed_modes": ["program"],
        "program_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
    }
    host = {
        "agent_keys": {"Agent:n": str(key)},
        "engine_command": [sys.executable],
        "model_config": str(root / "unused-model.json"),
        "platform_url": "https://band.example",
    }
    with (
        patch("remote_agent.band.generic.model_arguments", return_value=[]),
        patch("remote_agent.band.generic.load_config", side_effect=lambda value: value),
        patch("remote_agent.band.generic.Service", side_effect=ReachedService),
    ):
        try:
            GenericRuntimeDriver(value, assessment, root / "runtime", host_config=host)
        except ReachedService:
            return "qualified"
    raise AssertionError(
        "Expected to reach the service boundary after real qualification"
    )


class ArtifactPublicationFixture(unittest.TestCase):
    def child(self, script: Any, *arguments: Any) -> Any:
        # unittest discovery adds fixtures to this process's path, not a new Python process.
        runtime = CONTRACT_RUNTIME_ROOT.parent
        environment = dict(
            os.environ, PYTHONPATH=os.pathsep.join((str(runtime), str(REPO_ROOT)))
        )
        return subprocess.run(
            [sys.executable, "-B", "-W", "error", "-c", script, *map(str, arguments)],
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
            check=False,
        )
