"""UnknownRoomConfigKey specification regression checks."""

from __future__ import annotations

import json
import unittest
from typing import Any

import pytest

from contracts.spec.tools import generatevectors
from tests.contracts.spec.checkerfailures.kit import ROOT


class TestUnknownRoomConfigKey(unittest.TestCase):
    """room_new dropped unknown room_config keys: a misspelled per-actor share ran with the room-wide caps (N-6)."""

    def setup(self) -> list[dict[str, Any]]:
        return next(
            case
            for case in generatevectors.cases("v2-room-quota-retention")
            if case["id"] == "v2-room-quota-retention"
        )["requests"][:2]

    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_a_misspelled_share_is_refused_as_room_policy_required(self) -> None:
        requests = self.setup()
        requests[1]["config"]["max_mention_per_actor"] = 1
        responses = generatevectors.adapter(requests)
        self.assertEqual(responses[1], {"error": "room_policy_required"})
        responses = generatevectors.adapter(self.setup())
        self.assertIn("ok", responses[1])

    def test_the_adapter_accepts_exactly_the_schema_room_config_keys(self) -> None:
        keys = set()
        for path in (ROOT / "spec/schemas").glob("protocol*.schema.json"):
            definition = json.loads(path.read_text())["$defs"].get("room_config")
            if definition:
                self.assertIs(definition["additionalProperties"], False, path.name)
                keys |= set(definition["properties"])
        source = (ROOT / "spec/tools/reference_adapter.exs").read_text()
        line = next(
            line
            for line in source.splitlines()
            if line.strip().startswith("@room_config_keys ~w(")
        )
        self.assertEqual(set(line.split("~w(", 1)[1].rstrip(")").split()), keys)
