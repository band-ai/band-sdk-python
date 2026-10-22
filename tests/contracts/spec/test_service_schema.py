"""The published service schema must describe native raw configuration inputs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from contracts.spec.tools.buildserviceschema import schema as generated_schema
from remote_agent.service import ConfigurationError, load_config
from tests.paths import CONTRACTS_ROOT as ROOT


class TestServiceSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.committed_schema = json.loads(
            (ROOT / "spec/schemas/service-config.schema.json").read_text()
        )
        cls.validators = {
            label: jsonschema.Draft202012Validator(value)
            for label, value in (
                ("generator", generated_schema),
                ("published", cls.committed_schema),
            )
        }

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="service-schema-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "config.json"

    def example(self, kind: str) -> dict[str, Any]:
        return json.loads((ROOT / "deploy/examples" / (kind + ".json")).read_text())

    def native(self, config: dict[str, Any]) -> dict[str, Any]:
        self.path.write_text(json.dumps(config))
        self.path.chmod(0o600)
        # Configuration parsing only: no Service, Store, engine, provider or listener starts.
        return load_config(self.path)

    def accepted(self, config: dict[str, Any]) -> dict[str, Any]:
        parsed = self.native(config)
        for label, validator in self.validators.items():
            with self.subTest(schema=label):
                self.assertTrue(
                    validator.is_valid(config),
                    [error.message for error in validator.iter_errors(config)],
                )
        return parsed

    def refused(self, config: dict[str, Any]) -> None:
        with self.assertRaises(ConfigurationError):
            self.native(config)
        for label, validator in self.validators.items():
            with self.subTest(schema=label):
                self.assertFalse(validator.is_valid(config))

    def test_generated_and_published_schema_agree_and_are_valid(self) -> None:
        self.assertEqual(generated_schema, self.committed_schema)
        for value in (generated_schema, self.committed_schema):
            jsonschema.Draft202012Validator.check_schema(value)

    @pytest.mark.contract_linux
    def test_existing_examples_and_omitted_defaults_remain_accepted(self) -> None:
        for path in sorted((ROOT / "deploy/examples").glob("*.json")):
            with self.subTest(example=path.name):
                parsed = self.accepted(json.loads(path.read_text()))
                self.assertTrue(parsed["store"]["durable"])
                if parsed["kind"] == "agent":
                    self.assertIsNone(parsed["agent"]["initial_route"])
                else:
                    self.assertIsNone(parsed["room"]["capabilities"])

    @pytest.mark.contract_linux
    def test_supported_storage_modes_match_native_configuration(self) -> None:
        for kind, durable in (("agent", False), ("agent", True), ("room", True)):
            with self.subTest(kind=kind, durable=durable):
                config = self.example(kind)
                config["store"]["durable"] = durable
                self.assertIs(self.accepted(config)["store"]["durable"], durable)

    @pytest.mark.contract_linux
    def test_supported_initial_routes_match_native_configuration(self) -> None:
        for route in ("program", "llm"):
            with self.subTest(route=route):
                config = self.example("agent")
                config["agent"]["initial_route"] = route
                self.assertEqual(self.accepted(config)["agent"]["initial_route"], route)

    @pytest.mark.contract_linux
    def test_trusted_file_source_host_format_matches_native_configuration(self) -> None:
        for kind in ("room", "agent"):
            for format in ("frontmatter", "standard", "file-source"):
                with self.subTest(kind=kind, format=format):
                    config = self.example(kind)
                    config["engine"]["format"] = format
                    self.assertEqual(self.accepted(config)["engine"]["format"], format)
        for format in ("native", "unknown", "", None, True):
            with self.subTest(format=format):
                config = self.example("agent")
                config["engine"]["format"] = format
                self.refused(config)

    @pytest.mark.contract_linux
    def test_supported_capability_lists_match_native_configuration(self) -> None:
        for capabilities in (
            [],
            ["contract.replies/1"],
            [f"cap.x{n}/1" for n in range(16)],
        ):
            with self.subTest(capabilities=capabilities):
                config = self.example("room")
                config["room"]["capabilities"] = capabilities
                self.assertEqual(
                    self.accepted(config)["room"]["capabilities"], capabilities
                )

    @pytest.mark.contract_linux
    def test_room_durability_types_nulls_and_capability_bounds_remain_strict(
        self,
    ) -> None:
        room = self.example("room")
        room["store"]["durable"] = False
        self.refused(room)
        for field, kind, values in (
            ("durable", "agent", (None, 0, 1, "false", [], {})),
            ("initial_route", "agent", (None, True, 7, "hybrid", "", [], {})),
            (
                "capabilities",
                "room",
                (
                    None,
                    "contract.replies/1",
                    [1],
                    ["contract.replies/1"] * 2,
                    ["contract.replies/1", None],
                    ["Contract.replies/1"],
                    ["contract.replies/0"],
                    ["contract.replies/01"],
                    ["contract.replies/10000"],
                    ["contract.replies/1\n"],
                    ["\ncontract.replies/1"],
                    [f"cap.x{n}/1" for n in range(17)],
                ),
            ),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    config = self.example(kind)
                    target = config["store"] if field == "durable" else config[kind]
                    target[field] = value
                    self.refused(config)

    @pytest.mark.contract_linux
    def test_unknown_fields_remain_closed(self) -> None:
        for kind, target in (("agent", "store"), ("agent", "agent"), ("room", "room")):
            with self.subTest(kind=kind, target=target):
                config = self.example(kind)
                config[target]["unknown"] = True
                self.refused(config)


if __name__ == "__main__":
    unittest.main()
