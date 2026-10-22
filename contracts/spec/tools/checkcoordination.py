"""Explicit portable assertions for multi-agent skill coordination extensions."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections.abc import Callable
from typing import Any

import jsonschema

from contracts.spec.tools.buildcontributionschemas import root as schema
from contracts.spec.tools.buildparticipantschemas import root as prior_schema
from contracts.spec.tools.buildrepliesschemas import root as replies_schema
from contracts.spec.tools.generatevectors import adapter
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT
from contracts.spec.tools.v2trace import verify as verify_tags
from contracts.spec.tools.vectorinputs import hosted

validators = {
    k: jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/" + k})
    for k in schema["$defs"]
}
# A contract that waits for replies is checked against protocol/6, which extends protocol/5.
replies_validators = {
    k: jsonschema.Draft202012Validator({**replies_schema, "$ref": "#/$defs/" + k})
    for k in replies_schema["$defs"]
}
checks = []
traces = []


def eq(name: str, actual: Any, expected: Any) -> None:
    assert actual == expected, (name, actual, expected)
    checks.append(name)


def run(
    requests: list[dict[str, Any]],
    validators: dict[str, jsonschema.Draft202012Validator] = validators,
) -> list[dict[str, Any]]:
    requests = hosted(
        requests
    )  # The harness is the trusted host: every session gets room_policy (D-15).
    responses = adapter(requests)
    for q, a in zip(requests, responses):
        if "error" in a:
            raise AssertionError((q["op"], a))
        if "source" in q:
            validators["source"].validate(q["source"])
        if q["op"] == "new":
            validators["config"].validate(q["config"])
        if q["op"] == "compile":
            validators["compiled"].validate(a["ok"])
        if q["op"] in ["new", "restore"]:
            validators["checkpoint_data"].validate(a["ok"])
        if q["op"] in [
            "step",
            "tick",
            "host_signal",
            "stop_contribution",
            "fail_contribution",
        ]:
            validators["checkpoint_data"].validate(a["state"])
            if q["op"] in ["stop_contribution", "fail_contribution"]:
                validators["contribution_report"].validate(q["report"])
                validators["contribution_decision"].validate(a["decision"])
            elif q["op"] != "tick":
                validators[
                    "host_decision" if q["op"] == "host_signal" else "decision"
                ].validate(a["decision"])
        if q["op"] == "view":
            validators["projection"].validate(a["ok"])
        if q["op"] == "checkpoint":
            validators["room_checkpoint"].validate(a["ok"])
    traces.append({"requests": requests, "responses": responses})
    return responses


def compile_request(path: str) -> dict[str, Any]:
    raw = (ROOT / path).read_text()
    return {
        "op": "compile",
        "handle": "c",
        "source": {
            "profile": "band-contract/file-source/1",
            "format": "frontmatter",
            "content": raw,
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
        },
    }


now = 200_000_000
compile_q = compile_request("contract-lab/contracts/v3/review-lanes.band")
config: dict[str, Any] = {
    "session": "s",
    "now": now,
    "bindings": {
        "lead": "l",
        "reviewers": [
            {
                "name": "security",
                "actor": "sec",
                "guidance": "Trace authority boundaries.",
                "attributes": {"specialty": "security"},
            },
            {
                "name": "frontend",
                "actor": "web",
                "attributes": {"specialty": "frontend"},
            },
            {"name": "general", "actor": "gen", "attributes": {"specialty": "general"}},
        ],
    },
    "max_messages": 20,
    "capabilities": ["host.presence/1", "room.contributions/1"],
    "max_controls": 32,
    "initial_presence": {
        "reviewers/general": {"value": "offline", "since": now - 172800000}
    },
}
setup = [compile_q, {"op": "new", "contract": "c", "handle": "s", "config": config}]


def step(
    actor: str, id: str, kind: str, payload: dict[str, Any], at: int
) -> dict[str, Any]:
    return {
        "op": "step",
        "handle": "s",
        "actor": actor,
        "now": now + at,
        "message": {"id": id, "session": "s", "kind": kind, "payload": payload},
    }


start = step("l", "request", "request", {"text": "Review immutable change abc123."}, 0)
sec = step("sec", "security", "review", {"text": "One supported finding."}, 1)
failure = {
    "id": "frontend-stop",
    "session": "s",
    "collection": "reports",
    "entry": 1,
    "member": "reviewers/frontend",
    "cause": {
        "type": "provider/budget_exhausted",
        "reference": "provider-event-49",
        "message": "Assigned work ended after the retry budget was exhausted.",
    },
}
web = {"op": "fail_contribution", "handle": "s", "now": now + 2, "report": failure}
r = run(
    setup
    + [
        start,
        sec,
        web,
        {"op": "view", "handle": "s", "actor": "l", "now": now + 2},
        {"op": "checkpoint", "handle": "s"},
    ]
)
s = r[4]["state"]["state"]
result = s["collection_results"][-1]
eq("source-pinned", r[0]["ok"]["source_digest"], compile_q["source"]["sha256"])
eq("all-settled-continues", s["state"], "summarizing")
eq("session-active", s["status"], "active")
eq("result-success", result["members"]["reviewers/security"]["status"], "ok")
eq("result-failed", result["members"]["reviewers/frontend"]["status"], "failed")
eq("known-old-offline", result["members"]["reviewers/general"]["status"], "offline")
eq("early-close", result["closed_at"], now + 2)
eq("state-profile", r[1]["ok"]["profile"], "band-contract/state/5")
eq(
    "room-contribution-profile",
    r[0]["ok"]["contribution_profile"],
    "band-contract/contribution/1",
)
eq(
    "collection-outcome-profile",
    r[0]["ok"]["collections"]["reports"]["profile"],
    "band-contract/collection/2",
)
eq(
    "failure-is-not-a-business-message",
    result["members"]["reviewers/frontend"]["message"],
    None,
)
eq(
    "original-cause-type",
    result["members"]["reviewers/frontend"]["failure"]["type"],
    "provider/budget_exhausted",
)
eq(
    "original-cause-reference",
    result["members"]["reviewers/frontend"]["failure"]["reference"],
    "provider-event-49",
)
eq(
    "failure-origin",
    result["members"]["reviewers/frontend"]["failure"]["origin"],
    "host",
)
eq(
    "lead-sees-report",
    r[5]["ok"]["view"]["facts"]["security"]["payload"]["text"],
    "One supported finding.",
)
restored = run(
    [
        compile_q,
        {
            "op": "restore",
            "contract": "c",
            "handle": "s",
            "checkpoint": r[6]["ok"],
            "now": now + 10,
        },
    ]
)
eq(
    "portable-recovery-results",
    restored[1]["ok"]["state"]["collection_results"],
    s["collection_results"],
)
eq(
    "portable-recovery-attributes",
    restored[1]["ok"]["state"]["roster_config"],
    config["bindings"],
)
eq(
    "typed-roster-profile",
    r[0]["ok"]["role_sets"]["reviewers"],
    {"profile": "band-contract/roster/2", "member": "reviewer"},
)
prior = lambda key: jsonschema.Draft202012Validator(
    {**prior_schema, "$ref": "#/$defs/" + key}
)
eq(
    "old-reader-rejects-contribution-compiled",
    prior("compiled").is_valid(r[0]["ok"]),
    False,
)
eq(
    "old-reader-rejects-new-state-profile",
    prior("checkpoint_data").is_valid(r[1]["ok"]),
    False,
)
typed = run(
    setup
    + [
        {"op": "query", "contract": "c", "selector": "type", "name": "reviewer"},
        {"op": "view", "handle": "s", "actor": "sec", "now": now},
    ]
)
validators["participant_type"].validate(typed[2]["ok"])
eq("participant-type-query", typed[2]["ok"]["profile"], "band-contract/participant/1")
eq(
    "participant-prompt-retained",
    "Work independently" in typed[2]["ok"]["meta"]["doc"],
    True,
)
eq(
    "participant-view-attributes",
    typed[3]["ok"]["view"]["definition"]["role"]["attributes"],
    {"specialty": "security"},
)
eq(
    "participant-view-assignment",
    typed[3]["ok"]["view"]["definition"]["role"]["assignment"],
    "Trace authority boundaries.",
)
bad_config = copy.deepcopy(config)
bad_config["bindings"]["reviewers"][0]["attributes"]["specialty"] = "owner"
bad_requests = hosted(
    [compile_q, {"op": "new", "contract": "c", "handle": "s", "config": bad_config}]
)
bad_responses = adapter(bad_requests)
eq(
    "participant-invalid-attribute-rejected",
    bad_responses[-1],
    {"error": "invalid_bindings"},
)
traces.append({"requests": bad_requests, "responses": bad_responses})
r = run(
    setup + [start, sec, {"op": "view", "handle": "s", "actor": "web", "now": now + 2}]
)
eq("private-report-hidden", "security" in r[4]["ok"]["view"]["facts"], False)
projection = r[4]["ok"]
message = {
    **step("web", "frontend", "review", {"text": "Frontend findings."}, 2)["message"],
    "expected_version": projection["view"]["version"],
}
check = run(
    [
        compile_q,
        {
            "op": "check",
            "contract": "c",
            "projection": projection,
            "actor": "web",
            "now": now + 2,
            "message": message,
        },
    ]
)
eq("optional-local-checker", check[1]["status"], "accepted")
eq(
    "member-discovers-scoped-control",
    projection["view"]["contribution_controls"],
    [
        {
            "operation": "stop_contribution",
            "session": "s",
            "collection": "reports",
            "entry": 1,
            "member": "reviewers/frontend",
        }
    ],
)
own = {**web, "op": "stop_contribution", "actor": "web"}
wrong = {**own, "actor": "sec"}
control = run(
    setup
    + [
        start,
        wrong,
        own,
        own,
        {"op": "view", "handle": "s", "actor": "sec", "now": now + 2},
        {"op": "checkpoint", "handle": "s"},
    ]
)
eq(
    "member-cannot-stop-other-member",
    control[3]["decision"]["reason"],
    "unauthorized_contribution",
)
eq("member-can-stop-own", control[4]["decision"]["status"], "contribution_stopped")
eq("control-exact-retry", control[5]["decision"]["status"], "duplicate_contribution")
eq(
    "control-is-not-agent-message",
    "frontend-stop" in control[4]["state"]["state"]["facts"],
    False,
)
eq(
    "other-projection-redacts-cause",
    "provider-event-49" in json.dumps(control[6]["ok"]),
    False,
)
after = run(
    [
        compile_q,
        {
            "op": "restore",
            "contract": "c",
            "handle": "s",
            "checkpoint": control[7]["ok"],
            "now": now + 3,
        },
        {**own, "now": now + 3},
    ]
)
eq(
    "control-retry-after-restart",
    after[2]["decision"]["status"],
    "duplicate_contribution",
)
r = run(setup + [start, sec, {"op": "tick", "handle": "s", "now": now + 300000}])
eq("timeout-partial-state", r[-1]["state"]["state"]["state"], "summarizing")
eq(
    "timeout-member",
    r[-1]["state"]["state"]["collection_results"][-1]["members"]["reviewers/frontend"][
        "status"
    ],
    "timed_out",
)
# A renewable contract clock, without collections or a reply wait: the worker works from the start.
raw = (
    "---\nname: renewable-contract\ndescription: Renewal vector.\nengine: 0.2.0-lab.1\nroles: [lead, worker]\n---\n"
    "contract @80m max @4h => expired\ntype heartbeat\ntype result\n  text: text(1..4000)\nstate new\n"
    "  event worker -> lead: heartbeat() => @contract\n  worker -> lead: result() => complete\nend complete\n"
)
q = {
    "op": "compile",
    "handle": "c",
    "source": {
        "profile": "band-contract/file-source/1",
        "format": "frontmatter",
        "content": raw,
        "sha256": hashlib.sha256(raw.encode()).hexdigest(),
    },
}
cfg = {
    "session": "s",
    "now": now,
    "max_messages": 20,
    "bindings": {"lead": "l", "worker": "w"},
}
rs = run(
    [
        q,
        {"op": "new", "contract": "c", "handle": "s", "config": cfg},
        step("w", "h", "heartbeat", {}, 4000000),
        {"op": "checkpoint", "handle": "s"},
    ]
)
eq("contract-renewed", rs[2]["state"]["state"]["max_at"], now + 8800000)
eq("maximum-unchanged", rs[2]["state"]["state"]["clocks"]["hard_at"], now + 14400000)
run(
    [
        q,
        {
            "op": "restore",
            "contract": "c",
            "handle": "s",
            "checkpoint": rs[3]["ok"],
            "now": now + 4000001,
        },
    ]
)
bad = copy.deepcopy(q)
bad["source"]["content"] = bad["source"]["content"].replace(
    "@80m max @4h", "@infinity max @4h"
)
bad["source"]["sha256"] = hashlib.sha256(bad["source"]["content"].encode()).hexdigest()
response = adapter([bad])
validators["compiler_error"].validate(response[0]["error"])
# Portable conformance compares a compiler error's code and line exactly (contract-dsl.md, spec M10).
eq(
    "infinite-window-finite-maximum-rejected",
    (response[0]["error"]["code"], response[0]["error"]["line"]),
    ("duration", 1),
)
traces.append({"requests": [bad], "responses": response})
# The renewable work skill (v3): the job request awaits its result (contract.replies/1), and a
# heartbeat renews that wait by its 80-minute window, never past the 4-hour contract clock.
q = compile_request("contract-lab/contracts/v3/renewable-work.band")
cfg = {
    "session": "s",
    "now": now,
    "max_messages": 20,
    "bindings": {"lead": "l", "worker": "w"},
    "capabilities": ["contract.replies/1"],
}
rs = run(
    [
        q,
        {"op": "new", "contract": "c", "handle": "s", "config": cfg},
        step("l", "q", "request", {"text": "Perform work."}, 0),
        step("w", "h", "heartbeat", {}, 4000000),
        {"op": "checkpoint", "handle": "s"},
    ],
    replies_validators,
)
assert [r["decision"]["status"] for r in rs[2:4]] == ["accepted", "accepted"], [
    r["decision"] for r in rs[2:4]
]
assert (
    rs[3]["state"]["state"]["wait"]["due_at"],
    rs[3]["state"]["state"]["max_at"],
) == (now + 8800000, now + 14400000), rs[3]["state"]["state"]
run(
    [
        q,
        {
            "op": "restore",
            "contract": "c",
            "handle": "s",
            "checkpoint": rs[4]["ok"],
            "now": now + 4000001,
        },
    ],
    replies_validators,
)
# Timer semantics (DECISIONS D-11, D-12, D-13). Sources are inline so each pair is isolated.
pending = []


def red(name: str, actual: Any, expected: Any, waiting: Any) -> None:
    # A vector the specification requires before its engine change lands; it never fails the run.
    if actual == expected:
        checks.append(name)
    else:
        pending.append(
            {
                "name": name,
                "waiting_for": waiting,
                "expected": expected,
                "actual": actual,
            }
        )


def compile_text(name: str, header: str, body: str) -> dict[str, Any]:
    raw = (
        "---\nname: "
        + name
        + "\ndescription: Timer semantics vector.\nengine: 0.2.0-lab.1\n"
        + header
        + "---\n"
        + body
    )
    return {
        "op": "compile",
        "handle": "c",
        "source": {
            "profile": "band-contract/file-source/1",
            "format": "frontmatter",
            "content": raw,
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
        },
    }


def new(bindings: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "op": "new",
        "contract": "c",
        "handle": "s",
        "config": {
            "session": "s",
            "now": now,
            "bindings": bindings,
            "max_messages": 20,
            **extra,
        },
    }


def presence(
    seq: int, subject: str, principal: str, value: Any, at: int
) -> dict[str, Any]:
    return {
        "op": "host_signal",
        "handle": "s",
        "now": now + at,
        "event": {
            "id": f"p{seq}",
            "sequence": seq,
            "subject": subject,
            "principal": principal,
            "kind": "presence",
            "value": value,
            "at": now + at,
        },
    }


tick = lambda at: {"op": "tick", "handle": "s", "now": now + at}
pair = {"lead": "l", "a": "pa", "b": "pb"}
lanes = """contract @1h => expired
type request
type review
type note
type summary
state new
  lead -> {a,b}: request() => reviewing
