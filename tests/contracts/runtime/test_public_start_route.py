"""Keep a public llm start request pinned through native driver recovery."""

from __future__ import annotations

import json
from contextlib import closing

import pytest

from band.contracts.programs import ProgramStart
from tests.contracts.runtime.bandstandardsource import GenericStandardSourceFixture


@pytest.mark.contract_isolation
@pytest.mark.contract_engine("band")
class TestPublicStartRoute(GenericStandardSourceFixture):
    def test_public_llm_start_sets_native_route_and_recovery_keeps_its_pin(
        self,
    ) -> None:
        self.authority()
        start = ProgramStart(
            id="model-start",
            assessment_id="qualified",
            mode="llm",
            journal=True,
            target_bytes=1_000_000,
            minimum_steps=3,
        )
        with closing(self.start(start.body())) as first:
            self.assertEqual(first.options, start.body())
            self.assertEqual(first.service.store.get("mode"), "llm")
            self.assertEqual(first.service.store.get("config")["initial_route"], "llm")
            self.assertEqual(first.service.store.get("model_calls"), 0)
            qualified_program = first.service.store.get("program")
        config_path = self.workspace / "service.json"
        pinned = config_path.read_bytes()
        self.assertEqual(json.loads(pinned)["agent"]["initial_route"], "llm")
        with closing(self.start(start.body())) as restarted:
            self.assertEqual(restarted.service.store.get("mode"), "llm")
            self.assertEqual(restarted.service.store.get("model_calls"), 0)
            self.assertEqual(restarted.service.store.get("program"), qualified_program)
        self.assertEqual(config_path.read_bytes(), pinned)
        changed = ProgramStart.model_validate(dict(start.body(), mode="program"))
        with self.assertRaisesRegex(ValueError, "pinned_runtime_configuration_changed"):
            self.start(changed.body())
        self.assertEqual(config_path.read_bytes(), pinned)
        with closing(self.start(start.body())) as recovered:
            self.assertEqual(recovered.service.store.get("mode"), "llm")
            self.assertEqual(recovered.service.store.get("model_calls"), 0)
            self.assertEqual(recovered.service.store.get("program"), qualified_program)
