from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.contracts.runtime.modelhistoryretry.kit import (
    HistoryRetryFixture,
    InterruptedHost,
    Provider,
)


@pytest.mark.contract_linux
class TestHistoryRetry(HistoryRetryFixture):
    def test_retry_after_restart_resamples_only_the_unknown_history_followup(
        self,
    ) -> None:
        for index in (1, 2):
            for interrupted in (False, True):
                with (
                    self.subTest(index=index, interrupted=interrupted),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    provider = Provider(index, interrupted=interrupted)
                    agent = self.fixture(root, provider)
                    if interrupted:
                        with self.assertRaises(InterruptedHost):
                            agent._drain()
                    else:
                        self.assertEqual(agent._drain()["status"], "held")
                    original_calls = list(provider.calls)
                    prefix = {
                        key: agent.store.get(key)
                        for key in (
                            "model:turn:q:1:respond",
                            "context:turn:q:1:respond:0",
                        )
                    }
                    if index == 2:
                        prefix.update(
                            {
                                key: agent.store.get(key)
                                for key in (
                                    "model:turn:q:1:respond:history:1",
                                    "context:turn:q:1:respond:1",
                                )
                            }
                        )
                    agent.store.close()
                    agent = self.fixture(root, provider)
                    self.assertEqual(agent._drain()["status"], "held")
                    self.assertEqual(
                        provider.calls,
                        original_calls,
                        "Recovery cannot repeat an uncertain model call",
                    )
                    result = self.retry(agent)
                    self.assertEqual(result["status"], "completed")
                    self.assertEqual(provider.calls, original_calls + [index])
                    self.assertEqual(agent.store.get("model_calls"), index + 2)
                    self.assertEqual(agent.store.get("state"), {"count": 1})
                    for key, value in prefix.items():
                        self.assertEqual(
                            agent.store.get(key),
                            value,
                            "Completed history evidence remains pinned",
                        )
                    self.assertEqual(
                        self.retry(agent),
                        result,
                        "The control itself remains idempotent",
                    )
                    self.assertEqual(provider.calls, original_calls + [index])

    def test_history_response_retries_spend_the_existing_attempt_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = Provider(1, failures=10)
            agent = self.fixture(Path(directory), provider, max_attempts=2)
            self.assertEqual(agent._drain()["status"], "held")
            self.assertEqual(self.retry(agent, "retry-once")["status"], "held")
            self.assertEqual(provider.calls, [0, 1, 1])
            result = self.retry(agent, "retry-exhausted")
            self.assertEqual(result["status"], "held")
            self.assertIn("attempt budget", result["reason"])
            self.assertEqual(provider.calls, [0, 1, 1])
            self.assertEqual(agent.store.get("model_calls"), 3)
            self.assertEqual(agent.store.get("state"), {"count": 0})

    def test_unknown_initial_response_retains_the_existing_execution_abandonment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = Provider(0)
            agent = self.fixture(Path(directory), provider)
            self.assertEqual(agent._drain()["status"], "held")
            agent._retry_execution(
                {"id": "retry-initial", "reason": "Reconcile initial response"}
            )
            self.assertIsNone(agent.store.get("execution"))
            self.assertEqual(provider.calls, [0])
