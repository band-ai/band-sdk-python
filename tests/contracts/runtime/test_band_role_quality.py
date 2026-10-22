from __future__ import annotations

import pytest

from tests.contracts.runtime.bandrolequality import SOURCE, RoleQualityFixture, policy


class TestRoleQuality(RoleQualityFixture):
    @pytest.mark.contract_isolation
    def test_role_defined_state_and_messages_pass_real_worker_semantic_checks(
        self,
    ) -> None:
        original = policy()
        result = self.qualify(qualification=original)
        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"]["mode"], "full")
        self.assertEqual(len(result["checks"]), 6)
        self.assertEqual(original, policy())

    @pytest.mark.contract_isolation
    def test_valid_sdk_role_and_state_guards_are_not_mistaken_for_delegation(
        self,
    ) -> None:
        guarded = SOURCE.replace(
            '    payload = event.get("payload", {})',
            '    if (context.view.get("state") != "normalizing" or event.get("role") != "requester"\n'
            '        or set(event) != {"id", "session", "kind", "payload", "actor", "role"}):\n'
            '        return Delegate("Not an authenticated normalization input")\n'
            '    payload = event.get("payload", {})',
        )
        self.assertTrue(self.qualify(guarded)["ok"])

    @pytest.mark.contract_isolation
    def test_legal_looking_wrong_business_output_wrong_state_and_boolean_counter_fail(
        self,
    ) -> None:
        for source in (
            SOURCE.replace("].upper()", "].lower()"),
            SOURCE.replace('state["processed"] + 1', 'state["processed"] + 10'),
            SOURCE.replace('state["processed"] + 1', "True"),
        ):
            with (
                self.subTest(source=source[-100:]),
                self.assertRaisesRegex(ValueError, "semantic"),
            ):
                self.qualify(source)

    def test_host_policy_is_pinned_to_contract_and_role(self) -> None:
        for scope in ({"role": "other"}, {"contract_sha256": "c" * 64}):
            with (
                self.subTest(scope=scope),
                self.assertRaisesRegex(ValueError, "quality_scope"),
            ):
                self.qualify(**scope)

    @pytest.mark.contract_isolation
    def test_policy_cannot_be_empty_or_allow_a_candidate_to_delegate_every_common_case(
        self,
    ) -> None:
        empty = policy()
        empty["cases"] = []
        with self.assertRaisesRegex(ValueError, "quality_policy"):
            self.qualify(qualification=empty)
        delegated = SOURCE.replace('"mode":"full"', '"mode":"partial"').replace(
            'next_state = {"processed":state["processed"] + 1,"mode":"ascii"}',
            'return Delegate("No proven deterministic case")\n    next_state = state',
        )
        with self.assertRaisesRegex(ValueError, "semantic_incomplete_coverage"):
            self.qualify(delegated)

    @pytest.mark.contract_isolation
    def test_state_version_and_initial_state_remain_explicit(self) -> None:
        for source in (
            SOURCE.replace("normalization/1", "normalization/2"),
            SOURCE.replace(
                'return {"processed":0,"mode":"ascii"}',
                'return {"processed":1,"mode":"ascii"}',
            ),
        ):
            with (
                self.subTest(source=source[:50]),
                self.assertRaisesRegex(ValueError, "semantic"),
            ):
                self.qualify(source)
