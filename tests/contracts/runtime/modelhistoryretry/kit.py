from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from typing import Any, ClassVar, Never, cast

from remote_agent.controller import HybridAgent
from remote_agent.modeljournal import ModelJournal, ModelUnresolved
from remote_agent.store import Store
from remote_agent.transport import ReferenceRoom


class InterruptedHost(BaseException):
    pass


class Provider:
    identity: ClassVar[dict[str, str]] = {"profile": "history-retry-test-provider/1"}

    def __init__(
        self, index: Any, *, interrupted: bool = False, failures: int = 1
    ) -> None:
        self.index, self.interrupted, self.failures = index, interrupted, failures
        self.calls = []

    def prepare(self, operation: Any, bundle: Any) -> dict[str, Any]:
        return {"operation": operation, "bundle": bundle}

    def execute(self, request: Any, *, timeout: Any) -> dict[str, Any]:
        pages = request["bundle"].get("history_pages", [])
        index = len(pages)
        self.calls.append(index)
        if index < self.index:
            value = {
                "history_query": {"after": 0, "limit": 1, "kind": "turn", "query": ""}
            }
        elif self.failures:
            self.failures -= 1
            if self.interrupted:
                raise InterruptedHost()
            return {
                "profile": "remote-agent/callback-result/1",
                "value": None,
                "failure": "provider response outcome unknown",
                "uncertain": True,
            }
        else:
            value = {"state": {"count": 1}, "message": None, "scope": "turn"}
        return {
            "profile": "remote-agent/callback-result/1",
            "value": value,
            "failure": None,
            "uncertain": False,
        }

    def decode(self, operation: Any, execution: Any) -> Any:
        return execution["value"]

    def respond(self, bundle: dict[str, Any]) -> Never:
        raise AssertionError("History retries must use the prepared model journal")

    def wake(self, bundle: dict[str, Any]) -> Never:
        raise AssertionError("History retries must use the prepared model journal")

    def repair(self, bundle: dict[str, Any]) -> Never:
        raise AssertionError("History retries must use the prepared model journal")


class ResponseAgent(HybridAgent):
    """Run the production control/response methods without a room or callback process."""

    def __init__(self, store: Any, provider: Any, *, max_attempts: int = 3) -> None:
        self.store, self.provider = store, provider
        self.model_journal = ModelJournal(store)
        self.model_timeout = 1
        self.limits = {"max_model_calls": 10, "max_attempts": max_attempts}
        self.jobs, self.jobs_lock, self.lock = {}, threading.Lock(), threading.RLock()
        self.closed, self.stopping = False, threading.Event()
        self.control_actors, self.actor, self.now = (
            {"operator": ["retry"]},
            "Agent:a",
            lambda: 0,
        )
        self.room = cast(
            ReferenceRoom,
            SimpleNamespace(view=lambda *_: {"view": {"status": "active"}}),
        )

    def _bundle(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"state": {"count": 0}}

    def _kick_repair(self) -> None:
        pass

    def _drain(self) -> Any:
        execution = self.store.get("execution")
        try:
            result, identifier = self._respond_with_history(
                self._bundle(), execution["id"] + ":respond"
            )
        except ModelUnresolved as error:
            return self._held(error)
        self.store.commit(
            "test_response_completed",
            {"model_invocation": identifier},
            updates={"execution": None, "state": result["state"]},
        )
        return {"status": "completed", "model_invocation": identifier}


class HistoryRetryFixture(unittest.TestCase):
    def fixture(self, root: Any, provider: Any, *, max_attempts: int = 3) -> Any:
        path = root / "agent.sqlite3"
        store = Store(path)
        self.addCleanup(store.close)
        if store.get("execution") is None:
            store.commit(
                "attempt",
                {},
                updates={
                    "mode": "llm",
                    "state": {"count": 0},
                    "model_calls": 0,
                    "attempts:q": 1,
                    "staged": None,
                    "execution": {
                        "id": "turn:q:1",
                        "input": "q",
                        "attempt": 1,
                        "producer": "llm",
                    },
                },
            )
        return ResponseAgent(store, provider, max_attempts=max_attempts)

    def retry(self, agent: Any, identifier: str = "retry") -> Any:
        return agent.control(
            "operator",
            {
                "id": identifier,
                "action": "retry",
                "reason": "Reconcile the unknown model response",
            },
        )


class AnswerProvider(Provider):
    def execute(self, request: Any, *, timeout: Any) -> Any:
        result = super().execute(request, timeout=timeout)
        value = result["value"]
        if value and "state" in value:
            event = request["bundle"]["input"]
            value["message"] = {
                "kind": "answer",
                "payload": {
                    "request": event["id"],
                    "text": "Recorded recovered answer.",
                },
            }
        return result
