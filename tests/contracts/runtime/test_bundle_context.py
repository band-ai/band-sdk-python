from __future__ import annotations

from unittest.mock import patch

import pytest

from remote_agent.controller import AgentError
from remote_agent.storage import canonical
from remote_agent.store import Store
from tests.contracts.runtime.bundlecontext import BundleContextFixture
from tests.contracts.runtime.required import require_value


class TestBundleContext(BundleContextFixture):
    @pytest.mark.contract_linux
    def test_durable_and_ephemeral_contexts_decode_only_the_last_30_records(
        self,
    ) -> None:
        for durable in (True, False):
            with self.subTest(durable=durable):
                agent = self.fixture(durable=durable)
                event = {
                    "id": "current",
                    "kind": "counter",
                    "payload": {"text": "current"},
                }
                bundle = self.assert_bounded_bundle(
                    agent, event=event, view={"status": "waiting"}
                )
                self.assertEqual(len(bundle["journal_tail"]), 30)
                for record in bundle["journal_tail"]:
                    if record["kind"] in ("model_input", "model_output"):
                        self.assertNotIn("request", record["data"])
                        self.assertNotIn("result", record["data"])
                    elif record["kind"] in ("input", "turn"):
                        self.assertTrue(record["data"]["omitted"])
                        self.assertEqual(len(record["data"]["sha256"]), 64)
        self.assertFalse((self.root / "absent").exists())

    def test_context_cap_counts_each_evicted_tail_record(self) -> None:
        agent = self.fixture(durable=False, steps=0)
        for n in range(42):
            agent.store.commit("note", {"n": n, "text": "é" * 3800})
        bundle = self.assert_bounded_bundle(agent)
        self.assertLess(len(bundle["journal_tail"]), 30)
        self.assertEqual(bundle["journal_tail"][-1]["data"]["n"], 41)
        self.assertGreater(
            len(canonical(bundle)) + len(canonical(bundle["journal_tail"][0])), 131_072
        )

    @pytest.mark.contract_linux
    def test_retained_tail_counts_rows_instead_of_absolute_sequences_after_reopen(
        self,
    ) -> None:
        agent = self.fixture(durable=True, steps=41)
        before = agent.store.journal_head()
        retained = agent.store.retain(target_bytes=1, minimum_steps=12)
        self.assertTrue(retained["pruned"])
        self.assertEqual(retained["retained_steps"], 12)
        self.assert_bounded_bundle(agent)
        self.assertEqual(agent.store.journal_head(), before)
        self.assertEqual(
            require_value(agent.store.anchor(), "Expected a retained-history anchor")[
                "snapshot"
            ]["state"],
            {"n": 29},
        )
        agent.store.close()
        agent.store = Store(self.root / "durable.sqlite3")
        self.addCleanup(agent.store.close)
        bundle = self.assert_bounded_bundle(agent)
        self.assertEqual(
            bundle["journal_context"],
            {"mode": "summary", "omitted_records": 18, "retained_from": 118},
        )
        self.assertEqual(bundle["snapshot"]["state"], {"n": 41})
        agent.store._verify()
        agent.store._verify_snapshots()

    def test_short_and_empty_journals_keep_their_context_shape(self) -> None:
        agent = self.fixture(durable=False, steps=0)
        self.assertEqual(len(self.assert_bounded_bundle(agent)["journal_tail"]), 1)
        empty = Store(self.root / "absent/empty.sqlite3", durable=False)
        self.addCleanup(empty.close)
        # A read-only empty-journal probe supplies the same public projection.
        values = {
            key: agent.store.get(key)
            for key in (
                "state",
                "mode",
                "fault",
                "metadata",
                "program",
                "staged",
                "model_calls",
            )
        }
        agent.store = empty
        with patch.object(
            empty, "get", side_effect=lambda key, default=None: values.get(key, default)
        ):
            bundle = self.assert_bounded_bundle(agent)
        self.assertEqual(bundle["journal_tail"], [])
        self.assertEqual(
            bundle["journal_context"],
            {"mode": "summary", "omitted_records": 0, "retained_from": 1},
        )

    def test_oversized_current_state_still_fails_after_omitting_all_history(
        self,
    ) -> None:
        agent = self.fixture(durable=False)
        state = {"text": "x" * 131_072}
        agent.store.commit("state", {}, updates={"state": state})
        with self.assertRaisesRegex(AgentError, "Current state and role view exceed"):
            agent._bundle()
