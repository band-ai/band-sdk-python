from __future__ import annotations

import hashlib
import json
from contextlib import closing
from typing import cast

import pytest

from remote_agent.band.serve_programs import assessor
from remote_agent.transport import EngineProcess, TransportError
from tests.contracts.runtime.bandqualityauthoring import (
    MIXED_SOURCE,
    QualityAuthoringFixture,
    example_plan,
    mixed_plan,
)
from tests.contracts.runtime.bandrolequality import SOURCE
from tests.contracts.runtime.filesource import normalization_source, package
from tests.paths import CONTRACT_RUNTIME_TESTS


class TestQualityAuthoring(QualityAuthoringFixture):
    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_new_role_freezes_independent_examples_before_generating_candidate(
        self,
    ) -> None:
        result, prompts = self.assess()
        self.assertTrue(result["deployable"])
        self.assertEqual(len(prompts), 2)
        self.assertEqual(result["qualification_origin"], "independent-planning")
        self.assertEqual(
            result["quality"]["source_sha256"], self.context["source"]["sha256"]
        )
        self.assertEqual(len(result["checks"]), 3)
        first = result["quality"]["cases"][0]
        self.assertEqual(
            set(first["event"]), {"id", "session", "actor", "role", "kind", "payload"}
        )
        self.assertEqual(first["view"]["state"], "normalizing")
        self.assertEqual(first["view"]["status"], "active")
        self.assertEqual(result["allowed_modes"], ["program", "llm"])

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_standard_package_qualification_keeps_canonical_source_and_definition_identity(
        self,
    ) -> None:
        self.context["source"] = normalization_source()
        with closing(
            EngineProcess(
                package(self.root, self.context["source"]),
                format="standard",
                command=self.engine_command,
            )
        ) as engine:
            self.context["identity"] = engine.identity
        result, prompts = self.assess()
        self.assertTrue(result["deployable"])
        self.assertEqual(len(prompts), 2)
        self.assertEqual(
            result["quality"]["source_sha256"], self.context["source"]["sha256"]
        )
        self.assertEqual(len(result["checks"]), 3)
        path = (
            self.root / self.request["id"] / "quality-author" / "contract-source.json"
        )
        self.assertEqual(json.loads(path.read_text()), self.context["source"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(path.with_name("contract.band").exists())

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_inconsistent_standard_body_never_reaches_candidate_generation(
        self,
    ) -> None:
        self.context["source"] = normalization_source()
        self.context["source"]["body"] += "\n"
        with self.assertRaises(TransportError):
            self.assess()
        self.assertEqual(len(self.timeouts), 1)
        self.assertFalse(
            (
                self.root / self.request["id"] / "quality-author" / "quality.json"
            ).exists()
        )

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_standard_definition_conflict_never_reaches_candidate_generation(
        self,
    ) -> None:
        self.context["source"] = normalization_source()
        self.context["identity"] = {"definition": "0" * 64}
        with self.assertRaisesRegex(ValueError, "quality_plan_engine_identity"):
            self.assess()
        self.assertEqual(len(self.timeouts), 1)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_standard_qualification_refuses_changed_pinned_body_on_resume(self) -> None:
        self.context["source"] = normalization_source()
        self.assess()
        path = (
            self.root / self.request["id"] / "quality-author" / "contract-source.json"
        )
        saved = path.read_bytes()
        self.context["source"]["body"] += "\n"
        with self.assertRaisesRegex(ValueError, "quality_plan_identity_conflict"):
            self.assess()
        self.assertEqual(path.read_bytes(), saved)
        self.assertEqual(len(self.timeouts), 1)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_genuine_hybrid_handles_typed_arithmetic_and_delegates_language(
        self,
    ) -> None:
        source = (
            CONTRACT_RUNTIME_TESTS / "fixtures/band/mixed-assistant.band.txt"
        ).read_text()
        self.context["source"].update(
            content=source, sha256=hashlib.sha256(source.encode()).hexdigest()
        )
        self.context["config"]["bindings"] = {
            "requester": "Agent:requester",
            "assistant": "Agent:assistant",
        }
        self.request["role"] = "assistant"
        candidate = dict(
            self.candidate,
            classification="hybrid",
            source=MIXED_SOURCE,
            handles=["bounded sums"],
            delegates=["translation"],
        )
        result, _ = self.assess(plan=mixed_plan(), candidate=candidate)
        self.assertTrue(result["deployable"])
        self.assertEqual(result["allowed_modes"], ["hybrid", "llm"])
        self.assertEqual(len(result["checks"]), 4)
        judgment = result["quality"]["cases"][2]
        self.assertEqual(judgment["event"]["kind"], "translate")
        self.assertEqual(judgment["view"]["state"], "translating")
        self.assertEqual(judgment["expected"], {"type": "delegate"})

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_generator_receives_frozen_hybrid_coverage_without_private_examples(
        self,
    ) -> None:
        source = (
            CONTRACT_RUNTIME_TESTS / "fixtures/band/mixed-assistant.band.txt"
        ).read_text()
        self.context["source"].update(
            content=source, sha256=hashlib.sha256(source.encode()).hexdigest()
        )
        self.context["config"]["bindings"] = {
            "requester": "Agent:requester",
            "assistant": "Agent:assistant",
        }
        self.request["role"] = "assistant"
        plan = mixed_plan()
        plan["reason"] = "private independent planning rationale"
        plan["cases"][0]["name"] = "withheld hybrid qualification example"
        candidate = dict(
            self.candidate,
            classification="hybrid",
            source=MIXED_SOURCE,
            handles=plan["handles"],
            delegates=plan["delegates"],
        )
        result, prompts = self.assess(plan=plan, candidate=candidate)
        self.assertTrue(result["deployable"])
        marker = "FROZEN COVERAGE BOUNDARY:\n"
        self.assertIn(marker, prompts[1])
        coverage, _ = json.JSONDecoder().raw_decode(prompts[1].split(marker, 1)[1])
        self.assertEqual(
            coverage,
            {key: plan[key] for key in ("classification", "handles", "delegates")},
        )
        for private_value in (
            plan["reason"],
            plan["cases"][0]["name"],
            plan["cases"][0]["input_id"],
        ):
            self.assertNotIn(private_value, prompts[1])
        frozen = json.loads(
            (
                self.root / self.request["id"] / "quality-author" / "plan.json"
            ).read_text()
        )
        self.assertEqual(frozen, plan)
        self.assertEqual(len(prompts), 2)

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_candidate_cannot_replace_frozen_hybrid_classification_with_program(
        self,
    ) -> None:
        source = (
            CONTRACT_RUNTIME_TESTS / "fixtures/band/mixed-assistant.band.txt"
        ).read_text()
        self.context["source"].update(
            content=source, sha256=hashlib.sha256(source.encode()).hexdigest()
        )
        self.context["config"]["bindings"] = {
            "requester": "Agent:requester",
            "assistant": "Agent:assistant",
        }
        self.request["role"] = "assistant"
        with self.assertRaisesRegex(ValueError, "assessment_planned_coverage_mismatch"):
            self.assess(
                plan=mixed_plan(), candidate=dict(self.candidate, source=MIXED_SOURCE)
            )
        self.assertEqual(len(self.timeouts), 2)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_wrong_business_candidate_cannot_certify_itself(self) -> None:
        candidate = dict(
            self.candidate, source=SOURCE.replace("].upper()", "].lower()")
        )
        with self.assertRaisesRegex(ValueError, "semantic_case_mismatch"):
            self.assess(candidate=candidate)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_accepted_prefix_reply_can_precede_the_selected_later_input(self) -> None:
        source = (
            CONTRACT_RUNTIME_TESTS / "fixtures/band/mixed-assistant.band.txt"
        ).read_text()
        self.context["source"].update(
            content=source, sha256=hashlib.sha256(source.encode()).hexdigest()
        )
        self.context["config"]["bindings"] = {
            "requester": "Agent:requester",
            "assistant": "Agent:assistant",
        }
        self.request["role"] = "assistant"
        candidate = dict(
            self.candidate,
            classification="hybrid",
            source=MIXED_SOURCE,
            handles=["bounded sums"],
            delegates=["translation"],
        )
        plan = mixed_plan()
        common = plan["cases"][0]
        common["trace"][:0] = [
            {
                "role": "requester",
                "id": "earlier-words",
                "kind": "translate",
                "payload_json": '{"text":"Le chat est noir.","language":"English"}',
            },
            {
                "role": "assistant",
                "id": "earlier-translation",
                "kind": "translated",
                "payload_json": '{"request":"earlier-words","text":"The cat is black."}',
            },
        ]
        common["state_json"] = '{"completed":1}'
        expected = json.loads(common["expected_json"])
        expected["state"] = {"completed": 2}
        common["expected_json"] = json.dumps(expected)
        result, _ = self.assess(plan=plan, candidate=candidate)
        self.assertTrue(result["deployable"])
        self.assertEqual(result["quality"]["cases"][0]["event"]["id"], "sum-first")
        self.assertEqual(result["quality"]["cases"][0]["view"]["version"], 3)
        self.assertEqual(
            result["quality"]["cases"][0]["expected"]["state"], {"completed": 2}
        )

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_unrelated_prefix_does_not_supply_a_selected_role_startup_hook(
        self,
    ) -> None:
        source = """---
name: unrelated-prefix
description: A message to another role cannot initiate the selected participant.
engine: 0.2.0-lab.1
roles: [requester, normalizer, observer]
---
contract @30m mentions 1 => expired
type aside
type prologue
type normalize
  text: text(bytes: 1..80)
type normalized
  request: ref(normalize)
  text: text(bytes: 1..80)
type finish
state new
  requester -> observer: aside() => starting
state starting
  normalizer -> requester: prologue() => collecting
state collecting
  requester -> normalizer: normalize() as input => normalizing
state normalizing
  normalizer -> requester: normalized(request=$input) => ready
state ready
  requester -> normalizer: finish() => complete
end complete
end expired
"""
        self.context["source"].update(
            content=source, sha256=hashlib.sha256(source.encode()).hexdigest()
        )
        self.context["config"]["bindings"]["observer"] = "Agent:observer"
        plan = example_plan()
        common = plan["cases"][0]
        common["trace"][:0] = [
            {
                "role": "requester",
                "id": "unrelated",
                "kind": "aside",
                "payload_json": "{}",
            },
            {
                "role": "normalizer",
                "id": "unsolicited-prologue",
                "kind": "prologue",
                "payload_json": "{}",
            },
        ]
        with self.assertRaisesRegex(
            ValueError, "quality_plan_initial_trigger_required"
        ):
            self.assess(plan=plan)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_callback_failure_gets_one_correction_against_unchanged_examples(
        self,
    ) -> None:
        broken = SOURCE.replace('context.view.get("status")', "context.view.status")
        result, prompts = self.assess(
            candidate=dict(self.candidate, source=broken), correction={"source": SOURCE}
        )
        self.assertTrue(result["deployable"])
        self.assertEqual(result["candidate_repair_count"], 1)
        self.assertEqual(
            result["initial_program_sha256"],
            hashlib.sha256(broken.encode()).hexdigest(),
        )
        self.assertEqual(len(prompts), 3)
        self.assertIn(broken, prompts[2])
        self.assertNotIn("withheld spacing and punctuation oracle", prompts[2])
        frozen = json.loads(
            (self.root / self.request["id"] / "quality-author/quality.json").read_text()
        )
        self.assertEqual(result["quality"], frozen)
        self.assertIn("corrected", result["reason"])
        self.assertEqual(self.timeouts, [60, 103, 96])

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_repeated_callback_failure_holds_without_another_correction(self) -> None:
        broken = SOURCE.replace('context.view.get("status")', "context.view.status")
        with self.assertRaisesRegex(ValueError, "semantic_callback_failed"):
            self.assess(
                candidate=dict(self.candidate, source=broken),
                correction={"source": broken},
            )
        self.assertEqual(len(self.timeouts), 3)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_wrong_business_result_is_not_exposed_to_correction(self) -> None:
        broken = SOURCE.replace("].upper()", "].lower()")
        with self.assertRaisesRegex(ValueError, "semantic_case_mismatch"):
            self.assess(
                candidate=dict(self.candidate, source=broken),
                correction={"source": SOURCE},
            )
        self.assertEqual(len(self.timeouts), 2)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_malformed_callback_correction_cannot_replace_the_candidate(self) -> None:
        broken = SOURCE.replace('context.view.get("status")', "context.view.status")
        with self.assertRaisesRegex(ValueError, "assessment_repair_source_invalid"):
            self.assess(
                candidate=dict(self.candidate, source=broken),
                correction={"source": SOURCE, "quality": {}},
            )
        self.assertEqual(len(self.timeouts), 3)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_planning_and_candidate_share_the_existing_assessment_time_budget(
        self,
    ) -> None:
        self.assess()
        self.assertEqual(self.timeouts, [60, 103])

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine("band")
    def test_explicit_assessment_budget_covers_planning_and_candidate_without_reset(
        self,
    ) -> None:
        result, _ = self.assess(assessment_timeout_seconds=600)
        self.assertTrue(result["deployable"])
        self.assertEqual(self.timeouts, [240, 553])
        self.assertEqual(self.timeout_limits, [600, 600])

    def test_direct_assessor_cannot_bypass_the_operator_deadline_bounds(self) -> None:
        for invalid in (59, 901, True, 150.5, "600", None):
            with self.assertRaisesRegex(ValueError, "assessment_timeout"):
                assessor(self.root, assessment_timeout_seconds=cast(int, invalid))

    def test_automatic_qualification_is_an_explicit_boolean_host_setting(self) -> None:
        with self.assertRaisesRegex(ValueError, "auto_qualification"):
            assessor(self.root, auto_qualification=cast(bool, "yes"))

    @pytest.mark.contract_linux
    def test_essential_model_classification_does_not_sample_a_program(self) -> None:
        plan = dict(
            example_plan(),
            classification="llm",
            reason="Essential interpretation requires a model.",
            handles=[],
            delegates=["open-ended interpretation"],
            cases=[],
        )
        result, prompts = self.assess(plan=plan)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(result["classification"], "llm")
        self.assertFalse(result["deployable"])
        self.assertEqual(result["source"], "")
        self.assertEqual(result["qualification"], "llm_required")

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_invalid_trace_never_reaches_candidate_generation(self) -> None:
        plan = example_plan()
        plan["cases"][0]["trace"][0]["kind"] = "unknown_message"
        with self.assertRaisesRegex(ValueError, "quality_plan_trace"):
            self.assess(plan=plan)

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_qualification_cannot_invent_a_startup_action_for_an_input_driven_role(
        self,
    ) -> None:
        self.request["role"] = "requester"
        plan = example_plan()
        common = plan["cases"][0]
        common["trace"].append(
            {
                "role": "normalizer",
                "id": "first-response",
                "kind": "normalized",
                "payload_json": '{"request":"quality-first","text":"  BAND 42!  "}',
            }
        )
        common["input_id"] = "first-response"
        common["expected_json"] = json.dumps(
            {
                "type": "handled",
                "state": {"processed": 1, "mode": "ascii"},
                "message": {"kind": "normalize", "payload": {"text": "Next input"}},
            }
        )
        with self.assertRaisesRegex(
            ValueError, "quality_plan_initial_trigger_required"
        ):
            self.assess(plan=plan)

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_malformed_actor_data_is_rejected_before_generating_code(self) -> None:
        plan = example_plan()
        plan["cases"][0]["trace"][0]["role"] = []
        with self.assertRaisesRegex(ValueError, "quality_plan_trace"):
            self.assess(plan=plan)

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_terminal_oracle_cannot_change_an_integer_counter_to_a_boolean(
        self,
    ) -> None:
        plan = example_plan()
        terminal = plan["cases"][-1]
        terminal["state_json"] = '{"processed":1,"mode":"ascii"}'
        terminal["expected_json"] = (
            '{"type":"handled","state":{"processed":true,"mode":"ascii"},"message":null}'
        )
        with self.assertRaisesRegex(ValueError, "quality_plan_expected"):
            self.assess(plan=plan)

    @pytest.mark.contract_linux
    def test_missing_boundary_or_terminal_is_not_a_qualification(self) -> None:
        for category in ("boundary", "terminal"):
            with self.subTest(category=category):
                plan = example_plan()
                plan["cases"] = [
                    case for case in plan["cases"] if case["category"] != category
                ]
                with self.assertRaisesRegex(ValueError, "quality_plan_cases"):
                    self.assess(plan=plan)

    @pytest.mark.contract_linux
    def test_planner_cannot_choose_host_commands(self) -> None:
        plan = dict(example_plan(), engine_command=["untrusted-command"])
        with self.assertRaisesRegex(ValueError, "quality_plan_fields"):
            self.assess(plan=plan)
