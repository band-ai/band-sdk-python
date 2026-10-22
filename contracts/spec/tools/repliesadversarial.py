"""Generate independent reply-alternative inputs; run real compiled lab modules.

No reference outputs are blessed here. Each semantic expectation is authored
from DESIGN v2 plus COVERAGE's U/sugar and core-syntax decisions. Exit 1 means
a red vector or harness/build input failure. Build the selected repos first.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from contracts.spec.tools.adapterdriver import run
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT

FIXTURES = ROOT / "spec/conformance/replies-adversarial"
PROBE = FIXTURES / "replies_adversarial.exs"


def portable_evidence(value: Any) -> Any:
    """Keep observations intact; render checkout paths relative and home paths with ~."""
    if isinstance(value, dict):
        return {
            portable_evidence(key): portable_evidence(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [portable_evidence(item) for item in value]
    if isinstance(value, str):
        if value == str(ROOT):
            return "."
        if value == str(Path.home()):
            return "~"
        return value.replace(str(ROOT) + "/", "").replace(str(Path.home()) + "/", "~/")
    return value


def evidence_json(value: Any, **options: Any) -> str:
    return json.dumps(portable_evidence(value), **options)


def source(name: str, body: str) -> str:
    return (
        f"---\nname: {name}\ndescription: Local adversarial reply conformance.\n"
        "engine: 0.2.0-lab.1\nroles: [lead, worker]\n---\n" + body
    )


def simple(rows: list[str], fields: str = "  x: boolean\n  y: boolean\n") -> str:
    return (
        "contract @30m => expired\ntype request\ntype answer\n  request: ref(request)\n"
        + fields
        + "state new\n  lead -> worker: request() as req awaits waiting\n"
        + "".join(
            f"{'    =>' if i == 0 else '     |'} answer(request=$req{', ' if row else ''}{row}) & done\n"
            for i, row in enumerate(rows)
        )
        + "     | @10m & stalled\nend done\nend stalled\n"
    )


def step(
    actor: str, ident: str, kind: str, payload: dict[str, Any], now: int, **expect: Any
) -> dict[str, Any]:
    return {
        "actor": actor,
        "now": now,
        "message": {"id": ident, "session": "s", "kind": kind, "payload": payload},
        "expect": expect,
    }


def scenario(steps: list[dict[str, Any]], budget: int = 30) -> dict[str, Any]:
    return {
        "bindings": {"lead": "l", "worker": "w"},
        "session": "s",
        "now": 0,
        "max_messages": budget,
        "steps": steps,
    }


def vectors() -> list[tuple[dict[str, Any], str, dict[str, Any] | None]]:
    result = []

    def add(
        ident: str,
        body: str,
        code: str | None = None,
        replay: dict[str, Any] | None = None,
        rationale: str = "",
        correlation_binding: str = "req",
        line: int | None = None,
    ) -> None:
        text = source(ident, body)
        item: dict[str, Any] = {
            "id": ident,
            "source": {
                "profile": "band-contract/file-source/1",
                "format": "frontmatter",
                "content": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            },
            "rationale": rationale,
            "compile": {"code": code} if code else {"ok": True, "auto_extension": True},
        }
        body_lines = body.splitlines()
        if code == "reply_overlap":
            item["compile"]["line"] = [
                i + 1
                for i, row in enumerate(body_lines)
                if row.strip().startswith(("=> ", "| answer("))
            ][1]
        elif code == "reply_not_exhaustive":
            item["compile"]["line"] = next(
                i + 1 for i, row in enumerate(body_lines) if " awaits waiting" in row
            )
        if line is not None:
            item["compile"]["line"] = line
        if replay:
            item["scenario"] = replay
            if any(
                entry["expect"].get("reason") == "reference"
                for entry in replay["steps"]
            ):
                item["correlation_binding"] = correlation_binding
        result.append((item, text, replay))

    add(
        "overlap-useful-exhaustive",
        simple(["x=true", "y=true", "x=false, y=false"]),
        "reply_overlap",
        rationale="All rows useful and total, but true/true matches rows 1 and 2.",
    )
    add("overlap-bare-fallback", simple(["x=true", ""]), "reply_overlap")
    add(
        "coverage-false-false-gap",
        simple(["x=true", "x=false, y=true"]),
        "reply_not_exhaustive",
    )
    add(
        "disjoint-total-control",
        simple(["x=true", "x=false"]),
        replay=scenario(
            [
                step(
                    "l",
                    "req",
                    "request",
                    {},
                    0,
                    status="accepted",
                    state="waiting",
                    version=1,
                ),
                step(
                    "w",
                    "ans",
                    "answer",
                    {"request": "req", "x": False, "y": False},
                    1,
                    status="accepted",
                    outcome="done",
                    version=2,
                ),
            ]
        ),
    )

    fields = "".join(f"  b{i}: boolean\n" for i in range(64))
    staggered = [", ".join(f"b{i * 4 + j}=true" for j in range(4)) for i in range(16)]
    add(
        "s4-16x4-staggered-booleans",
        simple(staggered, fields),
        "reply_overlap",
        rationale="64 boolean columns: reject overlap before coverage, never enumerate 2^64 payloads.",
    )
    # The enum separates rows before counting, so this actually exercises the
    # non-overlapping coverage path over 16 * 2^48 assignments.
    tags = "|".join(f"v{i}" for i in range(16))
    fields = f"  tag: enum({tags})\n" + "".join(f"  b{i}: boolean\n" for i in range(48))
    rows = [
        ", ".join([f"tag=v{i}"] + [f"b{i * 3 + j}=true" for j in range(3)])
        for i in range(16)
    ]
    add(
        "s4-disjoint-large-domain",
        simple(rows, fields),
        "reply_not_exhaustive",
        rationale="Disjoint 16 rows x 4 literals; union is 1/8 of the domain, count with big integers.",
    )
    bigint_fields = f"  tag: enum({tags})\n" + "".join(
        f"  e{i}: enum({tags})\n" for i in range(48)
    )
    bigint_rows = [
        ", ".join([f"tag=v{i}"] + [f"e{i * 3 + j}=v0" for j in range(3)])
        for i in range(16)
    ]
    add(
        "s4-disjoint-bigint-domain",
        simple(bigint_rows, bigint_fields),
        "reply_not_exhaustive",
        rationale="Disjoint rows over 16^49 = 2^196 assignments; requires symbolic big-integer count, not enumeration or float equality.",
    )
    fields = "".join(f"  b{i}: boolean\n" for i in range(4))
    total_rows = [
        ", ".join(f"b{i}={str(v).lower()}" for i, v in enumerate(bits))
        for bits in itertools.product([False, True], repeat=4)
    ]
    add("s4-16x4-total-boundary", simple(total_rows, fields))
    add("limit-17-rows", simple(total_rows + [""], fields), "reply_limit")
    add(
        "limit-5-literals",
        simple(
            ["b0=true, b1=true, b2=true, b3=true, b4=true"], fields + "  b4: boolean\n"
        ),
        "reply_limit",
    )
    many = (
        "contract @30m => expired\ntype answer\n  request: ref("
        + "|".join(f"request{i}" for i in range(17))
        + ")\n"
        + fields
    )
    many += "".join(f"type request{i}\n" for i in range(17))
    many += "state new\n"
    for i in range(17):
        many += f"  lead -> worker: request{i}() as req awaits wait{i}\n"
        many += "".join(
            f"{'    =>' if j == 0 else '     |'} answer(request=$req, {r}) & done\n"
            for j, r in enumerate(total_rows)
        )
        many += "     | @10m & stalled\n"
    add("limit-contract-272-rows", many + "end done\nend stalled\n", "reply_limit")
    add(
        "unknown-enum-literal",
        simple(["value=felix_unknown_literal_6b7f5f"], "  value: enum(ok|error)\n"),
        "reply_match",
    )
    add(
        "unknown-boolean-literal",
        simple(["x=felix_unknown_boolean_6b7f5f"]),
        "reply_match",
    )

    cycle = (
        "contract @30m => expired\ntype request\ntype answer\n"
        "  request: ref(request)\n  choice: enum(approve|revise)\n"
        "state new\n  lead -> worker: request() as req awaits waiting\n"
        "    => answer(request=$req, choice=approve) & done\n"
        "     | answer(request=$req, choice=revise) & revising\n"
        "     | @10m & stalled\n"
        "state waiting max_entries 3 => exhausted\n"
        "state revising\n  lead -> worker: request() as req => waiting\n"
        "end done\nend stalled\nend exhausted\n"
    )
    add(
        "c1-late-reply-reentered-wait",
        cycle,
        replay=scenario(
            [
                step(
                    "l",
                    "req1",
                    "request",
                    {},
                    0,
                    status="accepted",
                    state="waiting",
                    version=1,
                ),
                step(
                    "w",
                    "revise1",
                    "answer",
                    {"request": "req1", "choice": "revise"},
                    10,
                    status="accepted",
                    state="revising",
                    version=2,
                ),
                step(
                    "w",
                    "late-before-reentry",
                    "answer",
                    {"request": "req1", "choice": "approve"},
                    20,
                    status="rejected",
                    reason="illegal_message",
                    state="revising",
                    version=2,
                ),
                step(
                    "l",
                    "req2",
                    "request",
                    {},
                    30,
                    status="accepted",
                    state="waiting",
                    version=3,
                ),
                step(
                    "w",
                    "late-after-reentry",
                    "answer",
                    {"request": "req1", "choice": "approve"},
                    40,
                    status="rejected",
                    reason="reference",
                    state="waiting",
                    version=3,
                ),
                step(
                    "w",
                    "current-reply",
                    "answer",
                    {"request": "req2", "choice": "approve"},
                    50,
                    status="accepted",
                    outcome="done",
                    version=4,
                ),
            ]
        ),
        rationale="New proposal IDs and fresh version avoid mistaking duplicate/stale_version checks for correlation.",
    )
    add(
        "c1-cycle-no-eligible-ref",
        cycle.replace(" as req", "")
        .replace("request=$req, ", "")
        .replace("  request: ref(request)\n", "  request: text(1..100)\n"),
        "reply_correlation",
        line=8,
        rationale="Missing as/=$ is legal sugar, but a non-ref request field cannot supply correlation.",
    )
    add(
        "c1-cycle-without-max-entries",
        cycle.replace(" max_entries 3 => exhausted", ""),
        "reply_cycle",
    )
    add(
        "c1-rebind-state-event",
        cycle.replace(
            "state revising\n",
            "  event lead -> worker: request() as req\nstate revising\n",
        ),
        "reply_correlation",
    )
    add(
        "c1-rebind-anystate-event",
        cycle + "anystate\n  event lead -> worker: request() as req\n",
        "reply_correlation",
    )
    acyclic = (
        "contract @30m => expired\ntype request\ntype cancel\ntype answer\n  request: ref(request)\n"
        "state new\n  lead -> worker: request() as req awaits w1\n"
        "    => answer(request=$req) & first_done\n     | @10m & stalled\n"
        "state w1\n  lead -> worker: cancel() => between\n"
        "state between\n  lead -> worker: request() as req awaits w2\n"
        "    => answer(request=$req) & second_done\n     | @10m & stalled\n"
        "end first_done\nend second_done\nend stalled\n"
    )
    cross_wait = scenario(
        [
            step("l", "q1", "request", {}, 0, status="accepted", state="w1", version=1),
            step(
                "l",
                "cancel1",
                "cancel",
                {},
                10,
                status="accepted",
                state="between",
                version=2,
            ),
            step(
                "l", "q2", "request", {}, 20, status="accepted", state="w2", version=3
            ),
            step(
                "w",
                "r1-first-arrival",
                "answer",
                {"request": "q1"},
                30,
                status="rejected",
                reason="reference",
                state="w2",
                version=3,
            ),
            step(
                "w",
                "r2",
                "answer",
                {"request": "q2"},
                40,
                status="accepted",
                outcome="second_done",
                version=4,
            ),
        ]
    )
    add(
        "c1-acyclic-cross-wait",
        acyclic,
        replay=cross_wait,
        rationale="Victor's acyclic W1→cancel→W2 witness: same role/kind, first-arriving old reply must fail reference.",
    )
    implicit_acyclic = acyclic.replace(" as req", "").replace("request=$req", "")
    add(
        "sugar-stale-implicit-binding",
        implicit_acyclic,
        replay=cross_wait,
        correlation_binding="w2",
        rationale="Implicit w1/w2 head bindings and inferred request refs still reject the first-arriving stale q1 with fresh version 3; q2 then succeeds.",
    )
    add(
        "c1-acyclic-rebind",
        acyclic.replace(
            "end first_done\n",
            "state w2\n  event lead -> worker: request() as req\nend first_done\n",
        ),
        "reply_correlation",
    )
    add(
        "c1-acyclic-implicit-binding",
        acyclic.replace(" as req", "").replace("request=$req", ""),
        rationale="Decision 4: each head implicitly binds its own W and the sole eligible ref is filled.",
    )
    add(
        "c1-acyclic-implicit-reference",
        acyclic.replace("request=$req", ""),
        rationale="Decision 4: explicit as req with the sole eligible ref omitted is legal.",
    )
    add(
        "c1-acyclic-wrong-reference",
        acyclic.replace("request=$req", "request=$other"),
        "reply_correlation",
    )
    add(
        "c1-reply-kind-ordinary-rule",
        acyclic.replace(
            "state between\n",
            "state between\n  worker -> lead: answer(request=$req) => first_done\n",
        ),
        "reply_correlation",
        rationale="COVERAGE SR3: reply kind cannot be an ordinary accepted message for the same sender role.",
    )
    add(
        "c1-reply-kind-anystate-event",
        acyclic + "anystate\n  event worker -> lead: answer(request=$req)\n",
        "reply_correlation",
    )

    implicit = simple([""]).replace(" as req", "").replace("request=$req", "")
    add(
        "sugar-no-eligible-ref-kind",
        implicit.replace("type request\n", "type request\ntype unrelated\n").replace(
            "request: ref(request)", "request: ref(unrelated)"
        ),
        "reply_correlation",
        line=10,
        rationale="A ref exists but does not include the head kind, so sugar cannot correlate it.",
    )
    ambiguous = (
        simple([""], "  previous: ref(request)\n")
        .replace(" as req", "")
        .replace("request=$req", "")
    )
    add(
        "sugar-ambiguous-ref-fields",
        ambiguous,
        "reply_correlation",
        line=8,
        rationale="Two eligible request refs and no =$ must not silently select the first field.",
    )
    add(
        "sugar-ambiguous-refs-explicit-control",
        ambiguous.replace("answer()", "answer(request=$waiting, previous=$waiting)"),
        rationale="Explicit =$ selects correlation with several eligible fields; every ref field must still be matched.",
    )
    add(
        "sugar-one-eligible-of-several-refs",
        ambiguous.replace("type request\n", "type request\ntype unrelated\n")
        .replace("previous: ref(request)", "previous: ref(unrelated)")
        .replace("answer()", "answer(previous=$waiting)"),
        rationale="Count eligible refs, not all refs: only request refers to the head kind; the other ref is explicitly matched.",
    )
    add(
        "sugar-implicit-wrong-reference",
        implicit.replace("answer()", "answer(request=$other)"),
        "reply_correlation",
        line=9,
    )
    for scope in ("state", "anystate"):
        body = implicit + ("state waiting\n" if scope == "state" else "anystate\n")
        body += "  event lead -> worker: request() as waiting\n"
        add(
            "sugar-rebind-wait-name-" + scope,
            body,
            "reply_correlation",
            line=len(body.splitlines()),
            rationale="W's inferred binding is protected from rebinding locally and in anystate.",
        )

    auto_wait = implicit.replace(" awaits waiting", "")
    add(
        "sugar-auto-wait-positive-control",
        auto_wait,
        replay=scenario(
            [
                step(
                    "l",
                    "auto-req",
                    "request",
                    {},
                    0,
                    status="accepted",
                    state="request_wait",
                    version=1,
                ),
                step(
                    "w",
                    "auto-ans",
                    "answer",
                    {"request": "auto-req", "x": True, "y": False},
                    1,
                    status="accepted",
                    outcome="done",
                    version=2,
                ),
            ]
        ),
    )
    add(
        "sugar-auto-wait-name-clash",
        auto_wait + "state request_wait\n",
        "duplicate",
        rationale="An explicit state must use awaits; it cannot collide with the generated <kind>_wait.",
    )
    add(
        "sugar-auto-wait-two-heads-clash",
        auto_wait.replace(
            "end done\n",
            "state later\n  lead -> worker: request()\n    => answer() & done\n     | @10m & stalled\nend done\n",
        ),
        "reply_route",
        line=12,
        rationale="Lead decision 7: a second block for implied request_wait is reply_route at its later head, not a state-name duplicate.",
    )

    pipe_shape = simple(["x=true", "x=false"])
    add(
        "u-pipe-no-opener",
        pipe_shape.replace("    => answer(", "     | answer(", 1),
        "reply_timeout",
        rationale="Lead decision 7: a line-start | without the => opener is a block-shape error.",
    )
    add(
        "u-double-opener",
        pipe_shape.replace("     | answer(", "    => answer(", 1),
        "reply_timeout",
        rationale="Lead decision 7: a second => opener is a block-shape error, not another reply row.",
    )
    add(
        "u-two-alternatives-one-line",
        pipe_shape.replace(" & done\n     | answer", " & done | answer", 1),
        "syntax",
        line=9,
        rationale="Lead decision 7: U requires one alternative per physical line. Pin the offending row so an old parser's syntax error at the head cannot falsely pass.",
    )

    pipe_body = simple(["x=true", "x=false"]).replace(
        "type request\n", "type request\ntype follow\n"
    )
    pipe_body = pipe_body.replace(
        "answer(request=$req, x=true) & done",
        "answer(request=$req, x=true) & reporting",
    )
    pipe_body += "state reporting\n  worker -> lead: follow() @5m => done | stalled\n"
    for reply_true in (True, False):
        steps = [
            step(
                "l",
                "pipe-req",
                "request",
                {},
                0,
                status="accepted",
                state="waiting",
                version=1,
            ),
            step(
                "w",
                "pipe-ans",
                "answer",
                {"request": "pipe-req", "x": reply_true, "y": False},
                1,
                status="accepted",
                version=2,
                **(
                    {"state": "reporting", "outcome": None}
                    if reply_true
                    else {"outcome": "done"}
                ),
            ),
        ]
        if reply_true:
            steps.append(
                step(
                    "w",
                    "pipe-follow",
                    "follow",
                    {},
                    2,
                    status="accepted",
                    outcome="done",
                    version=3,
                )
            )
        add(
            "u-pipe-reply-vs-timed-rule"
            + ("" if reply_true else "-second-alternative"),
            pipe_body,
            replay=scenario(steps),
            rationale="A line-start | selects a reply row; inline | belongs to a timed rule. Both rows keep their own targets.",
        )
    add(
        "u-pipe-timed-rule-timeout",
        pipe_body,
        replay=scenario(
            [
                step(
                    "l",
                    "pipe-req",
                    "request",
                    {},
                    0,
                    status="accepted",
                    state="waiting",
                    version=1,
                ),
                step(
                    "w",
                    "pipe-ans",
                    "answer",
                    {"request": "pipe-req", "x": True, "y": False},
                    1,
                    status="accepted",
                    state="reporting",
                    version=2,
                ),
                {"tick": 300001, "expect": {"status": "ticked", "outcome": "stalled"}},
            ]
        ),
        rationale="The inline timed-rule | remains its timeout target after parsing a U block.",
    )

    def renewal(follow_minutes: int, reverse: bool = False) -> str:
        rows = [
            "answer(request=$req, long=false) & done\n",
            "answer(request=$req, long=true) & reporting\n",
        ]
        if reverse:
            rows.reverse()
        return (
            "contract @30m max @4h => expired\ntype request\ntype answer\n  request: ref(request)\n  long: boolean\n"
            "type heartbeat\ntype summary\nstate new\n  lead -> worker: request() as req awaits waiting\n"
            + "    => "
            + rows[0]
            + "     | "
            + rows[1]
            + "     | @10m & stalled\nstate waiting\n"
            "  event worker -> lead: heartbeat() => @self\nstate reporting\n"
            f"  worker -> lead: summary() @{follow_minutes}m => done | stalled\nend done\nend stalled\n"
        )

    for reverse in (False, True):
        add(
            "v4-renew-all-targets-" + ("long-first" if reverse else "terminal-first"),
            renewal(20, reverse),
            replay=scenario(
                [
                    step(
                        "l",
                        "req",
                        "request",
                        {},
                        0,
                        status="accepted",
                        state="waiting",
                        version=1,
                    ),
                    step(
                        "w",
                        "hb",
                        "heartbeat",
                        {},
                        300000,
                        status="rejected",
                        reason="insufficient_time",
                        state="waiting",
                        version=1,
                    ),
                    {
                        "tick": 600000,
                        "expect": {"status": "ticked", "outcome": "stalled"},
                    },
                ]
            ),
            rationale="15m renewed due + 20m follow-on cannot fit 30m, regardless of row order; rejected renewal preserves due.",
        )
    add(
        "v4-renew-exact-boundary",
        renewal(15),
        replay=scenario(
            [
                step(
                    "l",
                    "req",
                    "request",
                    {},
                    0,
                    status="accepted",
                    state="waiting",
                    version=1,
                ),
                step(
                    "w",
                    "hb-fit",
                    "heartbeat",
                    {},
                    300000,
                    status="accepted",
                    state="waiting",
                    version=2,
                ),
                step(
                    "w",
                    "hb-over",
                    "heartbeat",
                    {},
                    300001,
                    status="rejected",
                    reason="insufficient_time",
                    version=2,
                ),
                {
                    "tick": 600000,
                    "expect": {"status": "ticked", "outcome": None, "state": "waiting"},
                },
                {"tick": 900000, "expect": {"status": "ticked", "outcome": "stalled"}},
            ]
        ),
    )

    reserve = (
        "contract @30m => expired\ntype request\ntype answer\n  request: ref(request)\n  long: boolean\ntype follow\n"
        "state new\n  lead -> worker: request() as req awaits waiting\n"
        "    => answer(request=$req, long=false) & done\n     | answer(request=$req, long=true) & f1\n"
        "     | @5m & stalled\nstate f1\n  worker -> lead: follow() @5m => f2 | stalled\n"
        "state f2\n  worker -> lead: follow() @5m => f3 | stalled\n"
        "state f3\n  worker -> lead: follow() @5m => done | stalled\nend done\nend stalled\n"
    )
    add(
        "reserve-0-or-3-underflow",
        reserve,
        replay=scenario(
            [
                step(
                    "l",
                    "req",
                    "request",
                    {},
                    0,
                    status="rejected",
                    reason="completion_capacity",
                    state="new",
                    version=0,
                )
            ],
            4,
        ),
        rationale="Entering wait needs head 1 + reply/follow-on reserve 4 = total budget 5.",
    )
    for long in (False, True):
        steps = [
            step(
                "l",
                "req",
                "request",
                {},
                0,
                status="accepted",
                state="waiting",
                version=1,
            ),
            step(
                "w",
                "ans",
                "answer",
                {"request": "req", "long": long},
                1,
                status="accepted",
                version=2,
                **({"state": "f1", "outcome": None} if long else {"outcome": "done"}),
            ),
        ]
        if long:
            for i in range(3):
                steps.append(
                    step(
                        "w",
                        f"follow{i}",
                        "follow",
                        {},
                        i + 2,
                        status="accepted",
                        version=i + 3,
                        **({"outcome": "done"} if i == 2 else {"state": f"f{i + 2}"}),
                    )
                )
        add(
            "reserve-0-or-3-" + ("long" if long else "short"),
            reserve,
            replay=scenario(steps, 5),
        )
    for _, _, replay in result:
        if replay:
            for entry in replay["steps"]:
                if entry["expect"].get("reason") == "reference":
                    entry["message"]["expected_version"] = entry["expect"]["version"]
    return result


def write_vectors() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    suite, portable, table = [], [], []
    for item, text, replay in vectors():
        suite.append(item)
        expected = item["compile"].get("code", "compile OK; auto contract.replies/1")
        if "line" in item["compile"]:
            expected += f" (body line {item['compile']['line']})"
        if replay:
            trace = []
            for entry in replay["steps"]:
                e = entry["expect"]
                trace.append(
                    " / ".join(
                        str(e[k])
                        for k in ("status", "reason", "state", "outcome", "version")
                        if k in e
                    )
                )
            expected += "; " + " → ".join(trace)
        table.append(f"| `{item['id']}` | {expected} |")
        requests = [{"op": "compile", "handle": "c", "source": item["source"]}]
        assertions: list[dict[str, Any]] = (
            [
                {
                    "response": 0,
                    "pointer": "/error/code",
                    "equals": item["compile"]["code"],
                }
            ]
            if "code" in item["compile"]
            else [
                {
                    "response": 0,
                    "pointer": "/ok/card/extensions",
                    "equals": ["contract.replies/1"],
                }
            ]
        )
        if "line" in item["compile"]:
            assertions.append(
                {
                    "response": 0,
                    "pointer": "/error/line",
                    "equals": item["compile"]["line"],
                }
            )
        if replay:
            requests.append(
                {
                    "op": "new",
                    "contract": "c",
                    "handle": "s",
                    "config": {
                        "session": "s",
                        "bindings": replay["bindings"],
                        "now": 0,
                        "max_messages": replay["max_messages"],
                        "room_policy": {"mode": "unchecked"},
                        "capabilities": ["contract.replies/1"],
                    },
                }
            )
            for entry in replay["steps"]:
                q = (
                    {"op": "tick", "handle": "s", "now": entry["tick"]}
                    if "tick" in entry
                    else {
                        "op": "step",
                        "handle": "s",
                        **{k: entry[k] for k in ("actor", "now", "message")},
                    }
                )
                requests.append(q)
                for key, value in entry["expect"].items():
                    base = (
                        "/decision/" if key in ("status", "reason") else "/state/state/"
                    )
                    assertions.append(
                        {
                            "response": len(requests) - 1,
                            "pointer": base + key,
                            "equals": value,
                        }
                    )
                if entry.get("expect", {}).get("reason") == "reference":
                    # A stale rejection must not rebind the current request or consume its wait.
                    request_id = (
                        "q2"
                        if item["id"]
                        in ("c1-acyclic-cross-wait", "sugar-stale-implicit-binding")
                        else "req2"
                    )
                    binding = item["correlation_binding"]
                    assertions.append(
                        {
                            "response": len(requests) - 1,
                            "pointer": "/state/state/variables/" + binding,
                            "equals": request_id,
                        }
                    )
        portable.append(
            {
                "id": "replies-adversarial-" + item["id"],
                "requirements": [],
                "requests": requests,
                "assertions": assertions,
            }
        )
    (FIXTURES / "suite.json").write_text(json.dumps({"cases": suite}, indent=2) + "\n")
    (FIXTURES / "semantic-inputs.json").write_text(
        json.dumps({"cases": portable}, indent=2) + "\n"
    )
    (FIXTURES / "EXPECTED.md").write_text(
        "# Expected codes and traces\n\n"
        "Generated from independently authored inputs, not observed implementation outputs. "
        "Trace entries show status / reason / state / outcome / version when pinned; "
        "terminal state names are not inferred. `None` means JSON null.\n\n"
        "| Vector | Required result |\n|---|---|\n" + "\n".join(table) + "\n"
    )


def run_probe(repo: Path, mode: str, timeout: float) -> list[dict[str, Any]]:
    paths = sorted((repo / "contract-lab/_build/dev/lib").glob("*/ebin"))
    if not paths:
        raise ValueError(f"Build {repo}/contract-lab first")
    argv = ["elixir", "--erl", "+S 4:4"]
    for path in paths:
        argv += ["-pa", str(path)]
    argv += [str(PROBE), mode, str(FIXTURES)]
    # subprocess timeout bounds the entire VM, including compile-time adversarial cases.
    output = run(argv, b"", cwd=repo / "contract-lab", timeout=timeout)
    rows = [json.loads(line) for line in output.splitlines()]
    expected = (
        [
            "old-compiler-rejects-reply-syntax",
            "old-engine-rejects-compiled-extension",
            "old-codec-rejects-unknown-wait",
        ]
        if mode == "old"
        else ["runner-real-replay-mutation-control"]
        + [c["id"] for c in json.loads((FIXTURES / "suite.json").read_text())["cases"]]
        + ["unknown-literals-never-interned"]
    )
    if [r.get("id") for r in rows] != expected or any(
        type(r.get("pass")) is not bool for r in rows
    ):
        raise ValueError(f"{mode} probe omitted, duplicated or malformed a result")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--normalize-evidence",
        action="store_true",
        help="Rewrite existing evidence paths only; preserve historical results",
    )
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument(
        "--old-repo",
        type=Path,
        help="Built immutable 95c9b25 checkout; required for old-reader checks",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    if args.normalize_evidence:
        for path in sorted((FIXTURES / "evidence").glob("*.json")):
            observed = json.loads(path.read_text())
            if portable_evidence(observed) != observed:
                path.write_text(evidence_json(observed, indent=2) + "\n")
        return 0
    if args.write:
        write_vectors()
        return 0
    try:
        for item in json.loads((FIXTURES / "suite.json").read_text())["cases"]:
            actual = hashlib.sha256(item["source"]["content"].encode()).hexdigest()
            if actual != item["source"]["sha256"]:
                raise ValueError(f"source identity mismatch: {item['id']}")
        if args.old_repo is None:
            raise ValueError(
                "--old-repo is required: S6 fail-closed probes may not be skipped"
            )
        old_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=args.old_repo, text=True
        ).strip()
        if old_head != "95c9b25a3ead70d94fb3977a81dd7cd13d22097c":
            raise ValueError(f"old-reader is not pinned 95c9b25: {old_head}")
        if subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=args.old_repo,
            text=True,
        ).strip():
            raise ValueError("old-reader checkout has modified tracked files")
        rows = run_probe(args.repo.resolve(), "candidate", args.timeout)
        rows += run_probe(args.old_repo.resolve(), "old", args.timeout)
        report = {
            "candidate_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=args.repo, text=True
            ).strip(),
            "old_head": old_head,
            "old_reader_checked": bool(args.old_repo),
            "cases": rows,
            "passed": sum(r["pass"] for r in rows),
            "failed": sum(not r["pass"] for r in rows),
        }
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(evidence_json(report, indent=2) + "\n")
        sys.stdout.write(str(evidence_json(report)) + "\n")
        return int(report["failed"] != 0)
    except (ValueError, RuntimeError, subprocess.SubprocessError, OSError) as error:
        sys.stdout.write(
            str(
                evidence_json(
                    {
                        "passed": 0,
                        "failed": 1,
                        "cases": [
                            {"id": "harness", "pass": False, "evidence": str(error)}
                        ],
                    }
                )
            )
            + "\n"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
