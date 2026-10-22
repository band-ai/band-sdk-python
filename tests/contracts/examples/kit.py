"""Own deterministic engine-boundary peers for example lifecycle checks."""

from __future__ import annotations

from contextlib import ExitStack
from enum import StrEnum
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest

from remote_agent.types import JsonObject
from tests.loaders import load_script_module
from tests.paths import CONTRACTS_ROOT

HYBRID_DEMO = CONTRACTS_ROOT / "agent-runtime/examples/hybrid_demo.py"
HYBRID_CONTRACT = CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"


class EngineRole(StrEnum):
    AUTHORITY = "authority"
    CHECKER = "checker"


class StartupFailure(Exception):
    pass


class InitializationFailure(Exception):
    pass


class CleanupFailure(Exception):
    pass


class EnginePeer:
    def __init__(self, owner: EngineFactory, role: EngineRole, pid: int) -> None:
        self.owner, self.role = owner, role
        self.process = SimpleNamespace(pid=pid)
        self.lease = owner.resources.enter_context((owner.directory / role).open("xb"))
        self.ready = Event()
        self.ready.set()

    def request(self, **request: Any) -> JsonObject:
        assert request["op"] == "new", "The demo advanced past the injected boundary"
        assert self.role is EngineRole.AUTHORITY
        assert all(peer.ready.is_set() for peer in self.owner.acquired.values())
        self.owner.initialization_requests.append(request)
        raise self.owner.initialization_failure

    def close(self) -> None:
        self.owner.close_attempts.append(self.role)
        self.lease.close()
        if self.owner.failing_cleanup is self.role:
            raise self.owner.cleanup_failure


class EngineFactory:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir()
        self.resources = ExitStack()
        self.acquired: dict[EngineRole, EnginePeer] = {}
        self.start_attempts: list[EngineRole] = []
        self.close_attempts: list[EngineRole] = []
        self.initialization_requests: list[JsonObject] = []
        self.fail_checker_startup = False
        self.failing_cleanup: EngineRole | None = None
        self.startup_failure = StartupFailure("The checker refused startup")
        self.initialization_failure = InitializationFailure(
            "The authority refused room initialization"
        )
        self.cleanup_failure = CleanupFailure("An owned engine failed during cleanup")

    def start(self, contract: Path) -> EnginePeer:
        assert contract == HYBRID_CONTRACT
        role = (EngineRole.AUTHORITY, EngineRole.CHECKER)[len(self.start_attempts)]
        self.start_attempts.append(role)
        if role is EngineRole.CHECKER:
            assert self.acquired[EngineRole.AUTHORITY].ready.is_set()
            if self.fail_checker_startup:
                raise self.startup_failure
        peer = EnginePeer(self, role, len(self.start_attempts))
        self.acquired[role] = peer
        return peer

    @property
    def all_released(self) -> bool:
        return all(peer.lease.closed for peer in self.acquired.values())

    def dispose(self) -> None:
        self.resources.close()


def run_demo(
    engines: EngineFactory, monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    demo = load_script_module(HYBRID_DEMO, "contractshybridlifecycle")
    monkeypatch.setattr(demo, "EngineProcess", engines.start)
    monkeypatch.setattr(
        demo.sys, "argv", [str(HYBRID_DEMO), "--workspace", str(workspace)]
    )
    demo.main()