state reviewing
  collect reports from {a,b} -> lead @5m => summarizing
    reply review()
"""
ends = """state summarizing
  lead -> {a,b}: summary() => complete
end complete
"""
contrib = {"capabilities": ["room.contributions/1"]}
# D-12, pair 1: a work duty and a collection deadline due together. The duty fires first.
q = compile_text(
    "tie-duty-collection",
    "roles: [lead, a, b]\nextensions: [room.contributions/1]\n",
    lanes.replace(
        "  collect reports",
        "  lead -> a: note() @5m => complete | note_missing\n  collect reports",
    )
    + ends
    + "end note_missing\n",
)
r = run(
    [
        q,
        new(pair, **contrib),
        step("l", "request", "request", {}, 0),
        step("pa", "ra", "review", {}, 60000),
        tick(300000),
    ]
)
eq("tie-duty-before-collection", r[-1]["state"]["state"]["outcome"], "note_missing")
# D-12, pair 2: a collection deadline and an offline grace due together. The collection closes first.
grace = lanes.replace("    reply review()\n", "    reply review()\n    offline @30s\n")
q = compile_text(
    "tie-collection-offline",
    "roles: [lead, a, b]\nextensions: [room.contributions/1, host.presence/1]\n",
    grace + ends,
)
cfg = {"capabilities": ["room.contributions/1", "host.presence/1"], "max_controls": 16}
r = run(
    [
        q,
        new(pair, **cfg),
        step("l", "request", "request", {}, 0),
        step("pa", "ra", "review", {}, 60000),
        presence(1, "b", "pb", "offline", 270000),
        tick(300000),
    ]
)
s = r[-1]["state"]["state"]
eq(
    "tie-collection-before-offline",
    s["collection_results"][-1]["members"]["b"]["status"],
    "timed_out",
)
eq("tie-collection-continues", s["state"], "summarizing")
# Pairs 2 and 3 together: collection deadline, offline grace and state cap at one instant (a collection window cannot exceed its state cap).
q = compile_text(
    "tie-collection-offline-state",
    "roles: [lead, a, b]\nextensions: [room.contributions/1, host.presence/1]\n",
    grace.replace("state reviewing\n", "state reviewing @5m => stalled\n")
    + ends
    + "end stalled\n",
)
r = run(
    [
        q,
        new(pair, **cfg),
        step("l", "request", "request", {}, 0),
        step("pa", "ra", "review", {}, 60000),
        presence(1, "b", "pb", "offline", 270000),
        tick(300000),
    ]
)
eq(
    "tie-collection-before-state-timeout",
    r[-1]["state"]["state"]["state"],
    "summarizing",
)
# The worker claims the work, so its result is a duty it owes, not a reply to a request (reply_required).
work = """type claim
type result
state new
  worker -> lead: claim() => working
