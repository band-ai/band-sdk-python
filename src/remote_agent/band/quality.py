"""Host-owned semantic examples for any pinned contract role, outside candidate authority."""

from __future__ import annotations

import re

from remote_agent.invocations import InvocationJournal
from remote_agent.program import Artifact, Program
from remote_agent.protocol import decode
from remote_agent.storage import canonical, digest, private_directory
from remote_agent.store import Store
from remote_agent.types import JsonObject, SourcePath


def checked_policy(
    value: JsonObject, *, role: str | None, contract_sha256: str | None
) -> JsonObject:
    try:
        encoded = canonical(value)
        if len(encoded) > 524_288:
            raise ValueError("capacity")
        value = decode(encoded.decode("utf-8"))
    except Exception as error:
        raise ValueError("quality_policy_invalid_json") from error
    fields = {
        "profile",
        "source_sha256",
        "role",
        "state_version",
        "initial",
        "modes",
        "groups",
        "cases",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value["profile"] != "band-contract/program-quality/1"
    ):
        raise ValueError("quality_policy_profile")
    if (
        value["role"] != role
        or value["source_sha256"] != contract_sha256
        or not isinstance(role, str)
        or not role
        or not isinstance(contract_sha256, str)
        or not re.fullmatch("[0-9a-f]{64}", contract_sha256)
    ):
        raise ValueError("quality_scope_mismatch")
    if (
        not isinstance(value["state_version"], str)
        or not 1 <= len(value["state_version"]) <= 100
        or not isinstance(value["initial"], dict)
        or len(canonical(value["initial"])) > 16_384
        or not isinstance(value["modes"], list)
        or not value["modes"]
        or any(mode not in ("full", "partial") for mode in value["modes"])
    ):
        raise ValueError("quality_policy_state_or_coverage")
    groups = value["groups"]
    if (
        not isinstance(groups, list)
        or not 1 <= len(groups) <= 32
        or any(not isinstance(group, str) or not group for group in groups)
        or len(set(groups)) != len(groups)
    ):
        raise ValueError("quality_policy_groups")
    cases = value["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 128:
        raise ValueError("quality_policy_cases")
    names, described = set(), set()
    for case in cases:
        if (
            not isinstance(case, dict)
            or set(case)
            != {"name", "group", "event", "state", "view", "expected", "allow_delegate"}
            or not isinstance(case["name"], str)
            or not 1 <= len(case["name"]) <= 200
            or case["name"] in names
            or type(case["allow_delegate"]) is not bool
            or any(
                not isinstance(case[key], dict)
                for key in ("event", "state", "view", "expected")
            )
        ):
            raise ValueError("quality_policy_case")
        names.add(case["name"])
        expected = case["expected"]
        if case["group"] is not None:
            if (
                not isinstance(case["group"], str)
                or case["group"] not in groups
                or expected.get("type") != "handled"
            ):
                raise ValueError("quality_policy_case_group")
            described.add(case["group"])
        if expected.get("type") == "delegate":
            valid = set(expected) == {"type"}
        else:
            message = expected.get("message")
            valid = (
                expected.get("type") == "handled"
                and set(expected) == {"type", "state", "message"}
                and isinstance(expected["state"], dict)
                and (
                    message is None
                    or isinstance(message, dict)
                    and set(message) == {"kind", "payload"}
                    and isinstance(message["kind"], str)
                    and bool(message["kind"])
                    and isinstance(message["payload"], dict)
                )
            )
        if not valid:
            raise ValueError("quality_policy_expected_result")
    if described != set(groups):
        raise ValueError("quality_policy_missing_group")
    return value


def qualify_candidate(
    source: str,
    workspace: SourcePath,
    *,
    qualification: JsonObject,
    role: str | None,
    contract_sha256: str | None,
    durable: bool = True,
) -> JsonObject:
    policy = checked_policy(qualification, role=role, contract_sha256=contract_sha256)
    workspace = private_directory(workspace)
    artifact = Artifact.publish(source, workspace / "artifacts")
    covered, checks = set(), []
    with Store(workspace / "validation.sqlite3", durable=durable) as journal:
        program = Program(artifact, journal=InvocationJournal(journal))
        metadata = program.describe({})
        if metadata["schema"] != policy["state_version"] or canonical(
            metadata["initial"]
        ) != canonical(policy["initial"]):
            raise ValueError("semantic_state_schema")
        mode = metadata["coverage"]["mode"]
        if mode not in policy["modes"]:
            raise ValueError("semantic_coverage")
        for case in policy["cases"]:
            try:
                actual = program.handle(case["event"], case["state"], case["view"])
            except Exception as error:
                raise ValueError("semantic_callback_failed") from error
            expected = case["expected"]
            if actual["type"] == "delegate":
                if expected["type"] != "delegate" and not (
                    mode == "partial" and case["allow_delegate"]
                ):
                    raise ValueError("semantic_case_requires_handling")
            elif expected["type"] != "handled" or canonical(
                {key: actual[key] for key in ("type", "state", "message")}
            ) != canonical(expected):
                raise ValueError("semantic_case_mismatch")
            elif case["group"] is not None:
                covered.add(case["group"])
            checks.append(case["name"])
    if not covered or mode == "full" and covered != set(policy["groups"]):
        raise ValueError("semantic_incomplete_coverage")
    return {
        "ok": True,
        "checks": checks,
        "program_sha256": artifact.sha256,
        "coverage": metadata["coverage"],
        "quality_sha256": digest(policy),
    }
