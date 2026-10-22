"""Band's public wire boundary must preserve typed identities and closed requests."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jsonschema

from contracts.spec.tools import checkspec
from contracts.spec.tools.buildschemas import root
from tests.contracts.spec.workspace import link_or_copy

SESSION = "22222222-2222-4222-8222-222222222222"
USER = "User:33333333-3333-4333-8333-333333333333"
AGENT = "Agent:44444444-4444-4444-8444-444444444444"


class TestBandWireSchema(unittest.TestCase):
    def validator(self, name: str) -> jsonschema.Draft202012Validator:
        self.assertTrue(name in root["$defs"], f"Missing normative Band schema: {name}")
        return jsonschema.Draft202012Validator({**root, "$ref": "#/$defs/" + name})

    def test_uuid_and_typed_actor_are_canonical(self) -> None:
        uuid = self.validator("band_uuid")
        actor = self.validator("band_actor")
        self.assertTrue(uuid.is_valid(SESSION))
        for value in (USER, AGENT):
            self.assertTrue(actor.is_valid(value))
        for value in (
            "Agent:" + "-" * 36,
            AGENT.replace("4", "A"),
            AGENT.lower(),
            "Bot:" + SESSION,
            SESSION,
            AGENT + "\n",
        ):
            with self.subTest(value=value):
                self.assertFalse(actor.is_valid(value))
        for value in (SESSION.replace("-", ""), SESSION + "\n", USER):
            self.assertFalse(uuid.is_valid(value))

    def test_config_bounds_and_trusted_fields(self) -> None:
        validator = self.validator("band_session_config")
        config = {
            "bindings": {"requester": USER, "assistant": AGENT},
            "inactivity": "accepted",
            "max_messages": 100,
            "max_bytes": 16384,
        }
        self.assertTrue(validator.is_valid(config))
        for change in (
            {"now": 0},
            {"actor": USER},
            {"max_messages": 0},
            {"max_messages": 10001},
            {"max_bytes": 0},
            {"max_bytes": 65537},
            {"inactivity": "caller"},
            {"bindings": {"assistant": "worker"}},
        ):
            with self.subTest(patch=change):
                self.assertFalse(validator.is_valid({**config, **change}))
        missing = copy.deepcopy(config)
        del missing["max_bytes"]
        self.assertFalse(validator.is_valid(missing))

    def test_proposals_and_views_cannot_supply_actor_or_clock(self) -> None:
        proposal = self.validator("band_envelope")
        view = self.validator("band_view_request")
        message = {
            "id": "q",
            "session": SESSION,
            "kind": "question",
            "payload": {"text": "ping"},
            "expected_version": 0,
        }
        self.assertTrue(proposal.is_valid(message))
        self.assertTrue(view.is_valid({}))
        for extra in ({"actor": USER}, {"now": 0}, {"role": "requester"}):
            self.assertFalse(proposal.is_valid({**message, **extra}))
            self.assertFalse(view.is_valid(extra))
        self.assertFalse(proposal.is_valid({**message, "session": "s"}))

    def test_band_identity_requires_durable_inbox_and_typed_actor(self) -> None:
        validator = self.validator("band_http_identity")
        identity = {
            "profile": "contract-room/http/1",
            "session": SESSION,
            "actor": AGENT,
            "identity": {"definition": "a" * 64, "source": "b" * 64, "ready": True},
            "idempotent": True,
            "inbox_profile": "contract-room/inbox/1",
        }
        self.assertTrue(validator.is_valid(identity))
        for key in ("inbox_profile", "idempotent"):
            missing = copy.deepcopy(identity)
            del missing[key]
            self.assertFalse(validator.is_valid(missing))
        for change in ({"actor": "worker"}, {"session": "s"}, {"idempotent": False}):
            self.assertFalse(validator.is_valid({**identity, **change}))

    def test_checker_evaluates_examples_even_when_their_hash_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            shutil.copytree(
                checkspec.ROOT / "spec",
                root_path / "spec",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            for name in (
                "deploy",
                "README.md",
                "PRODUCTION.md",
                "agent-runtime",
                "header-spikes",
                "contract-lab",
            ):
                link_or_copy(checkspec.ROOT / name, root_path / name)
            path = root_path / "spec/conformance/band-wire-examples.json"
            fixture = json.loads(path.read_text())
            fixture["examples"][0]["valid"] = (
                False  # A valid create must not pass as an invalid example.
            )
            path.write_text(json.dumps(fixture, indent=2) + "\n")
            manifest_path = path.with_name("manifest.json")
            manifest = json.loads(manifest_path.read_text())
            for entry in manifest["artifacts"]:
                if entry["path"] == path.name:
                    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            with (
                patch.object(checkspec, "ROOT", root_path),
                patch("sys.argv", ["checkspec", "--schema-only"]),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(AssertionError, "Band wire example differs"),
            ):
                checkspec.main()


if __name__ == "__main__":
    unittest.main()