"""
pause = """  on worker offline for 4m => paused
  worker -> lead: result() => complete
state paused
  worker -> lead: result() => complete
end complete
"""
duo = {"lead": "l", "worker": "w"}
presence_cfg = {"capabilities": ["host.presence/1"], "max_controls": 16}
# D-12, pair 4: a state timeout and a host trigger due together. The state timeout fires first.
q = compile_text(
    "tie-state-host",
    "roles: [lead, worker]\nextensions: [host.presence/1]\n",
    "contract @1h => expired\n"
    + work
    + "state working @5m => stalled\n"
    + pause
    + "end stalled\n",
)
r = run(
    [
        q,
        new(duo, **presence_cfg),
        step("w", "claim", "claim", {}, 0),
        presence(1, "worker", "w", "offline", 60000),
        tick(300000),
    ]
)
eq(
    "tie-state-timeout-before-host-trigger",
    r[-1]["state"]["state"]["outcome"],
    "stalled",
)
# D-12, pair 5: a host trigger and the whole-run cap due together. The trigger fires, then the cap ends the run.
q = compile_text(
    "tie-host-cap",
    "roles: [lead, worker]\nextensions: [host.presence/1]\n",
    "contract @5m => expired\n" + work + "state working\n" + pause,
)
r = run(
    [
        q,
        new(duo, **presence_cfg),
        step("w", "claim", "claim", {}, 0),
        presence(1, "worker", "w", "offline", 60000),
        tick(300000),
    ]
)
s = r[-1]["state"]["state"]
eq(
    "tie-host-trigger-before-cap",
    [e.get("target") for e in s["control_log"] if e.get("target")],
    ["paused"],
)
eq("tie-cap-after-host-trigger", s["outcome"], "expired")
# Cascade: a collection deadline and the whole-run cap due together. The collection closes, then the cap ends the run.
q = compile_text(
    "tie-collection-cap",
    "roles: [lead, a, b]\nextensions: [room.contributions/1]\n",
    lanes.replace("contract @1h => expired", "contract @5m => expired") + ends,
)
r = run(
    [
        q,
        new(pair, **contrib),
        step("l", "request", "request", {}, 0),
        step("pa", "ra", "review", {}, 60000),
        tick(300000),
    ]
)
s = r[-1]["state"]["state"]
eq(
    "tie-collection-before-cap",
    [s["collection_results"][-1]["reason"], s["outcome"]],
    ["deadline", "expired"],
)
# The same tie with a terminal collection outcome. The collection ends the run first, so its outcome stands.
q = compile_text(
    "tie-terminal-collection-cap",
    "roles: [lead, a, b]\nextensions: [room.contributions/1]\n",
    lanes.replace("contract @1h => expired", "contract @5m => expired").replace(
        "=> summarizing", "=> collected"
    )
    + "end collected\n",
)
r = run(
    [
        q,
        new(pair, **contrib),
        step("l", "request", "request", {}, 0),
        step("pa", "ra", "review", {}, 60000),
        tick(300000),
    ]
)
s = r[-1]["state"]["state"]
eq(
    "tie-terminal-collection-stands",
    [s["collection_results"][-1]["reason"], s["outcome"]],
    ["deadline", "collected"],
)
# D-13: a collection deadline observed late. summarizing starts at the deadline, so its own clock has already run out.
late = (
    lanes
    + "state summarizing @2m => undecided\n  lead -> {a,b}: summary() => complete\nend complete\nend undecided\n"
)
q = compile_text(
    "late-collection", "roles: [lead, a, b]\nextensions: [room.contributions/1]\n", late
)
first = [
    q,
    new(pair, **contrib),
    step("l", "request", "request", {}, 0),
    step("pa", "ra", "review", {}, 60000),
]
r = run(first + [step("l", "sum", "summary", {}, 430000)])
s = r[-1]["state"]["state"]
eq(
    "late-collection-entry-at-deadline",
    s["collection_results"][-1]["closed_at"],
    now + 300000,
)
eq(
    "late-collection-next-clock-from-deadline",
    [s["outcome"], s["expired_at"]],
    ["undecided", now + 420000],
)
eq("late-collection-proposal-after-cascade", r[-1]["decision"]["reason"], "terminal")
cp = run(first + [{"op": "checkpoint", "handle": "s"}])[-1]["ok"]
r = run(
    [
        q,
        {
            "op": "restore",
            "contract": "c",
            "handle": "s",
            "checkpoint": cp,
            "now": now + 60001,
        },
        tick(430000),
    ]
)
eq(
    "late-collection-after-restore",
    [r[-1]["state"]["state"]["outcome"], r[-1]["state"]["state"]["expired_at"]],
    ["undecided", now + 420000],
)
# D-13: a dwell trigger observed late. paused starts at the trigger time, and its clock counts from there.
q = compile_text(
    "late-dwell",
    "roles: [lead, worker]\nextensions: [host.presence/1]\n",
    "contract @1h => expired\n"
    + work
    + "state working\n  on worker offline for 5m => paused\n  worker -> lead: result() => complete\nstate paused @2m => stalled\n  worker -> lead: result() => complete\nend complete\nend stalled\n",
)
r = run(
    [
        q,
        new(duo, **presence_cfg),
        step("w", "claim", "claim", {}, 0),
        presence(1, "worker", "w", "offline", 1000),
        tick(500000),
    ]
)
eq(
    "late-dwell-entry-at-trigger",
    [r[-1]["state"]["state"]["outcome"], r[-1]["state"]["state"]["expired_at"]],
    ["stalled", now + 421000],
)
# D-11: max_at is the current window, hard_at the fixed maximum; admission tests new duties against max_at.
q = compile_text(
    "window-admission",
    "roles: [lead, worker]\n",
    "contract @80m max @4h => expired\n"
    + work
    + "state working\n  event worker -> lead: heartbeat() => @contract\n  worker -> lead: result() @30m => complete | stalled\ntype heartbeat\nend complete\nend stalled\n",
)
r = run([q, new(duo), step("w", "late", "claim", {}, 4200000)])
eq("window-admission-uses-max-at", r[-1]["decision"]["reason"], "insufficient_time")
r = run(
    [
        q,
        new(duo),
        step("w", "early", "claim", {}, 2400000),
        {"op": "checkpoint", "handle": "s"},
    ]
)
eq("window-admission-fits", r[-2]["decision"]["status"], "accepted")
eq(
    "window-and-maximum-initial",
    [r[-2]["state"]["state"]["max_at"], r[-2]["state"]["state"]["clocks"]["hard_at"]],
    [now + 4800000, now + 14400000],
)
r = run(
    [
        q,
        {
            "op": "restore",
            "contract": "c",
            "handle": "s",
            "checkpoint": r[-1]["ok"],
            "now": now + 2400001,
        },
    ]
)
eq(
    "window-and-maximum-restored",
    [r[-1]["ok"]["state"]["max_at"], r[-1]["ok"]["state"]["clocks"]["hard_at"]],
    [now + 4800000, now + 14400000],
)


# Contribution and host-signal conflicts and races (spec M23; contribution-outcomes.md admission, host-signals.md).
# Every rejection and retry must leave the whole state unchanged apart from the trusted clock.
def same(a: Any, b: Any) -> set[str]:
    return {
        k
        for k in a["state"]["state"]
        if k != "now" and a["state"]["state"][k] != b["state"]["state"][k]
    }


def report(at: int, **changes: Any) -> dict[str, Any]:
    return {
        "op": "fail_contribution",
        "handle": "s",
        "now": now + at,
        "report": {**failure, **changes},
    }


member = lambda a, name="reviewers/frontend": (
    a["state"]["state"]["collections"].get("reports")
    or a["state"]["state"]["collection_results"][-1]
)["members"][name]["status"]
reply = lambda at: step("web", "frontend", "review", {"text": "Frontend findings."}, at)
r = run(
    setup
    + [
        start,
        report(2),
        report(3),
        report(4, cause={**failure["cause"], "message": "Changed."}),
        {**report(5), "op": "stop_contribution", "actor": "web"},
    ]
)
eq(
    "contribution-retry-original-receipt",
    r[4]["decision"],
    {**r[3]["decision"], "status": "duplicate_contribution"},
)
eq("contribution-retry-keeps-state", same(r[4], r[3]), set())
eq(
    "contribution-id-conflict-changed-cause",
    r[5]["decision"],
    {"status": "rejected", "reason": "id_conflict"},
)
eq(
    "contribution-id-conflict-changed-producer",
    r[6]["decision"],
    {"status": "rejected", "reason": "id_conflict"},
)
eq(
    "contribution-conflict-keeps-state",
    [same(r[5], r[3]), same(r[6], r[3]), member(r[6])],
    [set(), set(), "failed"],
)
r = run(setup + [start, reply(2), report(3)])
eq(
    "reply-wins-before-failure",
    [r[4]["decision"]["reason"], member(r[4]), same(r[4], r[3])],
    ["already_contributed", "ok", set()],
)
r = run(setup + [start, report(2), reply(3)])
eq(
    "failure-wins-before-reply",
    [r[4]["decision"]["reason"], member(r[4]), same(r[4], r[3])],
    ["already_contributed", "failed", set()],
)
r = run(setup + [start, report(300000)])
eq(
    "deadline-before-exact-report",
    [r[3]["decision"]["reason"], member(r[3])],
    ["contribution_scope", "timed_out"],
)
r = run(setup + [start, reply(300000)])
eq(
    "deadline-before-exact-reply",
    [r[3]["decision"]["status"], member(r[3])],
    ["rejected", "timed_out"],
)
r = run(
    setup
    + [
        start,
        report(2, entry=2),
        report(2, session="other"),
        report(2, member="reviewers/nobody"),
        report(2, member="reviewers/general"),
    ]
)
eq(
    "stale-entry-rejected",
    r[3]["decision"],
    {"status": "rejected", "reason": "contribution_scope"},
)
eq(
    "wrong-session-rejected",
    r[4]["decision"],
    {"status": "rejected", "reason": "contribution_scope"},
)
eq(
    "unrelated-member-rejected",
    r[5]["decision"],
    {"status": "rejected", "reason": "contribution_scope"},
)
eq(
    "settled-member-rejected",
    r[6]["decision"],
    {"status": "rejected", "reason": "already_contributed"},
)
eq("scope-rejections-keep-state", [same(x, r[2]) for x in r[3:7]], [set()] * 4)
# The control bound is max_messages, shared across rounds; business admission reserves reply capacity,
# so the sixth control (third round) is the first one over a bound of five.
q = compile_text(
    "control-capacity",
    "roles: [lead, a, b]\nextensions: [room.contributions/1]\n",
    lanes.replace(
        "    reply review()\n",
        "    reply review()\nstate summarizing\n  lead -> {a,b}: request() => reviewing\n  lead -> {a,b}: summary() => complete\nend complete\n",
    ),
)


def control(i: str, who: str, entry: int, at: int) -> dict[str, Any]:
    return {
        "op": "fail_contribution",
        "handle": "s",
        "now": now + at,
        "report": {
            "id": i,
            "session": "s",
            "collection": "reports",
            "entry": entry,
            "member": who,
            "cause": {
                "type": "provider/budget_exhausted",
                "reference": "event-" + i,
                "message": "Ended.",
            },
        },
    }


rounds = []
for entry in (1, 2, 3):
    rounds += [
        step("l", f"r{entry}", "request", {}, 3 * entry - 3),
        control(f"c{entry}a", "a", entry, 3 * entry - 2),
        control(f"c{entry}b", "b", entry, 3 * entry - 1),
    ]
r = run(
    [q, new(pair, max_messages=5, **contrib)]
    + rounds
    + [control("c3a", "a", 3, 9), tick(300006)]
)
eq(
    "contribution-capacity",
    [x["decision"]["status"] for x in r[2:11]],
    ["accepted", "contribution_stopped", "contribution_stopped"] * 2
    + ["accepted", "contribution_stopped", "rejected"],
)
eq(
    "capacity-rejection-keeps-state",
    [
        r[10]["decision"]["reason"],
        same(r[10], r[9]),
        len(r[10]["state"]["state"]["contribution_receipts"]),
    ],
    ["contribution_capacity", set(), 5],
)
eq(
    "capacity-keeps-retries",
    r[11]["decision"],
    {**r[9]["decision"], "status": "duplicate_contribution"},
)
eq(
    "capacity-keeps-deadlines",
    {
        k: v["status"]
        for k, v in r[12]["state"]["state"]["collection_results"][-1]["members"].items()
    },
    {"a": "failed", "b": "timed_out"},
)
signal = lambda i, seq, value, at: {
    "op": "host_signal",
    "handle": "s",
    "now": now + at,
    "event": {
        "id": i,
        "sequence": seq,
        "subject": "reviewers/frontend",
        "principal": "web",
        "kind": "presence",
        "value": value,
        "at": now + at,
    },
}
first = signal("h2", 2, "offline", 2)
r = run(
    setup
    + [
        start,
        first,
        {**first, "now": now + 3},
        {**first, "event": {**first["event"], "value": "online"}, "now": now + 4},
        signal("h3", 2, "online", 5),
        signal("h4", 1, "online", 6),
    ]
)
eq(
    "host-retry-keeps-state",
    [r[3]["decision"], r[4]["decision"], same(r[4], r[3])],
    [{"status": "host_accepted"}, {"status": "duplicate_host_event"}, set()],
)
eq(
    "host-id-conflict",
    r[5]["decision"],
    {"status": "rejected", "reason": "id_conflict"},
)
eq(
    "host-stale-equal-sequence",
    r[6]["decision"],
    {"status": "rejected", "reason": "stale_host_event"},
)
eq(
    "host-stale-old-sequence",
    r[7]["decision"],
    {"status": "rejected", "reason": "stale_host_event"},
)
eq("host-rejections-keep-state", [same(x, r[3]) for x in r[5:8]], [set()] * 3)
# Spec M4: the initial_presence snapshot takes sequence 1, so sequence 1 as the first producer event is stale;
# without a snapshot the stored revision is 0 and sequence 1 is accepted.
r = run(setup + [start, signal("h1", 1, "offline", 2), signal("h2", 2, "offline", 3)])
eq(
    "host-snapshot-takes-sequence-1",
    [
        r[1]["ok"]["state"]["signal_revision"],
        r[3]["decision"],
        same(r[3], r[2]),
        r[4]["decision"]["status"],
    ],
    [1, {"status": "rejected", "reason": "stale_host_event"}, set(), "host_accepted"],
)
bare = {k: v for k, v in config.items() if k != "initial_presence"}
r = run(
    [
        compile_q,
        {"op": "new", "contract": "c", "handle": "s", "config": bare},
        start,
        signal("h1", 1, "offline", 2),
    ]
)
eq(
    "host-no-snapshot-sequence-1-valid",
    [r[1]["ok"]["state"]["signal_revision"], r[3]["decision"]["status"]],
    [0, "host_accepted"],
)
# Spec M6: the receipt lookup precedes the sequence check, so an exact retry after a newer sequence keeps its receipt.
r = run(setup + [start, first, signal("h3", 3, "online", 3), {**first, "now": now + 4}])
eq(
    "host-retry-after-newer-sequence",
    [r[4]["decision"]["status"], r[5]["decision"], same(r[5], r[4])],
    ["host_accepted", {"status": "duplicate_host_event"}, set()],
)
# A reducer time behind the stored clock is stale even when at equals it; the extension is checked before the value.
r = run(
    setup
    + [
        start,
        tick(5),
        signal("h2", 2, "offline", 4),
        {**first, "event": {**first["event"], "kind": "membership", "value": "online"}},
    ]
)
eq(
    "host-time-behind-state-stale",
    [r[4]["decision"], same(r[4], r[3])],
    [{"status": "rejected", "reason": "stale_host_event"}, set()],
)
eq(
    "host-extension-before-value",
    r[5]["decision"],
    {"status": "rejected", "reason": "unsupported_extension"},
)
# Spec O3: step 8 runs elapsed timers before admission, so an event that finds the session ended is rejected
# `terminal` and returns exactly the state those timers produced, not the state before the event.
r = run(setup + [start, signal("h2", 2, "offline", 1800001)])
ended = run(setup + [start, tick(1800001)])
host_end = r[3]["state"]["state"]
eq(
    "host-terminal-after-elapsed-timers",
    [
        r[3]["decision"],
        host_end["status"],
        host_end["outcome"],
        host_end["version"] > r[2]["state"]["state"]["version"],
        host_end == ended[3]["state"]["state"],
    ],
    [{"status": "rejected", "reason": "terminal"}, "terminal", "expired", True, True],
)
# Review fixes (rr3 spec @815c378, Rafael): coordination, contribution and renewal rules the prose now states.
probe = lambda requests: adapter(hosted(requests))
# M7: closed rounds count against max_messages; the round that reaches it diverts to completion_capacity.
loop = "contract @1h => expired\ntype request\ntype review\ntype done\nstate new\n  lead -> {a,b}: request() => polling\nstate polling\n  lead -> {a,b}: done() => complete\n  collect reports from {a,b} -> lead @1m => polling\n    reply review()\nend complete\n"
q = compile_text(
    "closed-round-bound",
    "roles: [lead, a, b]\nextensions: [room.contributions/1]\n",
    loop,
)
r = run(
    [
        q,
        {
            **new(pair, **contrib),
            "config": {**new(pair, **contrib)["config"], "max_messages": 3},
        },
        step("l", "request", "request", {}, 0),
        tick(120000),
        tick(180000),
    ]
)
s = r[-2]["state"]["state"]
eq(
    "closed-rounds-below-limit-continue",
    [s["state"], [x["reason"] for x in s["collection_results"]]],
    ["polling", ["deadline", "deadline"]],
)
s = r[-1]["state"]["state"]
eq(
    "closed-round-limit-outcome",
    [s["outcome"], [x["reason"] for x in s["collection_results"]]],
    ["completion_capacity", ["deadline", "deadline", "deadline"]],
)
# M11: a collection close replaces outstanding duties with the next state's, like a message transition.
q = compile_text(
    "close-drops-duties",
    "roles: [lead, a, b]\nextensions: [room.contributions/1]\n",
    lanes.replace(
        "  collect reports",
        "  event lead -> {a,b}: note() @5m => note_missing\n  collect reports",
    )
    + ends
    + "end note_missing\n",
)
r = run(
    [
        q,
        new(pair, **contrib),
        step("l", "request", "request", {}, 0),
        step("pa", "ra", "review", {}, 1000),
        step("pb", "rb", "review", {}, 2000),
        tick(400000),
    ]
)
eq(
    "collection-close-drops-state-duties",
    [r[-2]["state"]["state"]["state"], r[-2]["state"]["state"]["obligations"]],
    ["summarizing", []],
)
eq(
    "dropped-duty-never-fires",
    [r[-1]["state"]["state"]["status"], r[-1]["state"]["state"]["state"]],
    ["active", "summarizing"],
)
# N2: version counts journal rows (facts, collection rows, control records, expiry), not transitions.
r = run(
    [
        q,
        new(pair, **contrib),
        step("l", "request", "request", {}, 0),
        step("pa", "ra", "review", {}, 1000),
        step("pb", "rb", "review", {}, 2000),
    ]
)
eq("version-message-close", [x["state"]["state"]["version"] for x in r[2:]], [1, 2, 4])
q = compile_text(
    "version-trigger",
    "roles: [lead, worker]\nextensions: [host.presence/1]\n",
    "contract @1h => expired\n" + work + "state working\n" + pause,
)
r = run(
    [
        q,
        new(duo, **presence_cfg),
        step("w", "claim", "claim", {}, 0),
        presence(1, "worker", "w", "offline", 1000),
        presence(2, "worker", "w", "offline", 2000),
        tick(300000),
    ]
)
eq(
    "version-presence-and-trigger",
    [x["state"]["state"]["version"] for x in r[2:]]
    + [r[-1]["state"]["state"]["state"]],
    [1, 2, 3, 4, "paused"],
)
# M10: misplaced renewal targets are compile errors with code timer_target.
for name, clock, body in [
    (
        "state-target-without-clock",
        "contract @1h => expired\n",
        "state working\n  event worker -> lead: heartbeat() => @state\n",
    ),
    (
        "contract-target-fixed-clock",
        "contract @30m => expired\n",
        "state working\n  event worker -> lead: heartbeat() => @contract\n",
    ),
    (
        "contract-target-no-cap",
        "contract @infinity => expired\n",
        "state working\n  event worker -> lead: heartbeat() => @contract\n",
    ),
]:
    bad = compile_text(
        name,
        "roles: [lead, worker]\n",
        clock
        + work
        + "type heartbeat\n"
        + body
        + "  worker -> lead: result() => complete\nend complete\n",
    )
    response = probe([bad])
    validators["compiler_error"].validate(response[0]["error"])
    # Portable conformance compares a compiler error's code and line exactly (contract-dsl.md, spec M10);
    # body line 8 is the misplaced `=> @state` / `=> @contract` rule in all three sources.
    eq(
        "timer-target-" + name,
        (response[0]["error"]["code"], response[0]["error"]["line"]),
        ("timer_target", 8),
    )
    traces.append({"requests": [bad], "responses": response})
# M5: the creation snapshot is the only backdated presence; its since seeds offline grace and host-trigger dwell alike.
seeded = (
    "contract @1h => expired\ntype request\ntype review\ntype summary\nstate new\n  lead -> {a,b}: request() => reviewing\nstate reviewing\n  on a offline for 4m => paused\n  collect reports from {a,b} -> lead @5m => summarizing\n    reply review()\n    offline @30s\nstate paused\n  lead -> {a,b}: summary() => complete\n"
    + ends
)
q = compile_text(
    "seeded-presence",
    "roles: [lead, a, b]\nextensions: [room.contributions/1, host.presence/1]\n",
    seeded,
)
r = run(
    [
        q,
        new(
            pair,
            **cfg,
            initial_presence={"a": {"value": "offline", "since": now - 600000}},
        ),
        step("l", "request", "request", {}, 0),
    ]
)
s = r[-1]["state"]["state"]
eq(
    "seeded-presence-fires-at-entry",
    [
        s["state"],
        [(x.get("target"), x.get("at")) for x in s["control_log"] if x.get("target")],
    ],
    ["paused", [("paused", now)]],
)
eq(
    "seeded-presence-settles-grace",
    [(x["role"], x["reason"]) for x in s["collection_log"]],
    [("a", "offline"), ("", "cancelled")],
)
# H2: a round cancelled before any member replied or failed leaves only its log rows, no result.
eq("cancelled-round-without-contribution-no-result", s["collection_results"], [])
# L4: ownership is checked before the receipt lookup, so a peer replaying a settled member's exact control is unauthorized.
replay = run(setup + [start, own, wrong])
eq(
    "peer-replay-after-settlement",
    [replay[3]["decision"]["status"], replay[4]["decision"]["reason"]],
    ["contribution_stopped", "unauthorized_contribution"],
)


# M22: roster bounds at creation. Every failure is invalid_bindings; the 255-member and 16,384-byte boundaries are accepted.
def roster_case(
    name: str, mutate: Callable[[dict[str, Any]], None], expected: Any
) -> None:
    bad = copy.deepcopy(config)
    bad.pop("initial_presence")
    mutate(bad["bindings"])
    requests = hosted(
        [compile_q, {"op": "new", "contract": "c", "handle": "s", "config": bad}]
    )
    responses = adapter(requests)
    eq("roster-" + name, "ok" if "ok" in responses[-1] else responses[-1], expected)
    # Generated 255/256-member and 16 KiB-guidance rosters are rebuilt by this script, not stored in the report.
    if len(json.dumps(requests)) < 65536:
        traces.append({"requests": requests, "responses": responses})


roster_member = lambda i: {
    "name": f"m{i}",
    "actor": f"a{i}",
    "attributes": {"specialty": "general"},
}
roster_case(
    "lead-as-member",
    lambda b: b["reviewers"][0].update(actor="l"),
    {"error": "invalid_bindings"},
)
roster_case(
    "duplicate-member-actor",
    lambda b: b["reviewers"][1].update(actor="sec"),
    {"error": "invalid_bindings"},
)
roster_case("empty", lambda b: b.update(reviewers=[]), {"error": "invalid_bindings"})
roster_case(
    "256-members",
    lambda b: b.update(reviewers=[roster_member(i) for i in range(256)]),
    {"error": "invalid_bindings"},
)
roster_case(
    "255-members",
    lambda b: b.update(reviewers=[roster_member(i) for i in range(255)]),
    "ok",
)
roster_case(
    "guidance-16385",
    lambda b: b["reviewers"][0].update(guidance="g" * 16385),
    {"error": "invalid_bindings"},
)
roster_case(
    "guidance-16384", lambda b: b["reviewers"][0].update(guidance="g" * 16384), "ok"
)
# M9: a heartbeat stays in its state, so it must leave the untimed summary (the cheapest closing path) in the budget.
q = compile_text(
    "closing-reserve",
    "roles: [lead, a, b]\nextensions: [room.contributions/1]\n",
    lanes.replace(
        "  collect reports", "  event {a,b} -> lead: note() => @self\n  collect reports"
    )
    + ends,
)
r = run(
    [
        q,
        {
            **new(pair, **contrib),
            "config": {**new(pair, **contrib)["config"], "max_messages": 5},
        },
        step("l", "request", "request", {}, 0),
        step("pa", "na", "note", {}, 1000),
        step("pb", "nb", "note", {}, 2000),
        step("pa", "ra", "review", {}, 3000),
        step("pb", "rb", "review", {}, 4000),
        step("l", "sum", "summary", {}, 5000),
    ]
)
eq(
    "heartbeat-keeps-closing-reserve",
    [
        x["decision"]["status"]
        if x["decision"]["status"] != "rejected"
        else x["decision"]["reason"]
        for x in r[2:]
    ],
    ["accepted", "accepted", "completion_capacity", "accepted", "accepted", "accepted"],
)
eq("closing-reserve-completes", r[-1]["state"]["state"]["outcome"], "complete")
# M16: optional per-actor room-event shares (default: the room-wide values). One sender cannot use the whole window or receipt capacity.
q = compile_text(
    "room-actor-quota",
    "roles: [a, b, lead]\n",
    "contract @30m => expired\ntype notice\ntype close\nstate new\n  lead -> {a,b}: close() => complete\nroom\n  event {a,b} -> lead: notice()\nend complete\n",
)
room_config = {
    "bindings": {"a": "a", "b": "b", "lead": "l"},
    "now": now,
    "window_ms": 1000,
    "max_events": 4,
    "max_mentions": 4,
    "max_receipts": 6,
    "max_events_per_actor": 2,
    "max_receipts_per_actor": 3,
}
validators["room_config"].validate(room_config)
note = lambda actor, i, at: {
    "op": "room_step",
    "handle": "r",
    "actor": actor,
    "now": now + at,
    "message": {"id": i, "kind": "notice", "payload": {}},
}
r = run(
    [
        q,
        {"op": "room_new", "contract": "c", "handle": "r", "config": room_config},
        note("a", "a1", 0),
        note("a", "a2", 1),
        note("a", "a3", 2),
        note("b", "b1", 3),
        note("a", "a3", 1000),
        note("a", "a4", 1001),
        note("b", "b2", 1002),
    ]
)
eq(
    "room-actor-shares",
    [x["decision"].get("reason", x["decision"]["status"]) for x in r[2:]],
    [
        "accepted",
        "accepted",
        "room_budget",
        "accepted",
        "accepted",
        "retention_limit",
        "accepted",
    ],
)
shared = {k: v for k, v in room_config.items() if not k.endswith("_per_actor")}
r = run(
    [q, {"op": "room_new", "contract": "c", "handle": "r", "config": shared}]
    + [note("a", f"a{i}", i) for i in range(1, 5)]
    + [note("b", "b1", 5)]
)
eq(
    "room-actor-shares-default-room-wide",
    [x["decision"].get("reason", x["decision"]["status"]) for x in r[2:]],
    ["accepted"] * 4 + ["room_budget"],
)
# Tessa's reproducer: A's notices reach two recipients; a mention share keeps two of them from using B's mentions.
q = compile_text(
    "room-mention-share",
    "roles: [a, b, lead, witness]\n",
    "contract @30m => expired\ntype notice\ntype close\nstate new\n  lead -> {a,b}: close() => complete\nroom\n  event a -> {lead,witness}: notice()\n  event b -> lead: notice()\nend complete\n",
)
fan = {
    **room_config,
    "bindings": {"a": "a", "b": "b", "lead": "l", "witness": "w"},
    "max_mentions": 4,
    "max_mentions_per_actor": 3,
}
validators["room_config"].validate(fan)
r = run(
    [
        q,
        {"op": "room_new", "contract": "c", "handle": "r", "config": fan},
        note("a", "a1", 0),
        note("a", "a2", 1),
        note("b", "b1", 2),
        note("a", "a2", 1000),
    ]
)
eq(
    "room-mention-share",
    [x["decision"].get("reason", x["decision"]["status"]) for x in r[2:]],
    ["accepted", "room_budget", "accepted", "accepted"],
)
# M1 (with Omar's redaction): another member's failure in a collection the viewer does not receive keeps no control ID in its checkpoint.
r = run(
    setup
    + [
        start,
        web,
        {"op": "view", "handle": "s", "actor": "sec", "now": now + 2},
        {"op": "view", "handle": "s", "actor": "l", "now": now + 2},
    ]
)
peer = r[4]["ok"]["checkpoint"]["state"]
lead_cp = r[5]["ok"]["checkpoint"]["state"]
opaque = peer["collections"]["reports"]["members"]["reviewers/frontend"]["failure"][
    "id"
]
eq(
    "peer-checkpoint-failure-id-opaque",
    [
        opaque.startswith("redacted:"),
        len(opaque),
        "frontend-stop" in json.dumps(peer),
        opaque in peer["contribution_receipts"],
    ],
    [True, 41, False, True],
)
hidden = peer["collections"]["reports"]["members"]["reviewers/frontend"]["failure"]
eq(
    "peer-checkpoint-hidden-failure-shape",
    [
        hidden["type"],
        hidden["reference"],
        hidden["message"],
        peer["contribution_receipts"][opaque]["fingerprint"],
    ],
    ["redacted", None, "redacted", "0" * 64],
)
eq(
    "receiver-checkpoint-keeps-failure-id",
    lead_cp["collections"]["reports"]["members"]["reviewers/frontend"]["failure"]["id"],
    "frontend-stop",
)
# K1: a control_capacity rejection still appends the exhaustion record and ends the session (version +1).
q = compile_text(
    "control-exhaustion",
    "roles: [lead, worker]\nextensions: [host.presence/1]\n",
    "contract @1h => expired\n" + work + "state working\n" + pause,
)
r = run(
    [
        q,
        new(duo, capabilities=["host.presence/1"], max_controls=2),
        step("w", "claim", "claim", {}, 0),
        presence(1, "worker", "w", "offline", 1000),
        presence(2, "worker", "w", "online", 2000),
    ]
)
s = r[-1]["state"]["state"]
eq(
    "control-capacity-rejection-records-exhaustion",
    [
        r[-1]["decision"].get("reason"),
        s["outcome"],
        s["version"] - r[-2]["state"]["state"]["version"],
    ],
    ["control_capacity", "control_capacity_exhausted", 1],
)
# M8: a settled member cannot renew, and a renewal leaves an untimed continuation the renewed window before max_at.
renew = "contract @30m max @4h => expired\ntype request\ntype review\ntype heartbeat\ntype summary\nstate new\n  lead -> {a,b}: request() => reviewing\nstate reviewing\n  event {a,b} -> lead: heartbeat() => @self\n  collect reports from {a,b} -> lead @10m => summarizing\n    reply review()\nstate summarizing\n  lead -> {a,b}: summary() => complete\nend complete\n"
q = compile_text(
    "renewal-limits", "roles: [lead, a, b]\nextensions: [room.contributions/1]\n", renew
)
r = run(
    [
        q,
        new(pair, **contrib),
        step("l", "request", "request", {}, 0),
        step("pa", "ra", "review", {}, 60000),
        step("pa", "ha", "heartbeat", {}, 120000),
        step("pb", "hb1", "heartbeat", {}, 540000),
        step("pb", "hb2", "heartbeat", {}, 660000),
    ]
)
eq(
    "renewal-settled-and-untimed-window",
    [x["decision"].get("reason", x["decision"]["status"]) for x in r[3:]],
    ["accepted", "already_contributed", "accepted", "insufficient_time"],
)
eq(
    "renewal-close-leaves-summary-window",
    r[-1]["state"]["state"]["collections"]["reports"]["deadline_at"],
    now + 1140000,
)
# DF-07 (D13): one timed event both acknowledges (its own duty) and renews the following timed rule (@self).
# The adversary opens the round, so its critique is an owed report and stays a timed rule: as the reply to an
# owner's request it would be an alternative, and @self would renew the reply wait instead (renewable-timeouts.md).
ackrenew = "contract @12h => expired\ntype round1\ntype progress\ntype critique\nstate new\n  adversary -> owner: round1() => round1\nstate round1 @4h => stalled\n  event adversary -> owner: progress() @15m => unacknowledged @self\n  adversary -> owner: critique() @1h => folding | stalled\nstate folding\n  owner -> adversary: round1() => complete\nend complete\nend unacknowledged\nend stalled\n"
q = compile_text("timed-renewal-event", "roles: [owner, adversary]\n", ackrenew)
roles = {"owner": "o", "adversary": "a"}
r = run(
    [
        q,
        new(roles),
        step("a", "r1", "round1", {}, 0),
        step("a", "p1", "progress", {}, 300000),
        tick(900000),
        tick(3660000),
        tick(3900000),
    ]
)
heartbeat, critique = r[0]["ok"]["states"]["round1"]["rules"]
duties = r[0]["ok"]["states"]["round1"]["deadlines"]
eq(
    "timed-renewal-event-compiles",
    [
        heartbeat["event"],
        heartbeat["reset"].get("scope"),
        heartbeat["reset"].get("rule") == critique["id"],
        [(d["kind"], d["after_ms"], d["outcome"]) for d in duties],
    ],
    [
        True,
        "rule",
        True,
        [("progress", 900000, "unacknowledged"), ("critique", 3600000, "stalled")],
    ],
)
eq(
    "timed-renewal-event-acks-and-renews",
    [
        r[3]["decision"]["status"],
        r[4]["state"]["state"]["outcome"],
        r[5]["state"]["state"]["outcome"],
        r[6]["state"]["state"]["outcome"],
    ],
    ["accepted", None, None, "stalled"],
)
r = run([q, new(roles), step("a", "r1", "round1", {}, 0), tick(900000)])
eq(
    "timed-renewal-event-silence-ends",
    r[-1]["state"]["state"]["outcome"],
    "unacknowledged",
)
# M9 follow-up (set N B2): message_budget stays reachable. The closing reserve binds events only, so a transition may
# spend the last unit; the next proposal is then message_budget (step 9) before any reserve check (step 10).
# The sends are one-way: a reply head would reserve its reply's unit, so it could not spend the last one.
q = compile_text(
    "message-budget-reachable",
    "roles: [a, b]\n",
    "contract @1h => expired\ntype ping\ntype close\ntype note\nstate new\n  a -> b: ping() => waiting\nstate waiting\n  event a -> b: note()\n  a -> b: close() => done\nend done\n",
)
r = run(
    [
        q,
        new({"a": "pa", "b": "pb"}, max_messages=1),
        step("pa", "p1", "ping", {}, 0),
        step("pa", "c1", "close", {}, 1),
        step("pa", "n1", "note", {}, 2),
    ]
)
eq(
    "message-budget-after-transition",
    [x["decision"].get("reason", x["decision"]["status"]) for x in r[2:]]
    + [r[-1]["state"]["state"]["status"]],
    ["accepted", "message_budget", "message_budget", "active"],
)
# With one unit left, the event is completion_capacity instead: it may not take the closing message's unit.
r = run(
    [
        q,
        new({"a": "pa", "b": "pb"}, max_messages=2),
        step("pa", "p1", "ping", {}, 0),
        step("pa", "n1", "note", {}, 1),
        step("pa", "c1", "close", {}, 2),
    ]
)
eq(
    "event-keeps-closing-unit",
    [x["decision"].get("reason", x["decision"]["status"]) for x in r[2:]]
    + [r[-1]["state"]["state"]["outcome"]],
    ["accepted", "completion_capacity", "accepted", "done"],
)
# With no message path from the state to a terminal the closing reserve is zero, so events spend the last unit too.
q = compile_text(
    "events-reach-message-budget",
    "roles: [a, b]\n",
    "contract @1h => expired\ntype start\ntype note\nstate new\n  a -> b: start() => looping\nstate looping\n  event a -> b: note()\n",
)
r = run(
    [
        q,
        new({"a": "pa", "b": "pb"}, max_messages=2),
        step("pa", "s1", "start", {}, 0),
        step("pa", "n1", "note", {}, 1),
        step("pa", "n2", "note", {}, 2),
    ]
)
eq(
    "events-reach-message-budget",
    [x["decision"].get("reason", x["decision"]["status"]) for x in r[2:]]
    + [r[-1]["state"]["state"]["status"]],
    ["accepted", "accepted", "message_budget", "active"],
)
# Set O (Sofia's LOW residuals at 3a5e7bc).
# L6: the whole bindings object is bounded to 262,144 canonical bytes even when every member is within its own bounds.
roster_case(
    "total-under-262144",
    lambda b: b.update(
        reviewers=[{**roster_member(i), "guidance": "g" * 16000} for i in range(16)]
    ),
    "ok",
)
roster_case(
    "total-over-262144",
    lambda b: b.update(
        reviewers=[{**roster_member(i), "guidance": "g" * 16000} for i in range(17)]
    ),
    {"error": "invalid_bindings"},
)
# M1/M10: the redacted: prefix is reserved for checker placeholders, so a member or host report may not use it.
r = run(
    setup
    + [
        start,
        {**web, "report": {**failure, "id": "redacted:" + "0" * 32}},
        {**own, "report": {**failure, "id": "redacted:x"}},
    ]
)
eq(
    "reserved-redacted-prefix",
    [r[3]["decision"], r[4]["decision"], r[4]["state"]["state"]["version"]],
    [{"status": "rejected", "reason": "invalid_contribution"}] * 2
    + [r[2]["state"]["state"]["version"]],
)
# M11: a duty carried in by a nonterminal host transition (still serviceable there by an event) ends when the collection closes.
carried = "contract @1h => expired\ntype request\ntype note\ntype review\ntype summary\nstate new\n  lead -> {a,b}: request() => working\nstate working\n  lead -> a: note() @10m => done | note_missing\n  on b offline for 1m => reviewing\nstate reviewing\n  event lead -> a: note()\n  collect reports from {a,b} -> lead @2m => summarizing\n    reply review()\nstate summarizing\n  lead -> {a,b}: summary() => complete\nend done\nend note_missing\nend complete\n"
q = compile_text(
    "carried-duty-close",
    "roles: [lead, a, b]\nextensions: [room.contributions/1, host.presence/1]\n",
    carried,
)
r = run(
    [
        q,
        new(pair, **cfg),
        step("l", "request", "request", {}, 0),
        presence(1, "b", "pb", "offline", 1000),
        tick(61000),
        tick(181000),
        tick(700000),
    ]
)
s = r[4]["state"]["state"]
eq(
    "host-transition-carries-duty",
    [s["state"], [(d["kind"], d["due_at"]) for d in s["obligations"]]],
    ["reviewing", [("note", now + 600000)]],
)
s = r[5]["state"]["state"]
eq(
    "collection-close-drops-carried-duty",
    [s["state"], s["obligations"]],
    ["summarizing", []],
)
eq(
    "carried-duty-never-fires",
    [r[6]["state"]["state"]["status"], r[6]["state"]["state"]["state"]],
    ["active", "summarizing"],
)
# N4: a reference must name a fact its sender authored or received; a bound ID it can read is not enough.
# One-way: the verdict goes to the observer, not back to the author, because run() validates against
# protocol/5, which has no reply heads (contract.replies/1 is protocol/6).
refs = "contract @30m => expired\ntype draft\ntype comment\n  source: ref(draft)\ntype verdict\n  source: ref(draft)\nstate new\n  author -> reviewer: draft() as d => reviewing\nstate reviewing\n  observer -> author: comment(source=$d) => done\n  reviewer -> observer: verdict(source=$d) => done\nend done\n"
q = compile_text("hidden-reference", "roles: [author, reviewer, observer]\n", refs)
r = run(
    [
        q,
        new({"author": "au", "reviewer": "rv", "observer": "ob"}),
        step("au", "d1", "draft", {}, 0),
        step("ob", "c1", "comment", {"source": "d1"}, 1),
        step("rv", "v1", "verdict", {"source": "d1"}, 2),
    ]
)
eq(
    "hidden-reference-rejected",
    [r[3]["decision"].get("reason"), r[3]["state"]["state"]["version"]],
    ["reference", 1],
)
eq(
    "received-reference-accepted",
    [r[4]["decision"]["status"], r[4]["state"]["state"]["outcome"]],
    ["accepted", "done"],
)
jsonschema.Draft202012Validator.check_schema(schema)
eq(
    "schema-reproducible",
    json.loads((ROOT / "spec/schemas/protocol-contributions.schema.json").read_text()),
    schema,
)
# Every check names the v2 requirements it evidences (spec M24, v2trace.py).
requirements = verify_tags("checkcoordination", checks + [p["name"] for p in pending])
report = {
    "passed": True,
    "assertions": len(checks),
    "pending": pending,
    "requests": sum(len(t["requests"]) for t in traces),
    "schema_definitions": len(validators),
    "checks": checks,
    "requirements": {n: requirements[n] for n in checks},
    "traces": traces,
}
path = ROOT / "spec/conformance/coordination-report.json"
path.write_text(json.dumps(report, indent=2) + "\n")
sys.stdout.write(
    str(json.dumps({k: v for k, v in report.items() if k != "traces"})) + "\n"
)
for p in pending:
    sys.stdout.write(
        " ".join(
            str(value)
            for value in (
                "PENDING",
                p["name"],
                "waiting for",
                p["waiting_for"],
                "expected",
                p["expected"],
                "actual",
                p["actual"],
            )
        )
        + "\n"
    )
