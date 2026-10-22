"""Band qualification must detect membership-only authorization and create conflicts."""

from __future__ import annotations

import unittest

from jsonschema import ValidationError

from contracts.deploy import qualifyband
from contracts.deploy.bandprobes import probe_access_and_creation, validate_response
from tests.contracts.release.probes import ProbePeer, permissive_host


class TestBandProbe(unittest.TestCase):
    def test_both_unbound_members_and_outsiders_probe_every_route_on_both_surfaces(
        self,
    ) -> None:
        peer = ProbePeer()
        report = probe_access_and_creation(
            peer.http, [1, 2, 3], peer.data, peer.request, {"identity": "same"}
        )
        denied = [call for call in peer.calls if call[4] == 404]
        self.assertEqual(len(denied), 3 * 2 * 2 * 4)
        self.assertEqual(
            {call[2] for call in denied},
            {"observer", "outsider", "observer-agent", "outsider-agent"},
        )
        self.assertEqual(report["denial_probes"], 48)
        self.assertEqual(sum(call[4] == 201 for call in peer.calls), 3)
        self.assertEqual(sum(call[4] == 409 for call in peer.calls), 6)

    def test_create_exact_retry_must_retain_identical_identity(self) -> None:
        peer = ProbePeer()
        peer.retry_identity = "changed"
        with self.assertRaises(AssertionError):
            probe_access_and_creation(
                peer.http, [1], peer.data, peer.request, {"identity": "same"}
            )

    def test_bad_wire_bodies_fail_schema_even_when_status_is_correct(self) -> None:
        with self.assertRaises(ValidationError):
            validate_response(
                {"error": "not_found", "payload": "private"}, 404, "/v1/view", {}
            )
        schema = {"$defs": {"projection": {"type": "object", "required": ["view"]}}}
        with self.assertRaises(ValidationError):
            validate_response({"wrong": "shape"}, 200, "/v1/view", schema)
        validate_response({"view": {}}, 200, "/v1/view", schema)
        validate_response({"error": "not_found"}, 404, "/v1/view", {})

    def test_real_http_membership_only_authorization_is_rejected(self) -> None:
        peer = ProbePeer()
        with permissive_host() as port, self.assertRaises(AssertionError):
            probe_access_and_creation(
                qualifyband.http, [port], peer.data, peer.request, {}
            )
