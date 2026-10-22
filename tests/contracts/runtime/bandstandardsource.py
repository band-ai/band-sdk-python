from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from remote_agent.band.generic import GenericRuntimeDriver
from remote_agent.storage import digest
from remote_agent.transport import EngineProcess, ReferenceRoom
from tests.contracts.runtime.bandrolequality import SOURCE, policy
from tests.contracts.runtime.filesource import (
    engine_command,
    normalization_source,
    package,
)


class GenericStandardSourceFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "runtime"
        self.source = normalization_source()
        self.bindings = {
            "requester": "Agent:requester",
            "normalizer": "Agent:normalizer",
        }
        key = self.root / "room.key"
        key.write_text("k" * 32)
        key.chmod(0o600)
        model = self.root / "model.json"
        model.write_text(
            json.dumps(
                {
                    "profile": "band-contract/structured-model/1",
                    "command": [
                        sys.executable,
                        "-c",
                        'raise AssertionError("Unexpected model call")',
                    ],
                    "credentials": {},
                }
            )
        )
        model.chmod(0o600)
        self.host = {
            "agent_keys": {"Agent:normalizer": str(key)},
            "engine_command": engine_command(),
            "model_config": str(model),
            "platform_url": "https://band.example",
        }

    def authority(self) -> None:
        if self.source["format"] == "standard":
            path = package(self.root, self.source)
        else:
            path = self.root / "room.band"
            path.write_text(self.source["content"])
        engine = self.enterContext(
            closing(
                EngineProcess(
                    path, format=self.source["format"], command=engine_command()
                )
            )
        )
        room = ReferenceRoom(engine, session="standard-session", bindings=self.bindings)
        self.room = SimpleNamespace(
            actor="Agent:normalizer",
            session=room.session,
            identity=room.identity,
            idempotent=True,
            view=lambda actor, now=None: room.view(actor, 0),
        )

    def start(self) -> Any:
        quality = policy()
        quality["source_sha256"] = self.source["sha256"]
        value = {
            "room": "room",
            "session": self.room.session,
            "actor": "User:owner",
            "request": {
                "journal": True,
                "mode": "program",
                "target_bytes": 100_000_000,
                "minimum_steps": 100,
            },
            "context": {"source": self.source, "config": {"bindings": self.bindings}},
        }
        assessment = {
            "role": "normalizer",
            "quality": quality,
            "quality_sha256": digest(quality),
            "source": SOURCE,
            "program_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
            "allowed_modes": ["program", "llm"],
        }
        # Network is scripted; engine, semantic callback checks, journal and
        # HybridAgent initialization are the actual production implementations.
        with (
            patch("remote_agent.service.HTTPRoom", return_value=self.room),
            patch(
                "remote_agent.service.HTTPService",
                return_value=SimpleNamespace(close=lambda: None),
            ),
            patch.object(GenericRuntimeDriver, "run", return_value=None),
        ):
            return GenericRuntimeDriver(
                value, assessment, self.workspace, host_config=self.host
            )
