"""M22 independent compiler and typed-roster safety oracles."""

from __future__ import annotations

from typing import Any

from contracts.spec.tools.vectorinputs import file_source, trusted_policy
from remote_agent.protocol import canonical


def cases() -> list[dict[str, Any]]:
    rows = []
    header = "---\nname: safety-oracle\ndescription: Portable compiler safety checks.\nengine: 0.2.0-lab.1\nroles: [requester, assistant]\n---\n"

    def assertion(i: int, path: str, value: Any) -> dict[str, Any]:
        return {"response": i, "pointer": path, "equals": value}

    def compile(body: str) -> dict[str, Any]:
        return {"op": "compile", "handle": "c", "source": file_source(header + body)}

    def add(
        name: str,
        requests: list[dict[str, Any]],
        assertions: list[dict[str, Any]],
        **fields: Any,
    ) -> None:
        rows.append(
            {
                "profile": "band-contract/conformance-case/1",
                "id": "v2-m22-" + name,
                "requirements": ["DSL-004", "DSL-006", "ENG-001"],
                "requests": requests,
                "assertions": assertions,
                **fields,
            }
        )

    forms = [
        ("self", "state new\n requester -> assistant: question() => new", 5),
        # A form that starts with a field line extends `type answer`: a reply alternative's type
        # names its head (contract-dsl.md, Reply alternatives), and its wait lies on a cycle.
        (
            "next",
            " question: ref(question)\nstate new\n requester -> assistant: question() awaits answering\n  => answer() & new\n   | @1m & complete\nstate answering max_entries 1 => complete",
            7,
        ),
        (
            "any-state",
            " question: ref(question)\ntype restart\nstate new\n requester -> assistant: question() awaits answering\n  => answer() & complete\n   | @1m & complete\nstate answering max_entries 1 => complete\nanystate\n requester -> assistant: restart() => new",
            12,
        ),
        (
            "each",
            "state new\n {requester,assistant} -> requester: question() each => new",
            5,
        ),
        (
            "deadline",
            "state new\n requester -> assistant: question() @1m => complete | new",
            5,
        ),
        (
            "limit",
            "state new max_entries 1 => new\n requester -> assistant: question() => complete",
            4,
        ),
    ]
    for name, body, line in forms:
        body = (
            "contract @infinity => expired\ntype question\ntype answer\n"
            + body
            + "\nend complete\n"
        )
        add(
            "entry-" + name,
            [compile(body)],
            [
                assertion(0, "/error/code", "initial_state_target"),
                assertion(0, "/error/line", line),
            ],
        )
    body = "contract @infinity => expired\ntype question\n text: text(100, regex: ~r/(?=a)a/)\nstate new\n requester -> assistant: question() => complete\nend complete\n"
    # Portable conformance compares code and line (contract-dsl.md): the invalid field's line.
    add(
        "pattern-syntax",
        [compile(body)],
        [assertion(0, "/error/code", "pattern_syntax"), assertion(0, "/error/line", 3)],
    )
    body = (
        "contract @infinity => expired\ntype question\n"
        + "".join(f" f{i}: text(100000, regex: ~r/a*/)\n" for i in range(3))
        + "state new\n requester -> assistant: question() => complete\nend complete\n"
    )
    # The per-message-type pattern sum reports the type line.
    add(
        "pattern-cost",
        [compile(body)],
        [assertion(0, "/error/code", "pattern_limit"), assertion(0, "/error/line", 2)],
    )
    typed = """---
name: attribute-boundary
description: Participant attributes have their own canonical byte cap.
engine: 0.2.0-lab.1
roles: [lead, reviewers]
---
contract @infinity => expired
type reviewer: agent
 label: text(20000)
reviewers: list(reviewer)
type request
type reply
state new
 lead -> reviewers: request() => waiting
state waiting
 reviewers -> lead: reply() each => complete
end complete
"""
    requests = [{"op": "compile", "handle": "c", "source": file_source(typed)}]
    overhead = len(canonical({"label": ""}))
    for size in (16384, 16385):
        attributes = {"label": "a" * (size - overhead)}
        assert len(canonical(attributes)) == size
        bindings = {
            "lead": "l",
            "reviewers": [{"name": "one", "actor": "a", "attributes": attributes}],
        }
        requests.append(
            {
                "op": "new",
                "contract": "c",
                "handle": "s",
                "config": {
                    "session": "s",
                    "now": 0,
                    "max_messages": 10,
                    "bindings": bindings,
                    "room_policy": trusted_policy(bindings),
                },
            }
        )
    add(
        "attributes-bytes",
        requests,
        [
            assertion(1, "/ok/profile", "band-contract/state/4"),
            assertion(2, "/error", "invalid_bindings"),
        ],
        schema_profile=4,
    )
    return rows
