from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from remote_agent.controller import HybridAgent
from remote_agent.interfaces import ModelProvider
from remote_agent.modeljournal import ModelFailure, ModelJournal
from remote_agent.store import Store
from tests.contracts.runtime.setql2 import nested


@pytest.mark.contract_linux
class TestCallableDepth(unittest.TestCase):
    def test_each_callable_provider_operation_has_the_128_level_boundary(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            Store(Path(directory) / "agent.sqlite3") as store,
        ):
            agent = HybridAgent.__new__(HybridAgent)
            agent.model_journal = ModelJournal(store)
            agent.limits = {"max_model_calls": 20}
            agent.model_timeout = 5
            agent.jobs, agent.jobs_lock = {}, threading.Lock()
            for operation in ("respond", "repair", "wake"):
                callback = Mock()
                agent.provider = cast(
                    ModelProvider, SimpleNamespace(**{operation: callback})
                )
                for empty in (False, True):
                    with self.subTest(operation=operation, empty=empty):
                        accepted = nested(128, objects=True, empty=empty)
                        callback.return_value = accepted
                        self.assertEqual(
                            agent._call_model(
                                operation, {}, identifier=f"{operation}:{empty}:ok"
                            ),
                            accepted,
                        )
                        rejected = f"{operation}:{empty}:deep"
                        callback.return_value = nested(129, objects=True, empty=empty)
                        with self.assertRaisesRegex(ModelFailure, "128 levels"):
                            agent._call_model(operation, {}, identifier=rejected)
                        recorded = store.get("model:" + rejected)["execution"]
                        self.assertIsNone(recorded["value"])
                        self.assertFalse(recorded["uncertain"])
                        calls = callback.call_count
                        with self.assertRaises(ModelFailure):
                            agent._call_model(operation, {}, identifier=rejected)
                        self.assertEqual(
                            callback.call_count,
                            calls,
                            "replayed failure resampled provider",
                        )
                callback.return_value = {"ok": True}
                self.assertEqual(
                    agent._call_model(operation, {}, identifier=operation + ":next"),
                    {"ok": True},
                )
