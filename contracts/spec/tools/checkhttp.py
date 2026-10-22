"""Validate real standalone HTTP wire responses against the portable schemas."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import jsonschema

from contracts.spec.tools.buildrepliesschemas import root as current_schema
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT
from remote_agent.authority import DurableRoom
from remote_agent.network import HTTPRoom, HTTPService, RoomAPI
from remote_agent.store import Store
from remote_agent.transport import EngineProcess, LocalGate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    schema = json.loads(
        (ROOT / "spec/schemas/protocol-replies.schema.json").read_text()
    )
    assert schema == current_schema, "Regenerate the current HTTP protocol schema"
    checks = []

    def check(name: str, value: Any) -> Any:
        jsonschema.Draft202012Validator(
            dict(schema, **{"$ref": "#/$defs/" + name})
        ).validate(value)
        checks.append(name)
        return value

    with (
        tempfile.TemporaryDirectory(prefix="contract-http-conformance-") as temp,
        Store(Path(temp) / "room.sqlite3") as store,
    ):
        engine = EngineProcess(ROOT / "contract-lab/contracts/hybrid-assistant.band")
        try:
            room = DurableRoom(
                store,
                engine,
                session="s",
                bindings={"requester": "user", "assistant": "agent"},
                clock=lambda: 0,
            )
            service = HTTPService(
                RoomAPI(room),
                {
                    "u" * 32: {"actor": "user", "permissions": ["room"]},
                    "a" * 32: {"actor": "agent", "permissions": ["room"]},
                },
            )
            try:
                user, agent = (
                    HTTPRoom(service.url, "u" * 32),
                    HTTPRoom(service.url, "a" * 32, require_inbox=True),
                )
                check("http_identity", user._request("GET", "/v1/identity"))

                def projection(client: HTTPRoom, actor: str) -> dict[str, Any]:
                    # Validate actual wire objects before the runner hydrates them.
                    definition = check(
                        "http_definition", client._request("GET", "/v1/definition")
                    )
                    compact = check(
                        "http_compact_projection",
                        client._request("POST", "/v1/view", {}),
                    )
                    assert (
                        definition["actor"],
                        definition["session"],
                        definition["identity"],
                    ) == (actor, client.session, client.identity)
                    full = check("projection", client.view(actor))
                    expected = copy.deepcopy(compact)
                    expected["view"]["definition"] = definition["definition"]
                    assert full == expected, (
                        "Runner hydration changed current-view authority"
                    )
                    return compact

                compact = projection(user, "user")
                question = {
                    "id": "q",
                    "session": "s",
                    "kind": "question",
                    "payload": {"text": "ping"},
                    "expected_version": 0,
                }
                checker = EngineProcess(
                    ROOT / "contract-lab/contracts/hybrid-assistant.band"
                )
                try:
                    local = check(
                        "decision",
                        LocalGate(checker).check(compact, question, "user", 0),
                    )
                    assert local["status"] == "accepted", (
                        "Compact preflight must remain valid for LocalGate"
                    )
                finally:
                    checker.close()
                accepted = check("decision", user.publish(question, "user"))
                assert accepted["status"] == "accepted"
                assert check("decision", user.lookup(question, "user", 0)) == {
                    "status": "duplicate",
                    "original": accepted,
                }
                inbox = check("standalone_inbox_response", agent.inbox(0))
                assert [item["id"] for item in inbox["events"]] == ["q"]
                projection(agent, "agent")
                rejected = check(
                    "decision",
                    agent.publish(
                        dict(question, id="wrong", expected_version=1), "agent"
                    ),
                )
                assert rejected["status"] == "rejected"
                answer = {
                    "id": "answer",
                    "session": "s",
                    "kind": "answer",
                    "payload": {"request": "q", "text": "pong"},
                    "expected_version": 1,
                }
                assert (
                    check("decision", agent.publish(answer, "agent"))["status"]
                    == "accepted"
                )
                assert [
                    item["id"]
                    for item in check("standalone_inbox_response", user.inbox(0))[
                        "events"
                    ]
                ] == ["answer"]
            finally:
                service.close()
        finally:
            engine.close()
    report = {
        "profile": "contract-room/http/1",
        "checks": checks,
        "passed": True,
        "exit_code": 0,
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    sys.stdout.write(str(json.dumps(report)) + "\n")


if __name__ == "__main__":
    main()
