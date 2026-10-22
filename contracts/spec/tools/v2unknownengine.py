"""Portable V2-FAIL-CLOSED trace: an unknown engine identifier is refused with the
compiler's `engine` code before any other header field is checked."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any

import jsonschema

from contracts.spec.tools.buildv2schemas import root as SCHEMA
from contracts.spec.tools.generatevectors import adapter, pointer
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT

PATH = ROOT / "spec/conformance/v2-unknown-engine.json"
CASE = "v2-unknown-engine"
BODY = """contract @infinity => expired
type task
state new
  lead -> worker: task() => done
end done
"""
# Identifiers this reference does not implement: a later version, a sibling lab
# release, an unpinned word and a near-miss of the supported 0.2.0-lab.1.
UNKNOWN = ["9.9.9", "0.3.0-lab.1", "latest", "0.2.0-lab.2"]


def frontmatter(engine: str, name: str = "probe") -> str:
    return (
        f"---\nname: {name}\ndescription: Probe an unknown engine.\nengine: {engine}\n"
        f"roles: [lead, worker]\n---\n" + BODY
    )


def native(engine: str) -> str:
    return (
        f"+SKILL probe\n+SUMMARY Probe an unknown engine.\n+ENGINE {engine}\n"
        f"+ROLES lead, worker\n+BODY\n" + BODY
    )


def source(content: str, fmt: str = "frontmatter") -> dict[str, Any]:
    return {
        "profile": "band-contract/file-source/1",
        "format": fmt,
        "content": content,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


def requests() -> list[dict[str, Any]]:
    """Each request compiles alone; reuse any of them as a standalone negative."""
    sources = [source(frontmatter(engine)) for engine in UNKNOWN]
    # The engine is reported even when other header fields are also invalid.
    sources.append(source(frontmatter("9.9.9", name="Bad Name")))
    sources.append(source(native("9.9.9"), "native"))
    return [{"op": "compile", "handle": "c", "source": s} for s in sources]


def verify(observed: list[dict[str, Any]]) -> bool:
    """Independent oracle: never derived from the fixture's editable assertions.

    Native is retired (X4 wave 2, forward-only; Ada's set N decision B1, 2026-10-14): the native
    request is refused with the loader error frontmatter_required before any engine check.
    """
    sent = requests()
    assert len(observed) == len(sent), "unknown-engine trace response count differs"
    for index, (request, response) in enumerate(zip(sent, observed)):
        assert "ok" not in response, f"request {index}: an unknown engine compiled"
        error = response.get("error")
        if request["source"]["format"] == "native":
            assert error == "frontmatter_required", (
                f"request {index}: native is retired and must fail with frontmatter_required, got {error!r}"
            )
            continue
        assert isinstance(error, dict) and error.get("code") == "engine", (
            f"request {index}: an unknown engine must fail with code engine, got {error!r}"
        )
        assert error.get("line") == 1, (
            f"request {index}: the engine error is reported at line 1"
        )
    return True


def assertions() -> list[dict[str, Any]]:
    """Independently stated expectations, one set per request (Sofia F2): the retired native
    request fails with the bare loader code, every other request with `engine` at line 1."""
    stated = []
    for index, request in enumerate(requests()):
        if request["source"]["format"] == "native":
            stated.append(
                {
                    "response": index,
                    "pointer": "/error",
                    "equals": "frontmatter_required",
                }
            )
        else:
            stated += [
                {"response": index, "pointer": "/error/code", "equals": "engine"},
                {"response": index, "pointer": "/error/line", "equals": 1},
            ]
    return stated


def schema_assertions() -> list[dict[str, Any]]:
    """Each request that deliberately violates the current source schema, declared as
    conformance/README.md requires (Sofia set P P-L5): the retired native request."""
    return [
        {"request": index, "valid": False}
        for index, request in enumerate(requests())
        if request["source"]["format"] == "native"
    ]


def check(fixture: dict[str, Any], observed: list[dict[str, Any]]) -> None:
    assert fixture["profile"] == "band-contract/conformance-case/2"
    assert fixture["id"] == CASE
    assert fixture["requests"] == requests(), "unknown-engine requests changed"
    verify(fixture["expected"])
    verify(observed)
    assert fixture.get("source_schema_assertions") == schema_assertions(), (
        "source_schema_assertions must declare exactly the schema-invalid requests"
    )
    source_schema = jsonschema.Draft202012Validator(
        {**SCHEMA, "$ref": "#/$defs/source"}
    )
    declared = {
        row["request"]: row["valid"] for row in fixture["source_schema_assertions"]
    }
    for index, request in enumerate(fixture["requests"]):
        assert source_schema.is_valid(request["source"]) == declared.get(index, True), (
            f"source_schema_assertions differ from the schema at request {index}"
        )
    # The fixture's own assertions hold against its expected and the observed responses.
    for item in fixture["assertions"]:
        for responses in (fixture["expected"], observed):
            actual = pointer(responses[item["response"]], item["pointer"])
            assert actual == item["equals"], (
                f"assertion {item} does not hold: got {actual!r}"
            )
    assert observed == fixture["expected"], "unknown-engine expected responses differ"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    observed = adapter(requests())
    verify(observed)
    fixture = {
        "profile": "band-contract/conformance-case/2",
        "id": CASE,
        "requirements": ["V2-FAIL-CLOSED"],
        "requests": requests(),
        "expected": observed,
        "assertions": assertions(),
        "source_schema_assertions": schema_assertions(),
    }
    if args.write:
        PATH.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
    else:
        check(json.loads(PATH.read_text()), observed)
    sys.stdout.write(
        str(json.dumps({"case": CASE, "requests": len(observed), "passed": True}))
        + "\n"
    )


if __name__ == "__main__":
    main()
