from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import tests.contracts.runtime.bandqualityauthoring as quality_authoring
from remote_agent.authority import DurableRoom
from remote_agent.band.arithmetic import RuntimeDriver
from remote_agent.band.generic import GenericRuntimeDriver
from remote_agent.band.serve_programs import assessor, create_runtime
from remote_agent.network import RoomAPI
from remote_agent.program import Program
from remote_agent.store import Store
from remote_agent.transport import EngineProcess, TransportError
from tests.contracts.runtime.bandrolequality import SOURCE, policy
from tests.contracts.runtime.required import require_value
from tests.paths import CONTRACT_RUNTIME_TESTS

CONTRACT = CONTRACT_RUNTIME_TESTS / "fixtures/band/label-normalization.band.txt"


INITIAL = {"processed": 0, "mode": "ascii"}


DONE = {"status": "completed", "producer": "supervisor", "receipt": None}


class HistoryProvider:
    identity: ClassVar[dict[str, str]] = {
        "profile": "local-uncertain-history-control-test/1"
    }

    def __init__(self) -> None:
        self.calls = []

    def prepare(self, operation: Any, bundle: Any) -> dict[str, Any]:
        return {"operation": operation, "bundle": bundle}

    def execute(self, request: Any, *, timeout: Any) -> dict[str, Any]:
        if request["operation"] != "respond":
            raise AssertionError("Terminal retry needs no wake or repair")
        index = len(request["bundle"].get("history_pages", []))
        self.calls.append(index)
        if index == 0:
            return {
                "profile": "remote-agent/callback-result/1",
                "failure": None,
                "uncertain": False,
                "value": {
                    "history_query": {
                        "after": 0,
                        "limit": 1,
                        "kind": "turn",
                        "query": "",
                    }
                },
            }
        return {
            "profile": "remote-agent/callback-result/1",
            "value": None,
            "failure": "Stopped local history followup has no trustworthy result",
            "uncertain": True,
        }

    def decode(self, operation: Any, execution: Any) -> Any:
        return execution["value"]


class RunningHistoryProvider:
    def __init__(self) -> None:
        self.calls = []
        self.started, self.release = threading.Event(), threading.Event()

    def respond(self, bundle: Any) -> dict[str, Any]:
        index = len(bundle.get("history_pages", []))
        self.calls.append(index)
        if index == 0:
            return {
                "history_query": {"after": 0, "limit": 1, "kind": "turn", "query": ""}
            }
        self.started.set()
        if not self.release.wait(5):
            raise AssertionError("Test must release the owned callback")
        return {"state": INITIAL, "message": None, "scope": "turn"}


class NoListener:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def close(self) -> None:
        pass


class GenericTerminalRetryFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.command = quality_authoring.QualityAuthoringFixture.reference_engine()
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="generic-terminal-qualification-"
        )
        cls.addClassCleanup(cls.temporary.cleanup)
        text = CONTRACT.read_text()
        checksum = hashlib.sha256(text.encode()).hexdigest()
        cls.context = {
            "source": {
                "profile": "band-contract/file-source/1",
                "format": "frontmatter",
                "sha256": checksum,
                "content": text,
            },
            "config": {"bindings": {"requester": "user", "normalizer": "agent"}},
            "history": [],
        }
        quality = policy()
        quality["source_sha256"] = checksum
        candidate = {
            "classification": "program",
            "reason": "Bounded ASCII normalization",
            "handles": ["ASCII normalization"],
            "delegates": [],
            "evidence": [],
            "source": SOURCE,
        }
        # Only candidate generation is local; actual independent qualification runs.
        with patch("remote_agent.band.serve_programs.invoke", return_value=candidate):
            assessment = assessor(
                Path(cls.temporary.name) / "assessment", quality_policies=[quality]
            )(cls.context, {"id": "normalizer", "role": "normalizer"})
        if assessment["runtime_profile"] != "contract-role/2":
            raise AssertionError("The supported generic role must actually qualify")
        cls.assessment = dict(assessment, role="normalizer")

    def fixture(self, *, mode: str = "llm", provider: Any = None) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="generic-terminal-retry-")
        self.addCleanup(temporary.cleanup)
        self.root, self.clock = Path(temporary.name), [0]
        engine = EngineProcess(CONTRACT, command=self.command)
        self.addCleanup(engine.close)
        self.room_store = Store(self.root / "room.sqlite3")
        self.addCleanup(self.room_store.close)
        self.room = DurableRoom(
            self.room_store,
            engine,
            session="s",
            bindings={"requester": "user", "normalizer": "agent"},
            clock=lambda: self.clock[0],
        )
        api = RoomAPI(self.room)
        grant = {"actor": "agent", "permissions": ["room", "observe"]}

        def local_request(
            transport: Any, method: Any, path: Any, data: Any = None
        ) -> Any:
            status, result = api.dispatch(method, path, grant, data)
            self.assertEqual(status, 200)
            return result

        self.provider = provider or HistoryProvider()
        key = self.root / "agent.key"
        key.write_text("k" * 32)
        key.chmod(0o600)
        value = {
            "room": "room",
            "session": "s",
            "actor": "User:operator",
            "context": self.context,
            "request": {
                "mode": mode,
                "journal": True,
                "target_bytes": 1,
                "minimum_steps": 1,
            },
        }
        host = {
            "agent_keys": {"agent": str(key)},
            "engine_command": self.command,
            "model_config": str(self.root / "unused-model.json"),
            "platform_url": "https://fixture.invalid",
        }
        stack = self.enterContext(ExitStack())
        stack.enter_context(
            patch("remote_agent.network.HTTPRoom._request", new=local_request)
        )
        stack.enter_context(
            patch("remote_agent.service.CommandProvider", return_value=self.provider)
        )
        stack.enter_context(patch("remote_agent.service.HTTPService", NoListener))
        stack.enter_context(
            patch("remote_agent.band.generic.model_arguments", return_value=[])
        )
        stack.enter_context(
            patch.object(GenericRuntimeDriver, "run", lambda driver: None)
        )
        self.driver = create_runtime(
            value, self.assessment, self.root / "runtime", host_config=host
        )
        self.addCleanup(self.driver.close)
        self.assertIsInstance(self.driver, GenericRuntimeDriver)
        self.assertIs(self.driver.control.__func__, RuntimeDriver.control)
        self.agent = require_value(
            self.driver.service.agent, "The generic service must have an agent"
        )
        self.store = self.driver.service.store
        self.assertEqual(
            self.agent.limits["max_attempts"],
            3,
            "Use the actual default attempt ceiling",
        )
        self.assertEqual(self.agent.limits["max_model_calls"], 12)
        self.event = self.room.publish(
            {
                "id": "normalize-1",
                "session": "s",
                "kind": "normalize",
                "payload": {"text": "Band"},
                "expected_version": 0,
            },
            "user",
        )
        self.assertEqual(self.event["status"], "accepted")

    def retry(
        self,
        identifier: Any,
        *,
        actor: str = "User:operator",
        reason: str = "Recheck trusted terminal authority",
    ) -> Any:
        return self.driver.control(
            actor, {"id": identifier, "action": "retry", "reason": reason}
        )

    def exhaust(self) -> None:
        self.driver.service.cycle()
        self.assertTrue(self.driver.service.held)
        for identifier in ("retry-second", "retry-third"):
            self.assertEqual(self.retry(identifier)["status"], "held")
        self.assertEqual(self.provider.calls, [0, 1, 1, 1])
        self.assertEqual(self.store.get("attempts:" + self.event["id"]), 3)
        self.assertEqual(self.store.get("execution")["history_retries"], {"1": 2})
        self.assertEqual(self.store.get("model_calls"), 4)
        self.assertIsNone(self.store.get("staged"))

    def expire(self, now: int = 120000) -> Any:
        self.clock[0] = now
        view = self.room.view("agent")["view"]
        self.assertEqual((view["status"], view["legal"]), ("terminal", []))
        return view

    def pending(self) -> dict[str, Any]:
        return {
            key: self.store.get(key)
            for key in (
                "execution",
                "queue",
                "state",
                "staged",
                "attempts:" + self.event["id"],
                "model_calls",
                "turns",
            )
        }

    def pins(self) -> dict[str, Any]:
        # Captures, completed query pages, and input-wide page accounting must survive cleanup.
        keys = [
            row["data"]["id"]
            for row in self.store.records()
            if row["kind"] == "model_input"
        ]
        root = self.store.get("execution")["id"] + ":respond"
        return {
            key: self.store.get(key)
            for key in [
                *("model:" + key for key in keys),
                "context:" + root + ":0",
                self.agent._input_history_key(self.event["id"]),
            ]
        }

    def assert_staged_reconciliation(self, *, accepted: Any) -> None:
        self.fixture(mode="program")
        publish = self.driver.service.room.publish

        def lose_ack(*args: Any) -> None:
            if accepted:
                publish(*args)
            raise TransportError("Publication outcome unknown")

        with patch.object(self.driver.service.room, "publish", side_effect=lose_ack):
            self.driver.service.cycle()
        staged = self.store.get("staged")
        self.assertIsNotNone(staged)
        self.assertTrue(self.driver.service.held)
        terminal = self.expire(1800000 if accepted else 120000)
        execute = Program.execute
        with (
            patch.object(
                Program, "execute", autospec=True, side_effect=execute
            ) as callbacks,
            patch.object(self.driver.service.room, "publish", wraps=publish) as sends,
        ):
            if accepted:
                result = self.retry("staged-accepted")
                self.assertEqual(
                    (result["status"], result["producer"]), ("completed", "program")
                )
                self.assertEqual(result["receipt"]["id"], staged["envelope"]["id"])
                self.assertEqual(
                    self.store.get("state"), {"processed": 1, "mode": "ascii"}
                )
                self.assertEqual(self.store.get("queue"), [])
                self.assertIsNone(self.store.get("staged"))
            else:
                with patch.object(
                    self.driver.service.room,
                    "lookup",
                    side_effect=TransportError("Still unknown"),
                ):
                    result = self.retry("staged-unknown")
                self.assertEqual(result["status"], "held")
                self.assertEqual(
                    self.store.get("staged")["envelope"], staged["envelope"]
                )
                self.assertEqual(self.store.get("queue"), [self.event["id"]])
                self.assertEqual(self.store.get("state"), INITIAL)
                self.assertEqual(self.store.get("turns"), 0)
            self.assertEqual(self.provider.calls, [])
            self.assertEqual(self.store.get("model_calls"), 0)
            self.assertEqual(
                self.room.view("agent")["view"]["facts"], terminal["facts"]
            )
            callbacks.assert_not_called()
            if accepted:
                # HTTPRoom inherits ReferenceRoom.lookup's exact-envelope dedup retry.
                sends.assert_called_once()
                self.assertEqual(sends.call_args.args[0], staged["envelope"])
                self.assertEqual(
                    self.room.view("agent")["view"]["version"], terminal["version"]
                )
            else:
                sends.assert_not_called()
        self.store._verify()
