"""Validate requirements, schemas, fixture hashes, independent assertions and adapter output."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import re
import sys
import tempfile
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, NoReturn

import jsonschema

from contracts.spec.tools.buildcoverage import coverage
from contracts.spec.tools.buildschemas import root as expected_protocol_schema
from contracts.spec.tools.buildserviceschema import schema as expected_service_schema
from contracts.spec.tools.buildv2schemas import root as expected_v2_schema
from contracts.spec.tools.generatevectors import (
    adapter,
    adapter_argv,
    adapter_command,
    check_parity,
    checked_argv,
    pointer,
)
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT
from contracts.spec.tools.vectorinputs import trusted_policy
from contracts.spec.tools.writerequirements import catalog as expected_requirements
from remote_agent import operations
from remote_agent.invocations import InvocationJournal, InvocationUnresolved
from remote_agent.openaiprovider import OpenAIProvider
from remote_agent.program import Artifact, Program
from remote_agent.protocol import canonical
from remote_agent.storage import digest as journal_digest
from remote_agent.store import Store

logger = logging.getLogger(__name__)

QUERY_SCHEMAS = {
    "contract": "compiled",
    "role": "role_definition",
    "type": "type",
    "state": "state_declaration",
    "outcome": "terminal",
    "room": "scope_definition",
    "any_state": "scope_definition",
}


def comparison_bytes(request: Any, response: Any) -> bytes:
    """Compare normative values, allowing only compiler diagnostic detail to vary."""
    encoded = canonical(
        response
    )  # Even ignored diagnostic values must be portable JSON.
    if request["op"] == "compile" and isinstance(response, dict):
        error = response.get("error")
        if isinstance(error, dict) and set(error) == {"code", "line", "detail"}:
            return canonical({**response, "error": {**error, "detail": None}})
    return encoded


class ReplayProgram(Program):
    def execute(self, request: dict[str, Any], *, max_bytes: int = 262_144) -> NoReturn:
        raise AssertionError("recovery executed a program")


def check_replay(fixture: dict[str, Any], validate: Callable[[Any, str], None]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        for complete in (False, True):
            with Store(
                Path(directory) / ("complete.db" if complete else "interrupted.db")
            ) as store:
                frames = fixture["journal"] if complete else fixture["journal"][:1]
                for frame in frames:
                    assert (
                        store.commit(
                            frame["kind"], frame["data"], updates=frame["updates"]
                        )
                        == frame
                    )
                input = fixture["input"]
                program = ReplayProgram(
                    Artifact(**input["program"]), timeout=input["timeout_ms"] / 1000
                )
                invoke = InvocationJournal(store)
                if complete:
                    captured = invoke.run(
                        program,
                        input["request"],
                        invocation_id=input["id"],
                        purpose=input["purpose"],
                    )
                    assert captured == fixture["output"]["execution"]
                    assert Program.result(captured) == fixture["recovered_result"]
                else:
                    try:
                        invoke.run(
                            program,
                            input["request"],
                            invocation_id=input["id"],
                            purpose=input["purpose"],
                        )
                    except InvocationUnresolved:
                        pass
                    else:
                        raise AssertionError("interrupted invocation must hold")
                assert len(store.records()) == len(frames)
                archive = Path(directory) / (
                    "complete.jsonl" if complete else "interrupted.jsonl"
                )
                validate(
                    operations.export(store.path, archive), "journal_export_header"
                )
                recorded = operations.recorded(archive, input["id"])
                validate(recorded, "recorded_result")
                assert recorded["replayable"] == complete


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--adapter-config", type=Path)
    parser.add_argument(
        "--case-prefix",
        help="Execute only matching adapter cases; still validate the full bundle",
    )
    args = parser.parse_args()
    # Refuse a malformed or falsy configuration before any check runs; only an absent one means the reference adapter.
    configured = (
        checked_argv(json.loads(args.adapter_config.read_text()))
        if args.adapter_config
        else None
    )
    schema = json.loads((ROOT / "spec/schemas/protocol.schema.json").read_text())
    assert schema == expected_protocol_schema, "Regenerate the protocol schema"
    jsonschema.Draft202012Validator.check_schema(schema)
    service_schema = json.loads(
        (ROOT / "spec/schemas/service-config.schema.json").read_text()
    )
    assert service_schema == expected_service_schema, (
        "Regenerate the service configuration schema"
    )
    jsonschema.Draft202012Validator.check_schema(service_schema)
    service_validator = jsonschema.Draft202012Validator(service_schema)
    for example in (ROOT / "deploy/examples").glob("*.json"):
        service_validator.validate(json.loads(example.read_text()))
    validators = {
        name: jsonschema.Draft202012Validator(
            dict(schema, **{"$ref": "#/$defs/" + name})
        )
        for name in schema["$defs"]
    }
    v2_schema = json.loads((ROOT / "spec/schemas/protocol-v2.schema.json").read_text())
    assert v2_schema == expected_v2_schema, "Regenerate the profile-2 protocol schema"
    jsonschema.Draft202012Validator.check_schema(v2_schema)
    v2_validators = {
        name: jsonschema.Draft202012Validator(
            dict(v2_schema, **{"$ref": "#/$defs/" + name})
        )
        for name in v2_schema["$defs"]
    }
    current_validators = {2: v2_validators}
    for number, module, name in (
        (3, "buildcoordinationschemas", "protocol-coordination.schema.json"),
        (4, "buildparticipantschemas", "protocol-participants.schema.json"),
        (5, "buildcontributionschemas", "protocol-contributions.schema.json"),
        (6, "buildrepliesschemas", "protocol-replies.schema.json"),
    ):
        registry = json.loads((ROOT / "spec/schemas" / name).read_text())
        assert registry == import_module("contracts.spec.tools." + module).root, (
            "Regenerate the explicitly declared current schema: " + name
        )
        jsonschema.Draft202012Validator.check_schema(registry)
        current_validators[number] = {
            key: jsonschema.Draft202012Validator({**registry, "$ref": "#/$defs/" + key})
            for key in registry["$defs"]
        }

    def validate(value: Any, name: str) -> None:
        validators[name].validate(value)

    requirements = json.loads((ROOT / "spec/requirements.json").read_text())
    assert requirements == expected_requirements, "Regenerate the requirements catalog"
    assert (
        f"Specification revision: **{requirements['revision']}**"
        in (ROOT / "spec/README.md").read_text()
    ), "Specification overview revision differs"
    v2_validators["requirements"].validate(requirements)
    identifiers = {row["id"] for row in requirements["requirements"]}
    assert len(identifiers) == len(requirements["requirements"])
    for requirement in requirements["requirements"]:
        assert (ROOT / "spec" / requirement["specification"]).is_file()
        assert requirement["level"] in requirement["statement"], requirement["id"]
    for path in (ROOT / "spec").glob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            assert (path.parent / target.split("#")[0]).exists(), (path.name, target)
    manifest = json.loads((ROOT / "spec/conformance/manifest.json").read_text())
    assert manifest["revision"] == requirements["revision"], (
        "Specification bundle revisions differ"
    )
    for entries in (manifest["cases"], manifest["artifacts"]):
        assert len({entry["path"] for entry in entries}) == len(entries), (
            "Duplicate manifest paths"
        )
    assert len({entry["id"] for entry in manifest["cases"]}) == len(
        manifest["cases"]
    ), "Duplicate case IDs"
    assert {entry["path"] for entry in manifest["cases"]} == {
        path.name for path in (ROOT / "spec/conformance").glob("case-*.json")
    }, "Case inventory differs from manifest"
    invocation_count = 0
    bandexamples = None
    current_band_examples = None
    for entry in manifest.get("artifacts", []):
        assert set(entry["requirements"]) <= identifiers, "Unknown artifact requirement"
        assert len(set(entry["requirements"])) == len(entry["requirements"]), (
            "Duplicate artifact requirements"
        )
        path = ROOT / "spec/conformance" / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], (
            path.name
        )
        value = json.loads(path.read_text())
        validate(value, entry["schema"])
        if entry["schema"] == "band_wire_examples":
            assert path.name in (
                "band-wire-examples.json",
                "band-wire-examples-v2.json",
            ), "Unexpected Band wire example path"
            current_artifact = path.name == "band-wire-examples-v2.json"
            if current_artifact:
                assert current_band_examples is None, (
                    "Duplicate current Band wire example artifact"
                )
                current_band_examples = value["examples"]
            else:
                assert bandexamples is None, "Duplicate Band wire example artifact"
                bandexamples = value["examples"]
            examples = value["examples"]
            assert len({item["id"] for item in examples}) == len(examples), (
                "Duplicate Band wire example ID"
            )
            artifact_validators = v2_validators if current_artifact else validators
            for item in examples:
                assert (
                    artifact_validators[item["schema"]].is_valid(item["value"])
                    == item["valid"]
                ), "Band wire example differs: " + item["id"]
        if entry["schema"] == "invocation_fixture":
            assert value["output"]["input_hash"] == journal_digest(value["input"])
            for frame in value["journal"]:
                assert frame["hash"] == journal_digest(
                    {k: v for k, v in frame.items() if k != "hash"}
                )
            if not args.schema_only:
                check_replay(value, validate)
                invocation_count += 2
    fixtures = []
    if not args.schema_only:
        request = json.loads(
            (ROOT / "spec/conformance/openai-request.json").read_text()
        )
        recorded = json.loads(
            (ROOT / "spec/conformance/openai-result.json").read_text()
        )
        expected_usage = json.loads(
            (ROOT / "spec/conformance/openai-usage.json").read_text()
        )
        provider = OpenAIProvider(
            ROOT / "spec/conformance",
            model=request["body"]["model"],
            pricing=request["pricing"],
        )
        execution = {
            "profile": "remote-agent/process-result/1",
            "stdout": base64.b64encode(canonical(recorded)).decode(),
            "stderr": "",
            "exit_code": 0,
            "failure": None,
            "complete": True,
            "elapsed_ms": 1,
        }
        validate(execution, "process_result")
        validate(provider.identity, "openai_provider")
        assert provider.usage(execution) == expected_usage
        assert provider.decode("respond", execution) == {
            "state": {"count": 1},
            "message": None,
            "scope": "turn",
        }
        ceil_recorded = json.loads(
            (ROOT / "spec/conformance/openai-result-ceil.json").read_text()
        )
        ceil_usage = json.loads(
            (ROOT / "spec/conformance/openai-usage-ceil.json").read_text()
        )
        ceil_execution = dict(
            execution, stdout=base64.b64encode(canonical(ceil_recorded)).decode()
        )
        validate(ceil_execution, "process_result")
        assert provider.usage(ceil_execution) == ceil_usage
        assert ceil_usage["estimated_cost_micro_usd"] == 59
    for entry in manifest["cases"]:
        path = ROOT / "spec/conformance" / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], (
            path.name
        )
        fixture = json.loads(path.read_text())
        assert (
            fixture["id"] == entry["id"] and set(fixture["requirements"]) <= identifiers
        )
        assert fixture["requirements"] == entry["requirements"], (
            "Case requirements differ from manifest"
        )
        assert len(set(fixture["requirements"])) == len(fixture["requirements"]), (
            "Duplicate case requirements"
        )
        assert len(fixture["requests"]) == len(fixture["expected"]) == entry["requests"]
        current = any(
            request.get("source", {}).get("profile") == "band-contract/file-source/1"
            for request in fixture["requests"]
        )
        declared_profile = fixture.get("schema_profile", 2 if current else 1)
        assert type(declared_profile) is int and declared_profile in (1, 2, 3, 4, 5, 6)
        assert not current or declared_profile >= 2, (
            "Current source cannot use the compatibility schema"
        )
        case_validators = (
            validators
            if declared_profile == 1
            else current_validators[declared_profile]
        )

        def validate_case(
            value: Any,
            name: str,
            registry: dict[str, jsonschema.Draft202012Validator] = case_validators,
        ) -> None:
            registry[name].validate(value)

        source_schemas = {}
        for declared in fixture.get("source_schema_assertions", []):
            assert (
                set(declared) == {"request", "valid"}
                and type(declared["request"]) is int
                and type(declared["valid"]) is bool
            )
            i = declared["request"]
            assert (
                0 <= i < len(fixture["requests"]) and "source" in fixture["requests"][i]
            )
            assert i not in source_schemas, "Duplicate source schema oracle"
            source_schemas[i] = declared["valid"]
        for request_index, (request, expected) in enumerate(
            zip(fixture["requests"], fixture["expected"])
        ):
            op = request["op"]
            if "source" in request:
                assert case_validators["source"].is_valid(
                    request["source"]
                ) == source_schemas.get(request_index, True), (
                    "Source schema validity differs from its explicit oracle"
                )
            if op == "compile" and "ok" in expected:
                validate_case(expected["ok"], "compiled")
            if op == "compile" and isinstance(expected.get("error"), dict):
                validate_case(expected["error"], "compiler_error")
            if op == "query" and "ok" in expected:
                validate_case(expected["ok"], QUERY_SCHEMAS[request["selector"]])
            if op in ("new", "restore") and "ok" in expected:
                validate_case(expected["ok"], "checkpoint_data")
            if op in ("step", "tick") or (
                op == "wire_step" and "error" not in expected
            ):
                validate_case(expected["state"], "checkpoint_data")
                if op != "tick":
                    validate_case(expected["decision"], "decision")
            if op == "room_new":
                validate_case(request["config"], "room_config")
            if op == "room_step":
                validate_case(expected["decision"], "room_decision")
            if op == "view" and "ok" in expected:
                validate_case(expected["ok"], "projection")
            if op == "checkpoint" and "ok" in expected:
                validate_case(expected["ok"], "room_checkpoint")
        for assertion in fixture["assertions"]:
            assert (
                pointer(
                    fixture["expected"][assertion["response"]], assertion["pointer"]
                )
                == assertion["equals"]
            )
        fixtures.append(fixture)
    assert bandexamples is not None, "Missing Band wire example artifact"
    # Original wire examples remain pinned schema-only evidence. Their retired
    # normalized-source create is not an executable current-profile admission case.
    assert current_band_examples is not None, (
        "Missing current Band wire example artifact"
    )
    current_create = next(
        item["value"] for item in current_band_examples if item["id"] == "create"
    )
    current_case = next(
        fixture for fixture in fixtures if fixture["id"] == "v2-band-session"
    )
    assert current_create["source"] == current_case["requests"][0]["source"], (
        "Current Band source examples differ"
    )
    # The host states the capabilities the compiled card declares; a create request never carries them.
    extensions = current_case["expected"][0]["ok"]["card"]["extensions"]
    assert {
        "session": current_create["id"],
        "now": 0,
        **current_create["config"],
        **({"capabilities": extensions} if extensions else {}),
        "room_policy": trusted_policy(current_create["config"]["bindings"]),
    } == current_case["requests"][1]["config"], "Current Band session examples differ"
    coverage_index = json.loads((ROOT / "spec/conformance/coverage.json").read_text())
    v2_validators["coverage_index"].validate(coverage_index)
    assert coverage_index == coverage(requirements, manifest), (
        "Regenerate the coverage index"
    )
    parity = check_parity(ROOT)
    selected = [
        fixture
        for fixture in fixtures
        if not args.case_prefix or fixture["id"].startswith(args.case_prefix)
    ]
    assert selected, "No cases match the selected prefix"
    expected = [response for fixture in fixtures for response in fixture["expected"]]
    selected_expected = [
        response for fixture in selected for response in fixture["expected"]
    ]
    if not args.schema_only:
        requests = [request for fixture in selected for request in fixture["requests"]]
        observed = adapter(requests, configured)
        for index, (request, actual, wanted) in enumerate(
            zip(requests, observed, selected_expected)
        ):
            assert comparison_bytes(request, actual) == comparison_bytes(
                request, wanted
            ), f"adapter response {index} differs from its immutable fixture"
    report = {
        "revision": requirements["revision"],
        "requirements": len(identifiers),
        "schema_definitions": len(schema["$defs"]),
        "cases": len(fixtures),
        "current_schema_definitions": len(v2_schema["$defs"]),
        "assertions": sum(len(fixture["assertions"]) for fixture in fixtures),
        "generated_artifacts_checked": True,
        "assertion_parity": parity,
        "coverage": coverage_index["summary"],
        "qualification": "not_assessed",
        "requests": len(expected),
        "adapter_checked": not args.schema_only,
        "invocation_replays": invocation_count,
        "adapter_scope": args.case_prefix or "full",
        "adapter_cases": 0 if args.schema_only else len(selected),
        "adapter_requests": 0 if args.schema_only else len(selected_expected),
        "adapter": None
        if args.schema_only
        else {
            "kind": "reference" if configured is None else "configured",
            "argv": adapter_argv(configured),
        },
        "adapter_command": None if args.schema_only else adapter_command(configured),
        "band_wire_examples": len(bandexamples),
        "band_live_checked": False,
        "current_band_wire_examples": len(current_band_examples),
        "provider_fixture_checked": not args.schema_only,
        "command": [sys.executable, *sys.argv],
        "exit_code": 0,
        "passed": True,
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    sys.stdout.write(str(json.dumps(report)) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logger.debug("Specification check failed", exc_info=True)
        sys.stderr.write(str(str(error)) + "\n")
        raise SystemExit(1)
