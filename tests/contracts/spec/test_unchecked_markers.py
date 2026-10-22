"""Runtime and schema regression for unchecked result marking."""

from __future__ import annotations

import copy
import unittest

import jsonschema
import pytest

from contracts.spec.tools.buildv2schemas import root as schema
from contracts.spec.tools.generatevectors import adapter
from contracts.spec.tools.uncheckedmarkers import requests, verify


class TestUncheckedMarkers(unittest.TestCase):
    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_portable_unchecked_results(self) -> None:
        verify(adapter(requests()), True)

    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_portable_checked_results_are_unmarked(self) -> None:
        verify(adapter(requests(False)), False)

    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_stripping_any_result_marker_is_detected(self) -> None:
        observed = adapter(requests())
        for index in range(2, 9):
            mutant = copy.deepcopy(observed)
            mutant[index]["decision"].pop("policy", None)
            with self.assertRaisesRegex(AssertionError, "lacks marker"):
                verify(mutant)

    def test_host_decision_schema_accepts_unchecked_marker(self) -> None:
        validator = jsonschema.Draft202012Validator(
            {**schema, "$ref": "#/$defs/host_decision"}
        )
        for status in ["host_accepted", "duplicate_host_event"]:
            validator.validate({"status": status, "policy": "unchecked"})
            with self.assertRaises(jsonschema.ValidationError):
                validator.validate({"status": status, "policy": "checked"})

    def test_tick_decision_schema(self) -> None:
        validator = jsonschema.Draft202012Validator(
            {**schema, "$ref": "#/$defs/tick_decision"}
        )
        for decision in [
            {"status": "ticked"},
            {"status": "ticked", "policy": "unchecked"},
            {"status": "rejected", "reason": "invalid_time", "policy": "unchecked"},
        ]:
            validator.validate(decision)
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate({"status": "ticked", "policy": "checked"})


if __name__ == "__main__":
    unittest.main()
