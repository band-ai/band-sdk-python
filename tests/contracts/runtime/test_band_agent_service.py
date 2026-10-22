from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from remote_agent.band.authoring import (
    Journal,
    author_prompt,
    output_schema,
    process_one,
)
from tests.contracts.runtime.bandagentservice import AgentServiceFixture
from tests.contracts.runtime.required import require_value


class TestAgentService(AgentServiceFixture):
    def test_live_author_reads_the_bundled_skill_before_room_history(self) -> None:
        job = {
            "operation": "generate",
            "history": [
                {
                    "id": "room-1",
                    "role": "human",
                    "text": "Please review this actual room work.",
                }
            ],
        }
        skill = (
            files("remote_agent.band")
            .joinpath("assets/room-protocol-author.md")
            .read_text()
        )
        prompt = author_prompt("EXACT PINNED GRAMMAR", job)
        self.assertIn(skill, prompt)
        self.assertLess(prompt.index(skill), prompt.index("OPERATOR JOB (DATA):"))
        self.assertIn("EXACT PINNED GRAMMAR", prompt)
        self.assertNotIn("history is synthetic", prompt)
        self.assertIn('"id":"room-1"', prompt)

    def test_model_schema_requires_a_literal_accepted_evidence_id(self) -> None:
        job = {
            "operation": "refine",
            "history": [{"id": "accepted-id", "role": "lead", "text": "cancel"}],
        }
        validator = Draft202012Validator(output_schema(job))
        value = {
            "source": "contract",
            "derivation": [{"evidence": "accepted-id", "rule": "cancel"}],
            "unresolved": [],
        }
        self.assertFalse(list(validator.iter_errors(value)))
        value["derivation"][0]["evidence"] = "accepted-id: a quotation"
        self.assertTrue(list(validator.iter_errors(value)))

    @pytest.mark.contract_linux
    def test_exact_job_retry_reuses_recorded_model_result(self) -> None:
        job = {
            "operation": "generate",
            "history": [{"id": "m1", "role": "lead", "text": "Please review"}],
        }
        calls = []

        def model(value: Any) -> dict[str, Any]:
            calls.append(value)
            return {
                "source": "typed contract source",
                "derivation": [{"evidence": "m1", "rule": "review"}],
                "unresolved": [],
            }

        self.journal.submit("job-1", job)
        process_one(self.journal, model)
        self.journal.submit("job-1", job)
        self.assertFalse(process_one(self.journal, model))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.journal.result("job-1")["status"], "complete")
        self.assertEqual(
            self.journal.result("job-1")["result"]["source"], "typed contract source"
        )
        with self.assertRaises(ValueError):
            self.journal.submit("job-1", {**job, "operation": "refine"})

    @pytest.mark.contract_linux
    def test_uncertain_inflight_call_is_held_across_restart(self) -> None:
        self.journal.submit("uncertain", {"operation": "generate", "history": []})
        self.journal.claim()
        other = Journal(Path(self.tmp.name))
        self.addCleanup(other.close)
        other.recover()
        self.assertEqual(other.result("uncertain")["status"], "held")
        self.assertFalse(
            process_one(
                other, lambda _: self.fail("must not resample an uncertain model call")
            )
        )

    @pytest.mark.contract_linux
    def test_exhausted_model_budget_reports_a_durable_hold_instead_of_waiting_forever(
        self,
    ) -> None:
        job = {"operation": "generate", "history": []}
        for index in range(20):
            identifier = "spent-" + str(index)
            self.journal.submit(identifier, job)
            self.assertEqual(
                require_value(self.journal.claim(), "Expected a claimable journal job")[
                    0
                ],
                identifier,
            )
            self.journal.finish(identifier, "complete", {})
        self.journal.submit("over-budget", job)
        self.assertFalse(
            process_one(
                self.journal, lambda _: self.fail("model budget must be honored")
            )
        )
        held = self.journal.result("over-budget")
        self.assertEqual(held["status"], "held")
        self.assertIn("budget", held["result"]["detail"])
        self.assertIsNone(held["started"])
        self.assertFalse(
            process_one(self.journal, lambda _: self.fail("held work must not restart"))
        )

    @pytest.mark.contract_linux
    def test_bad_derivation_never_becomes_a_successful_protocol(self) -> None:
        self.journal.submit(
            "bad",
            {
                "operation": "generate",
                "history": [{"id": "m1", "role": "lead", "text": "review"}],
            },
        )
        process_one(
            self.journal,
            lambda _: {
                "source": "source",
                "derivation": [{"evidence": "invented", "rule": "review"}],
                "unresolved": [],
            },
        )
        self.assertEqual(self.journal.result("bad")["status"], "failed")
