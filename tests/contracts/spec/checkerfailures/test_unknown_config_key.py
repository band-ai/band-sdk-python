"""UnknownConfigKey specification regression checks."""

from __future__ import annotations

import copy
import json
import unittest

import pytest

from contracts.spec.tools import generatevectors
from contracts.spec.tools.vectorinputs import hosted
from tests.contracts.spec.checkerfailures.kit import ROOT


class TestUnknownConfigKey(unittest.TestCase):
    """The reference adapter dropped unknown session-config keys: a typo ran with default caps (impl L18)."""

    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_a_misspelled_cap_is_refused_as_invalid_config(self) -> None:
        setup = next(
            case
            for case in generatevectors.cases("v2-band-session")
            if case["id"] == "v2-band-session"
        )["requests"][:2]
        requests = copy.deepcopy(setup)
        requests[1]["config"]["max_byte"] = 200
        responses = generatevectors.adapter(hosted(requests))
        self.assertEqual(responses[1], {"error": "invalid_config"})
        responses = generatevectors.adapter(hosted(copy.deepcopy(setup)))
        self.assertIn("ok", responses[1])

    def test_the_adapter_accepts_exactly_the_schema_config_keys(self) -> None:
        keys = set()
        for path in (ROOT / "spec/schemas").glob("protocol*.schema.json"):
            definition = json.loads(path.read_text())["$defs"].get("config")
            if definition:
                self.assertIs(definition["additionalProperties"], False, path.name)
                keys |= set(definition["properties"])
        source = (ROOT / "spec/tools/reference_adapter.exs").read_text()
        line = next(
            line
            for line in source.splitlines()
            if line.strip().startswith("@config_keys ~w(")
        )
        self.assertEqual(set(line.split("~w(", 1)[1].rstrip(")").split()), keys)
