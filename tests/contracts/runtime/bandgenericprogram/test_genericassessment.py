from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from remote_agent.band.configuration import module_command
from remote_agent.band.serve_programs import assessor
from remote_agent.program import Artifact, Program
from remote_agent.providers import CommandValidator, ProviderError
from tests.contracts.runtime.bandgenericprogram.kit import GenericAssessmentFixture
from tests.contracts.runtime.bandrolequality import SOURCE


class TestGenericAssessment(GenericAssessmentFixture):
    @pytest.mark.contract_isolation
    def test_registered_role_uses_its_own_state_abi_and_held_out_host_checks(
        self,
    ) -> None:
        result, prompt = self.assess()
        self.assertTrue(result["deployable"])
        self.assertEqual(result["runtime_profile"], "contract-role/2")
        self.assertEqual(result["quality"]["role"], "normalizer")
        self.assertEqual(result["allowed_modes"], ["program", "llm"])
        self.assertIn("normalization/1", prompt)
        self.assertNotIn('"answered"', prompt)
        self.assertNotIn("uppercase preserves spacing and punctuation", prompt)
        self.assertEqual(len(result["checks"]), 6)

    @pytest.mark.contract_isolation
    def test_generation_distinguishes_owner_observation_from_runtime_callback_inputs(
        self,
    ) -> None:
        _, prompt = self.assess()
        self.assertIn("OWNER OBSERVATION (not context.view)", prompt)
        self.assertIn("RUNTIME CALLBACK INTERFACE", prompt)
        # accepted_input intentionally removes owner receipt status, version,
        # recipients and state. Generated programs must not require those fields.
        self.assertIn(
            "event contains exactly id, session, kind, payload, actor, role", prompt
        )
        self.assertIn("context.view is the flat authenticated role view", prompt)
        self.assertIn("plain Python dictionaries", prompt)
        self.assertIn('context.view["status"] is active or terminal', prompt)
        self.assertIn('context.view["definition"]', prompt)
        self.assertNotIn("context.view.status is active or terminal", prompt)
        self.assertIn("Do not require owner-only receipt or observation fields", prompt)

    @pytest.mark.contract_isolation
    def test_evidence_schema_accepts_only_literal_visible_receipt_ids(self) -> None:
        with patch(
            "remote_agent.band.serve_programs.invoke", return_value=self.candidate
        ) as invoke:
            assessor(self.root, quality_policies=[self.quality])(
                self.context, {"id": "evidence-shape", "role": "normalizer"}
            )
        evidence = invoke.call_args.args[2]["properties"]["evidence"]
        self.assertEqual(evidence["items"]["enum"], ["accepted-1"])
        self.assertIn("bare IDs only", invoke.call_args.args[1])
        invalid = dict(self.candidate, evidence=["accepted-1: a true explanation"])
        with self.assertRaisesRegex(ValueError, "assessment_cites_absent_evidence"):
            self.assess(candidate=invalid)

    def test_an_unconfigured_role_can_be_assessed_but_cannot_be_started(self) -> None:
        result, prompt = self.assess(policies=[])
        self.assertFalse(result["deployable"])
        self.assertEqual(result["qualification"], "host_quality_policy_required")
        self.assertNotIn("pinned calculator", prompt)

    @pytest.mark.contract_isolation
    def test_candidate_cannot_supply_its_own_qualification_or_misstate_coverage(
        self,
    ) -> None:
        for extra in ({"quality": self.quality}, {"classification": "hybrid"}):
            with (
                self.subTest(extra=list(extra)),
                self.assertRaisesRegex(ValueError, "assessment_"),
            ):
                self.assess(candidate=dict(self.candidate, **extra))

    def test_policy_registration_cannot_silently_override_an_existing_identity(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "quality_policy_identity_conflict"):
            assessor(
                self.root, quality_policies=[self.quality, copy.deepcopy(self.quality)]
            )

    @pytest.mark.contract_isolation
    def test_repair_validator_command_uses_the_host_policy_and_ignores_bundle_overrides(
        self,
    ) -> None:
        artifact = Artifact.publish(SOURCE, self.root / "artifacts")
        path = self.root / "quality.json"
        path.write_text(json.dumps(self.quality))
        path.chmod(0o600)
        command = module_command(
            "program_validator",
            "--qualification",
            str(path),
            "--role",
            "normalizer",
            "--contract-sha256",
            self.context["source"]["sha256"],
        )
        result = CommandValidator(command)(
            Program(artifact), {"quality": {"role": "other", "cases": []}}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["checks"]), 6)
        command[command.index("--role") + 1] = "other"
        with self.assertRaises(ProviderError):
            CommandValidator(command)(Program(artifact), {"quality": self.quality})
