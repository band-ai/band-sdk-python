"""N1Schema specification regression checks."""

from __future__ import annotations

import unittest

from contracts.spec.tools import checkbandbinding as binding
from tests.contracts.spec.bandbinding.kit import fixture


class TestN1Schema(unittest.TestCase):
    def test_human_creator_identity_is_valid_in_plain_and_carrier_profiles(
        self,
    ) -> None:
        identity = fixture()["creation"][0]["expected"]["identity"]
        binding.validator("band_http_identity").validate(identity)
        carrier = {
            "profile": "band-contract/carrier/1",
            "name": "band-room/1",
            "fields": {
                "id": "body",
                "session": "path:id",
                "kind": "body",
                "expected_version": "body",
            },
        }
        binding.validator("band_carrier_http_identity").validate(
            {**identity, "carrier": carrier}
        )

    def test_authenticated_actors_are_canonical_but_bindings_remain_agents_only(
        self,
    ) -> None:
        actor = binding.validator("band_authenticated_actor")
        for value in (binding.USER, binding.AGENT_A):
            actor.validate(value)
        for value in (
            binding.USER + "\n",
            binding.USER.lower(),
            "Bot:" + binding.SESSION,
            "User:" + "-" * 36,
            binding.USER.replace("3", "A"),
        ):
            self.assertFalse(actor.is_valid(value), value)
        config = fixture()["creation"][0]["request"]["config"]
        binding.validator("band_session_config").validate(config)
        config["bindings"]["requester"] = binding.USER
        self.assertFalse(binding.validator("band_session_config").is_valid(config))
        self.assertFalse(binding.validator("band_actor").is_valid(binding.USER))
