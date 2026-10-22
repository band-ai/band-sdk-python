"""Independent semantic assertions and wire-schema checks for the new profile."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from typing import Any

import jsonschema

from contracts.spec.tools.buildparticipantschemas import root as participant_schema
from contracts.spec.tools.buildv2schemas import root as schema
from contracts.spec.tools.generatevectors import adapter, pointer
from contracts.spec.tools.h7sharednamespace import PATH as h7_path
from contracts.spec.tools.h7sharednamespace import check as check_h7
from contracts.spec.tools.negativevectorsm21 import cases as fail_closed_cases
from contracts.spec.tools.negativevectorsm22 import cases as safety_cases
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT
from contracts.spec.tools.uncheckedmarkers import PATH as unchecked_path
from contracts.spec.tools.uncheckedmarkers import check as check_unchecked
from contracts.spec.tools.v2trace import verify as verify_tags
from contracts.spec.tools.v2unknownengine import PATH as unknown_engine_path
from contracts.spec.tools.v2unknownengine import check as check_unknown_engine
from contracts.spec.tools.vectorinputs import file_source, hosted, trusted_policy
from contracts.spec.tools.vectorinputs import source as corpus_source

catalog = [
    (
        "V2-ID",
        "file-identity.md",
        "A session MUST pin the exact UTF-8 file hash; author version does not upgrade a session.",
    ),
    (
        "V2-FAIL-CLOSED",
        "README.md",
        "Unknown engine identifiers and unsupported state, checkpoint or view profiles MUST be rejected; implementations MUST NOT infer a profile from field shape.",
    ),
    (
        "V2-TEXT",
        "text-v2.md",
        "Every text field MUST have a UTF-8 byte bound; grapheme bounds are optional additional constraints.",
    ),
    (
        "V2-PATTERN",
        "safe-patterns.md",
        "Authored patterns MUST use the bounded DFA profile and MUST NOT execute a backtracking matcher.",
    ),
    (
        "V2-PATTERN-LITERAL",
        "safe-patterns.md",
        "Patterns MUST be raw ~r literals that preserve pattern bytes; uppercase ~R and quoted-string patterns are syntax errors.",
    ),
    (
        "V2-PROMPT",
        "multi-agent-skills.md",
        "Ordinary one-line and multiline strings MUST attach prompt metadata to the next item, preserve it in agent views, and never become executable admission predicates.",
    ),
    (
        "V2-DOC-SIGIL",
        "sigils-v2.md",
        "Raw ~s documentation MUST preserve literal contents and query metadata without evaluating expressions or changing existing session pins; uppercase ~S is a syntax error.",
    ),
    (
        "V2-WORD-SIGIL",
        "sigils-v2.md",
        "Raw ~w lists MUST preserve ordered names and existing enum, reference and selected-role semantics without creating atoms or changing existing session pins; uppercase ~W is a syntax error.",
    ),
    (
        "V2-LOWERCASE",
        "lowercase-v2.md",
        "Body keywords and rule options MUST be lowercase; uppercase aliases and plus-prefixed declarations are syntax errors, identifiers and literal contents are never rewritten.",
    ),
    (
        "V2-ENTRY",
        "lifecycle-v2.md",
        "The compiler MUST reject every transition back to new.",
    ),
    (
        "V2-DUE",
        "lifecycle-v2.md",
        "The due ... with ... in form is a syntax error; a duty MUST be written as @duration on the transition or event rule that discharges it.",
    ),
    (
        "V2-TIMED-RULE",
        "timeouts-v2.md",
        "A state-local timed transition or timed event MUST derive a matching duty; each requires all senders and its absence permits any sender. A timed event never transitions.",
    ),
    (
        "V2-TIMEOUT-SCOPES",
        "timeouts-v2.md",
        "State caps MUST use saved entry time and whole-run caps MUST use creation time; retries and restore MUST NOT reset either clock.",
    ),
    (
        "V2-TIE-ORDER",
        "timeouts-v2.md",
        "Timers due at the same instant MUST fire in the equal-time order: work duty, collection deadline, offline grace, state timeout, host trigger, whole-run cap; IDs compare lexicographically within a category, so due-10 fires before due-2.",
    ),
    (
        "V2-RENEWAL",
        "renewable-timeouts.md",
        "A renewable contract clock MUST keep its current window max_at apart from its fixed maximum clocks.hard_at: an accepted renewal sets max_at to the earlier of acceptance time plus the window and hard_at, nothing moves hard_at, admission tests new duties against max_at, restore reconstructs both, and an infinite window with a finite maximum fails compilation.",
    ),
    (
        "V2-RENEWAL-FIT",
        "renewable-timeouts.md",
        "Only a sender that still waits MUST be able to renew a rule or collection, and the renewal MUST leave the state it leads to a whole window before max_at; a settled member is refused already_contributed and a renewal that does not fit is refused insufficient_time.",
    ),
    (
        "V2-TIMER-TARGET",
        "renewable-timeouts.md",
        "A misplaced renewal target MUST fail compilation with timer_target at its rule, including @state in a state without a declared clock and @contract unless the contract line declares a finite window with max.",
    ),
    (
        "V2-TIMED-RENEWAL",
        "renewable-timeouts.md",
        "A timed event that ends with @self MUST compile to one rule carrying its own derived duty and an @self renewal of the immediately following timed rule or collect; an accepted event MUST discharge that duty and renew that target, and silence MUST still end the session with the event outcome at its deadline.",
    ),
    (
        "V2-DEFAULTS",
        "defaults-v2.md",
        "Every contract MUST declare a whole-run clock, contract @<duration> or the explicit contract @infinity opt-out; a missing clock fails compilation with contract_clock_required. There is no generic idle expiry.",
    ),
    (
        "V2-CAPACITY",
        "defaults-v2.md",
        "Admission MUST preserve known remaining duty message capacity; mention caps apply per message, with no session budget.",
    ),
    (
        "V2-HUMAN",
        "agents-and-extensions.md",
        "Humans and agent-human conversation MUST remain outside contract admission.",
    ),
    (
        "V2-POLICY",
        "agents-and-extensions.md",
        "The host MUST verify agent bindings and current protected room assignments.",
    ),
    (
        "V2-EXT",
        "agents-and-extensions.md",
        "Every declared extension MUST be explicitly supported by the host.",
    ),
    (
        "V2-PRESENCE",
        "host-signals.md",
        "Presence continuity MUST survive restart and MUST be interrupted by unknown or online observations.",
    ),
    (
        "V2-REMOVAL",
        "host-signals.md",
        "With host.membership/1 enabled, removal of a bound agent MUST terminate the session immediately.",
    ),
    (
        "V2-CONTROL",
        "host-signals.md",
        "Presence and membership host events MUST use independent bounded control accounting (max_controls) with a reserved terminal record; contribution controls never consume it.",
    ),
    (
        "V2-HOST-ORDER",
        "host-signals.md",
        "Host events MUST pass the documented check order: sequences strictly increase from signal_revision (1 after an initial_presence snapshot, otherwise 0), an older or equal sequence or a reducer time behind the stored now is stale_host_event, and a changed event under an accepted ID is id_conflict. A rejection before the timer step leaves the state unchanged; a terminal rejection returns the state after the elapsed timers, and a control_capacity rejection appends the exhaustion record.",
    ),
    (
        "V2-RESTORE",
        "host-signals.md",
        "Profile 2 checkpoints MUST preserve exact identity, original duties, host sequence and control receipts.",
    ),
    (
        "V2-ROSTER",
        "coordination.md",
        "A roster MUST be immutable for its run; actor IDs MUST be unique across all roles and groups, and an empty collection is rejected.",
    ),
    (
        "V2-COLLECTION",
        "coordination.md",
        "A collection result MUST record each member's status and closing reason; partial or zero success does not by itself fail the session, and closed results survive later states and portable recovery.",
    ),
    (
        "V2-REPORT-VISIBILITY",
        "coordination.md",
        "A collection's receivers MUST see every accepted report; a contributor's role view MUST show only its own status and facts, never another contributor's report.",
    ),
    (
        "V2-CONTRIBUTION",
        "contribution-outcomes.md",
        "With room.contributions/1 enabled before creation, a member stop or host failure report MUST identify the active collection entry and a pending member; it settles that member as failed with its original cause, never as a business message.",
    ),
    (
        "V2-STATE5",
        "contribution-outcomes.md",
        "Sessions with room.contributions/1 MUST persist band-contract/state/5; a reader that does not support the contribution feature or state/5 MUST reject them instead of dropping fields.",
    ),
    (
        "V2-ID-NAMESPACE",
        "engine.md",
        "Message IDs MUST share one session-wide namespace: another actor's reuse of an accepted ID is id_conflict, changes no state and cannot claim the original actor's duplicate receipt.",
    ),
    (
        "V2-CONFIG",
        "engine.md",
        "The session config MUST be closed: any member other than the documented config members fails creation with invalid_config before the other creation checks, and a misspelled member is never replaced by a default.",
    ),
    (
        "V2-REFERENCE",
        "engine.md",
        "A field=$binding reference MUST name the currently bound fact, and its sender MUST be that fact's author or an accepted recipient; a bound ID the sender can read but never received is rejected with reference.",
    ),
    (
        "V2-VERSION",
        "engine.md",
        "The state version MUST count durable records, not transitions: one per accepted fact, host-control record, collection log row and the expiry; entering a state adds nothing and restore verifies the count.",
    ),
    (
        "V2-PREFLIGHT",
        "engine.md",
        "An agent's optional local check MUST run the reducer on its verified agent-view projection and discard the candidate state; a local acceptance still requires final room admission.",
    ),
    (
        "V2-ROOM-SHARE",
        "engine.md",
        "Optional per-actor room-event shares MUST default to the room-wide limits; a sender past its event or mention share is refused room_budget and one holding its receipt share retention_limit, while the room-wide limits still apply and the event and mention shares reset with the window.",
    ),
]

# Traceability for each catalog row (spec M24): every row is MUST; profiles and
# acceptance obligations follow requirements.json. Acceptance lists what an
# implementation must demonstrate, not which checks exist today.
C, E, D, R = "compiler/2", "session-engine/2", "durable-room/1", "band-room/1"
trace = {
    "V2-ID": (
        (C, E),
        (
            "Compile the same bytes twice and get the same SHA-256 pin; change one byte, including only the author version, and get a different pin.",
            "Restore a session against a file whose hash differs from its pin and observe rejection; the session is never upgraded in place.",
        ),
    ),
    "V2-FAIL-CLOSED": (
        (C, E),
        (
            "Compile a source with an unknown engine identifier and observe rejection.",
            "Restore a checkpoint or read a view with an unsupported state, checkpoint or view profile and observe rejection.",
            "Restore a checkpoint labelled state/2 that carries state/4 members and observe rejection, not a profile inferred from field shape.",
        ),
    ),
    "V2-TEXT": (
        (C, E),
        (
            "Compile text fields with omitted, infinite, reversed, overflowing and grapheme-only byte bounds and observe each rejected.",
            "Admit a payload at exactly the byte bound and reject one byte more, including multibyte UTF-8 at the boundary.",
            "When a grapheme bound is declared, enforce it in addition to the byte bound.",
        ),
    ),
    "V2-PATTERN": (
        (C, E),
        (
            "Compile patterns outside the bounded DFA profile or over the compile limits and observe rejection.",
            "Match admitted and rejected payloads within the documented work limits, with no backtracking matcher on the path.",
        ),
    ),
    "V2-PATTERN-LITERAL": (
        (C,),
        (
            "Compile ~r literals with each accepted delimiter and confirm the stored pattern bytes equal the authored bytes; a single backslash stays single.",
            "Compile an uppercase ~R literal and a quoted-string pattern and observe syntax errors.",
        ),
    ),
    "V2-PROMPT": (
        (C, E),
        (
            "Attach one-line and multiline strings to a role, type, state and rule, and find each in queries and the agent view.",
            "Add or remove prompt strings and confirm the behavior digest and admission decisions do not change.",
        ),
    ),
    "V2-DOC-SIGIL": (
        (C,),
        (
            "Query ~s documentation that contains expression-like text and get the literal bytes back unevaluated.",
            "Change only ~s text: behavior is unchanged, the file pin changes and existing sessions keep their pin.",
            "Compile an uppercase ~S literal and observe a syntax error.",
        ),
    ),
    "V2-WORD-SIGIL": (
        (C, E),
        (
            "Use ~w for an enum, a reference type list and a role group, and confirm order and semantics equal the plain-list form.",
            "Confirm ~w names create no atoms or symbols, including on rejected input.",
            "Compile an uppercase ~W literal and observe a syntax error.",
        ),
    ),
    "V2-LOWERCASE": (
        (C,),
        (
            "Compile uppercase body keywords, uppercase rule options and plus-prefixed declarations and observe syntax errors.",
            "Confirm identifiers and literal contents that contain uppercase characters are preserved byte for byte.",
        ),
    ),
    "V2-ENTRY": (
        (C,),
        ("Compile every transition form whose target is new and observe rejection.",),
    ),
    "V2-DUE": (
        (C,),
        (
            "Compile the due ... with ... in form and observe a syntax error.",
            "Compile a duty written as @duration on the discharging transition or event rule and find the derived duty in the compiled contract.",
        ),
    ),
    "V2-TIMED-RULE": (
        (C, E),
        (
            "Compile a state-local timed transition and a timed event and confirm each derives a matching duty that requires all senders.",
            "Confirm a rule without a duration permits any sender and a timed event never transitions.",
        ),
    ),
    "V2-TIMEOUT-SCOPES": (
        (E, D),
        (
            "Expire a state cap from its saved entry time and a whole-run cap from creation time, including the initial wait.",
            "Restart and retry before expiry and confirm neither clock resets.",
        ),
    ),
    "V2-TIE-ORDER": (
        (E,),
        (
            "Make duties due-2 and due-10 fall due at the same instant and confirm due-10 fires first, not the lower number or the earlier declaration.",
            "Make timers of each adjacent category pair fall due together and confirm the category order, with the remaining due timers cascading before any proposal is admitted.",
        ),
    ),
    "V2-RENEWAL": (
        (C, E, D),
        (
            "Renew a contract @W max @M window and confirm max_at moves to acceptance plus W while clocks.hard_at stays at creation plus M, including after restore.",
            "Introduce a duty that does not fit before max_at and observe insufficient_time; one that fits is accepted.",
            "Compile contract @infinity max @M and observe a duration error at the contract line.",
        ),
    ),
    "V2-RENEWAL-FIT": (
        (E,),
        (
            "Ask a collection member that already replied to renew and observe already_contributed.",
            "Renew a collection so its close would leave the untimed next state less than a whole window before max_at and observe insufficient_time, with the deadline left at the last renewal that fit.",
        ),
    ),
    "V2-TIMER-TARGET": (
        (C,),
        (
            "Compile @state in a state without a declared clock and observe timer_target at that rule's line.",
            "Compile @contract under contract @30m and under contract @infinity and observe timer_target at that rule's line.",
        ),
    ),
    "V2-TIMED-RENEWAL": (
        (C, E),
        (
            "Compile event x -> y: k() @15m => outcome @self directly before a timed rule and confirm one event rule carries both the derived duty and an @self reset that names the following rule.",
            "Accept that event before its deadline and confirm the session outlives the event deadline and the following duty falls due one window after the acceptance; send nothing and observe the event outcome at its deadline.",
        ),
    ),
    "V2-DEFAULTS": (
        (C, E),
        (
            "Compile a contract without a clock and observe contract_clock_required; compile contract @infinity => expired and observe success.",
            "Leave a session idle and confirm no generic idle expiry occurs.",
        ),
    ),
    "V2-CAPACITY": (
        (E,),
        (
            "Refuse new work when the remaining duty message capacity cannot fit the owed answer.",
            "Apply a contract-level mention cap and a rule override per message and confirm there is no session mention budget.",
        ),
    ),
    "V2-HUMAN": (
        (E, R),
        (
            "Bind a human to a contract role at creation and on a step and observe refusal.",
            "Exchange agent-human messages and confirm they never enter contract admission.",
        ),
    ),
    "V2-POLICY": (
        (E, R),
        (
            "Omit fresh trusted room policy at creation, on a step, on an exact retry and on a view, and observe refusal.",
            "Use a revoked member and a mismatched fixed role assignment and observe a refusal that admits nothing.",
            "Supply empty, mixed and unknown policy evidence and observe a fail-closed refusal.",
        ),
    ),
    "V2-EXT": (
        (C, E),
        (
            "Declare an extension the host does not support and observe unsupported_extension at creation.",
            "Send a host event whose kind needs an extension the session did not enable and observe unsupported_extension.",
        ),
    ),
    "V2-PRESENCE": (
        (E, D),
        (
            "Restart during an offline dwell and confirm the dwell transition fires at its original time.",
            "Observe online, and separately unknown, during a dwell; each interrupts continuity and a later offline starts a new period.",
        ),
    ),
    "V2-REMOVAL": (
        (E,),
        (
            "With host.membership/1, remove a bound agent and confirm immediate termination, including when only the final control record remains.",
            "Without host.membership/1, confirm a membership event is refused with unsupported_extension.",
        ),
    ),
    "V2-CONTROL": (
        (E,),
        (
            "Create with max_controls outside 0-1024, or below 2 with a signal extension, and observe control_budget_required.",
            "Spend control records with presence or membership events until only the final record remains; the next non-removal event is rejected with control_capacity and records control_capacity_exhausted.",
            "Retry an exact host event and confirm it consumes no further record.",
        ),
    ),
    "V2-HOST-ORDER": (
        (E,),
        (
            "With an initial_presence snapshot, send sequence 1 and observe stale_host_event, then sequence 2 accepted; without a snapshot, sequence 1 is accepted.",
            "Send an equal sequence, an older sequence, and an event whose reducer time is behind the stored now, and observe stale_host_event for each.",
            "Send a changed event under an accepted ID and observe id_conflict.",
            "Confirm each rejection before the timer step, such as stale_host_event and id_conflict, leaves the whole state unchanged.",
            "Send a host event after an elapsed timer ended the session and observe terminal with the state those timers produced; a control_capacity rejection instead appends the exhaustion record.",
        ),
    ),
    "V2-RESTORE": (
        (E, D),
        (
            "Checkpoint and restore mid-session and confirm file identity, original duties, host sequence and control receipts are preserved exactly.",
            "Retry an exact host event after restore and confirm the original receipt is returned.",
        ),
    ),
    "V2-ROSTER": (
        (C, E, "band-contract/roster/1", "band-contract/roster/2"),
        (
            "Create a session with an actor ID repeated across roles or groups, and one with an empty collection, and observe each rejected.",
            "Restore a typed roster and confirm attributes are revalidated, the roster is re-instantiated and the binding match is verified.",
        ),
    ),
    "V2-COLLECTION": (
        (E, D, "band-contract/collection/1", "band-contract/collection/2"),
        (
            "Close a collection by settlement, by deadline and by offline grace, and confirm each member status, settlement time and reason.",
            "Close a collection with zero successes and confirm the session stays active.",
            "Restore after close and confirm the result is unchanged and a reconnect does not reopen it.",
        ),
    ),
    "V2-REPORT-VISIBILITY": (
        (E, "band-contract/collection/2"),
        (
            "Confirm a collection receiver's view shows an accepted report's payload.",
            "Confirm another contributor's view does not list that report.",
        ),
    ),
    "V2-CONTRIBUTION": (
        (E, "band-contract/contribution/1", "band-contract/collection/2"),
        (
            "Confirm a member can stop its own contribution but not another member's.",
            "Send reports for a wrong session, a stale round, an unrelated member and an already-settled contribution and observe each rejected.",
            "At the exact deadline the deadline settles first; whichever of a reply or a terminal failure wins first cannot be replaced.",
            "Retry an exact control before and after restart and confirm a single receipt with the original cause.",
        ),
    ),
    "V2-STATE5": (
        (E, D, "band-contract/contribution/1"),
        (
            "Export and restore a state/5 checkpoint and confirm member statuses, failure causes and control receipts survive.",
            "Give contribution compiled data and a state/5 checkpoint to a reader without that support and observe rejection.",
        ),
    ),
    "V2-ID-NAMESPACE": (
        (E,),
        (
            "After actor B's ID X is accepted, send actor A's proposal with X and observe id_conflict with the state unchanged.",
            "Retry B's exact envelope and confirm the duplicate returns B's original receipt.",
        ),
    ),
    "V2-CONFIG": (
        (E,),
        (
            "Create with a misspelled cap key and observe invalid_config, and confirm the config schema rejects it.",
        ),
    ),
    "V2-REFERENCE": (
        (E,),
        (
            "Reference a bound fact from a participant that neither authored nor received it and observe reference with the version unchanged.",
            "Reference the same fact from its recipient and observe acceptance.",
        ),
    ),
    "V2-VERSION": (
        (E,),
        (
            "Settle a collection's last member with a reply and confirm the version adds the fact and the round close.",
            "Accept presence reports and a fired host trigger and confirm each adds one, while entering the paused state adds nothing.",
        ),
    ),
    "V2-PREFLIGHT": (
        (E,),
        (
            "Run the optional checker on a contributor's agent-view projection with a legal reply and confirm accepted.",
        ),
    ),
    "V2-ROOM-SHARE": (
        ("room-events/1",),
        (
            "With per-actor shares, a sender past its event share gets room_budget while another sender is accepted; after the window resets it may send again, and a sender holding its receipt share gets retention_limit.",
            "Without per-actor shares, one sender may use the whole room-wide window, and the room-wide limit refuses the next event with room_budget.",
            "A fan-out past the sender's mention share gets room_budget while another sender's event is accepted.",
        ),
    ),
}


def swap(text: str, old: str, new: str) -> str:
    # Every source mutation must change the source: an absent needle would compare a contract with itself (impl M6).
    if old not in text:
        raise ValueError(f"mutation needle not found: {old!r}")
    return new.join(text.split(old))


TYPED_ERRORS = {
    "new": "creation_error",
    "restore": "restore_error",
    "wire": "wire_error",
    "room_new": "room_creation_error",
}


def typed_error(
    validators: dict[str, jsonschema.Draft202012Validator], request: Any, response: Any
) -> None:
    # Every error a v2 operation returns is one of its enumerated codes (spec M19, Omar N-3); a
    # compile error is a bare loader code or a {code, line, detail} object, never an array.
    if "error" not in response:
        return
    if request["op"] in TYPED_ERRORS:
        validators[TYPED_ERRORS[request["op"]]].validate(response)
    elif request["op"] == "compile":
        if isinstance(response["error"], str):
            validators["loader_error"].validate(response)
        else:
            validators["compiler_error"].validate(response["error"])


def source(raw: str) -> dict[str, Any]:
    return {
        "profile": "band-contract/file-source/1",
        "format": "frontmatter",
        "content": raw,
        "sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }


def setup(raw: str, **changes: Any) -> list[dict[str, Any]]:
    # A host that supports reply alternatives says so; assistant-answer needs it.
    cfg = {
        "session": "s",
        "bindings": {"requester": "r", "assistant": "a"},
        "now": 0,
        "max_messages": 2,
        "capabilities": ["contract.replies/1"],
        **changes,
    }
    return [
        {"op": "compile", "handle": "c", "source": source(raw)},
        {"op": "new", "contract": "c", "handle": "s", "config": cfg},
    ]


def step(
    actor: str,
    ident: str,
    kind: str,
    at: int = 0,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "op": "step",
        "handle": "s",
        "actor": actor,
        "now": at,
        "message": {
            "id": ident,
            "session": "s",
            "kind": kind,
            "payload": payload or {"text": "hello"},
        },
    }


# assistant-answer's answer is a reply alternative: it names the question it answers.
def answer_step(at: int = 1, question: str = "q") -> dict[str, Any]:
    return step("a", "a", "answer", at, {"question": question, "text": "hello"})


def signal(
    seq: int, value: Any, at: int, subject: str = "assistant", principal: str = "a"
) -> dict[str, Any]:
    return {
        "op": "host_signal",
        "handle": "s",
        "now": at,
        "event": {
            "id": f"h{seq}",
            "sequence": seq,
            "subject": subject,
            "principal": principal,
            "kind": "membership" if value == "removed" else "presence",
            "value": value,
            "at": at,
        },
    }


def main() -> None:
    path = ROOT / "spec/schemas/protocol-v2.schema.json"
    assert json.loads(path.read_text()) == schema
    jsonschema.Draft202012Validator.check_schema(schema)
    validators = {
        k: jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/" + k})
        for k in schema["$defs"]
    }
    # The examples write requests with reply alternatives (contract.replies/1), so their compiled
    # objects, states and views follow protocol/6, the superset that adds the reply wait.
    replies = json.loads(
        (ROOT / "spec/schemas/protocol-replies.schema.json").read_text()
    )
    v2_validators = validators
    reply_validators = {
        k: jsonschema.Draft202012Validator({**replies, "$ref": "#/$defs/" + k})
        for k in replies["$defs"]
    }
    checks = []

    def eq(name: str, actual: Any, expected: Any) -> None:
        assert actual == expected, (name, actual, expected)
        checks.append(name)

    def compile_error(name: str, response: Any, code: str, line: int) -> None:
        # Portable conformance compares a compiler error's code and line exactly (contract-dsl.md, spec M10).
        validators["compiler_error"].validate(response["error"])
        eq(name, (response["error"]["code"], response["error"]["line"]), (code, line))

    def run(
        reqs: list[dict[str, Any]],
        host: bool = True,
        source_validity: dict[int, bool] | None = None,
    ) -> list[dict[str, Any]]:
        # By default the harness is the trusted host and supplies room_policy (D-15);
        # host=False sends exactly the requests given, for the fail-closed checks.
        if host:
            reqs = hosted(reqs)
        out = adapter(reqs)
        compiled = {}
        validators = v2_validators
        if any(
            q["op"] == "compile"
            and "contract.replies/1"
            in a.get("ok", {}).get("card", {}).get("extensions", [])
            for q, a in zip(reqs, out)
        ):
            validators = reply_validators
        for index, (q, a) in enumerate(zip(reqs, out)):
            if q["op"] == "compile" and "ok" in a:
                compiled[q["handle"]] = a["ok"]
            # The schema and the reducer agree on every creation's control budget (spec L2).
            if (
                q["op"] == "new"
                and q["contract"] in compiled
                and ("ok" in a or a.get("error") == "control_budget_required")
            ):
                valid = validators["initialization"].is_valid(
                    {"contract": compiled[q["contract"]], "config": q["config"]}
                )
                assert valid == ("ok" in a), (
                    "initialization schema disagrees with the reducer",
                    q["config"],
                    a,
                )
            typed_error(validators, q, a)
            if "source" in q:
                if source_validity is not None and index in source_validity:
                    assert (
                        validators["source"].is_valid(q["source"])
                        == source_validity[index]
                    ), ("source schema oracle differs", index)
                else:
                    validators["source"].validate(q["source"])
            if q["op"] == "new":
                validators["config"].validate(q["config"])
            if q["op"] == "compile" and "ok" in a:
                validators["compiled"].validate(a["ok"])
            if q["op"] == "query" and q["selector"] == "role" and "ok" in a:
                validators["role_definition"].validate(a["ok"])
            if q["op"] in ("new", "restore") and "ok" in a:
                validators["checkpoint_data"].validate(a["ok"])
            if q["op"] in ("step", "tick", "host_signal"):
                validators["checkpoint_data"].validate(a["state"])
                validators[
                    "tick_decision"
                    if q["op"] == "tick"
                    else "host_decision"
                    if q["op"] == "host_signal"
                    else "decision"
                ].validate(a["decision"])
            if q["op"] == "view" and "ok" in a:
                validators["projection"].validate(a["ok"])
            if q["op"] == "checkpoint" and "ok" in a:
                validators["room_checkpoint"].validate(a["ok"])
        traces.append({"requests": reqs, "responses": out})
        return out

    traces: list[dict[str, Any]] = []
    h7_fixture = json.loads(h7_path.read_text())
    check_h7(h7_fixture, run(h7_fixture["requests"]))
    checks.append("h7-shared-session-id-namespace")
    unchecked_fixture = json.loads(unchecked_path.read_text())
    for lane, mode in [("unchecked", True), ("checked", False)]:
        check_unchecked(
            unchecked_fixture,
            run(unchecked_fixture["requests"][lane], host=False),
            mode,
        )
        checks.append("h8-result-markers-" + lane)
    # V2-FAIL-CLOSED: an unknown engine is refused with `engine` before any other header field.
    unknown_engine = json.loads(unknown_engine_path.read_text())
    # The retained native refusal probe deliberately violates the current source
    # schema; the fixture declares it in source_schema_assertions. Never skip its response oracle.
    check_unknown_engine(
        unknown_engine,
        run(
            unknown_engine["requests"],
            host=False,
            source_validity={
                row["request"]: row["valid"]
                for row in unknown_engine["source_schema_assertions"]
            },
        ),
    )
    checks.append("unknown-engine-refused-first")
    raw = (ROOT / "contract-lab/contracts/v2/assistant-answer.band").read_text()
    r = run(setup(raw) + [step("r", "q", "question"), answer_step()])
    eq("exact-file-sha", r[0]["ok"]["source_digest"], source(raw)["sha256"])
    eq("no-idle-default", r[1]["ok"]["state"]["idle_ms"], None)
    eq("no-hard-cap-default", r[1]["ok"]["state"]["max_at"], None)
    eq("plain-question-accepted", r[2]["decision"]["status"], "accepted")
    eq("plain-answer-completes", r[3]["state"]["state"]["outcome"], "complete")
    changed = swap(raw, "version: 0.4.1", "version: 0.4.2")
    r2 = run(setup(changed))
    eq("author-version-not-behavior", r[0]["ok"]["digest"], r2[0]["ok"]["digest"])
    eq(
        "author-version-changes-file-pin",
        r[0]["ok"]["source_digest"] == r2[0]["ok"]["source_digest"],
        False,
    )
    r = run(setup(raw, max_messages=1) + [step("r", "q", "question")])
    eq(
        "refuse-work-without-answer-capacity",
        r[-1]["decision"]["reason"],
        "completion_capacity",
    )
    r = run(
        setup(raw)
        + [step("r", "q", "question"), {"op": "tick", "handle": "s", "now": 300000}]
    )
    eq("owed-answer-expires-as-stalled", r[-1]["state"]["state"]["outcome"], "stalled")
    # Any return to new passes back through the reply wait, so bound that cycle to reach the entry check.
    back_to_new = swap(
        swap(raw, "& complete", "& new"),
        "\nstate answering\n",
        "\nstate answering max_entries 2 => stalled\n",
    )
    for label, bad in [
        ("entry-only", back_to_new),
        ("encoding", swap(raw, "version: 0.4.1", "encoding: utf-16")),
    ]:
        r = run([{"op": "compile", "handle": "c", "source": source(bad)}])
        eq(label, "error" in r[0], True)
    r = run([{"op": "compile", "handle": "c", "source": source(back_to_new)}])
    compile_error("entry-only-code-line", r[0], "initial_state_target", 42)
    r = run(
        [
            {
                "op": "compile",
                "handle": "c",
                "source": source(swap(raw, "version: 0.4.1", "encoding: utf-16")),
            }
        ]
    )
    compile_error("encoding-field-value", r[0], "field_value", 5)
    # Sofia set O LOW: every malformed header takes the compiler shape, at the field's file line.
    r = run(
        [
            {
                "op": "compile",
                "handle": "c",
                "source": source(
                    swap(raw, "name: assistant-answer", "name: [unclosed")
                ),
            }
        ]
    )
    compile_error("header-invalid-yaml", r[0], "header", 1)
    r = run(
        [
            {
                "op": "compile",
                "handle": "c",
                "source": source(
                    swap(raw, "name: assistant-answer", "name: Assistant Answer")
                ),
            }
        ]
    )
    compile_error("header-bad-name", r[0], "field_value", 2)
    ei = corpus_source("explicit-infinity")["content"]
    ei_body = ei.split("---\n", 2)[2]
    package = (
        '---\nname: explicit-infinity\ndescription: Current standard package.\nmetadata:\n  band-engine: "0.2.0-lab.1"\n  band-roles: "a,b"\n  band-contract: "protocol.band"\n  band-contract-sha256: "'
        + hashlib.sha256(ei_body.encode()).hexdigest()
        + '"\n---\nPackage guidance.\n'
    )
    r = run(
        [
            {
                "op": "compile",
                "handle": "c",
                "source": file_source(
                    package,
                    format="standard",
                    body=ei_body,
                    package_name="other-package",
                ),
            }
        ]
    )
    compile_error("header-package-name-mismatch", r[0], "field_value", 2)
    # Loader precedence (Sofia set P P-L6): size, UTF-8 and the envelope hash come before the format,
    # so a retired native source with a wrong hash is file_digest_mismatch, not frontmatter_required.
    native = source(raw)
    native["format"] = "native"
    native["sha256"] = "0" * 64
    r = run(
        [{"op": "compile", "handle": "c", "source": native}], source_validity={0: False}
    )
    eq("loader-hash-before-format", r[0], {"error": "file_digest_mismatch"})
    pattern = swap(raw, "text(1..2000)", "text(8, regex: ~r/[A-Z]+/)")
    r = run(setup(pattern) + [step("r", "q", "question")])
    eq("pattern-rejects-payload", r[-1]["decision"]["reason"], "payload")
    # D-6: one pattern spelling; ~R and quoted strings are removed.
    for label, old_form in [
        ("uppercase-pattern-sigil-removed", "~R/[A-Z]+/"),
        ("quoted-pattern-removed", '"[A-Z]+"'),
    ]:
        bad = run(
            [
                {
                    "op": "compile",
                    "handle": "c",
                    "source": source(swap(pattern, "~r/[A-Z]+/", old_form)),
                }
            ]
        )
        compile_error(label, bad[0], "syntax", 24)
    raw_plus = swap(raw, "text(1..2000)", r"text(8, regex: ~r/a\+b/)")
    proposal = step("r", "q", "question")
    proposal["message"]["payload"]["text"] = "a+b"
    r = run(setup(raw_plus) + [proposal])
    eq("raw-pattern-single-backslash", r[-1]["decision"]["status"], "accepted")
    doc_text = r"Keep C:\work\notes, \n and #{request_id} literally."
    # The example already prompts every type; replace the question's prompt with the raw doc.
    documented = swap(
        raw,
        '"""\nOne question, with the context needed to answer it.\n"""\ntype question',
        '~s"""\n' + doc_text + '\n"""\ntype question',
    )
    doc_result = run(
        [
            setup(documented)[0],
            {"op": "query", "contract": "c", "selector": "type", "name": "question"},
        ]
    )
    eq(
        "raw-doc-query-preserves-text",
        doc_result[1]["ok"]["fields"]["text"]["kind"],
        "text",
    )
    eq("raw-doc-query-content", doc_result[1]["ok"]["meta"]["doc"], doc_text)
    plain = run([setup(raw)[0]])[0]["ok"]
    eq(
        "raw-doc-does-not-change-behavior",
        doc_result[0]["ok"]["digest"],
        plain["digest"],
    )
    eq(
        "raw-doc-changes-file-pin",
        doc_result[0]["ok"]["source_digest"] == plain["source_digest"],
        False,
    )
    # Ordinary guidance is retained prompt metadata, including non-enforceable business rules.
    # Replace the example's block prompts with one-line prompts; both forms attach the same way.
    prompted = swap(
        raw,
        '"""\nAnswer the question you were asked, within the deadline. Say what you are\nunsure of or could not check instead of guessing.\n"""\nrole assistant',
        '"Work independently and state uncertainty."\nrole assistant',
    )
    prompted = swap(
        prompted,
        '"""\nOne question, with the context needed to answer it.\n"""\ntype question',
        '"Ask for evidence."\ntype question',
    )
    prompted = swap(
        prompted,
        '"""\nThe assistant is preparing the answer; the question\'s reply window bounds the wait.\n"""\nstate answering',
        '"Apply business judgment to the answer."\nstate answering',
    )
    prompted = swap(
        prompted,
        '    """\n    The assistant answers within five minutes of the question.\n    """',
        '    "Explain your findings and remaining uncertainty."',
    )
    r = run(
        setup(prompted)
        + [
            step("r", "q", "question"),
            {"op": "query", "contract": "c", "selector": "role", "name": "assistant"},
            {"op": "view", "handle": "s", "actor": "a", "now": 0},
            answer_step(),
        ]
    )
    eq(
        "plain-prompt-role",
        r[3]["ok"]["role"]["meta"]["doc"],
        "Work independently and state uncertainty.",
    )
    eq(
        "plain-prompt-type",
        r[3]["ok"]["types"]["question"]["meta"]["doc"],
        "Ask for evidence.",
    )
    eq(
        "plain-prompt-state",
        r[3]["ok"]["states"]["answering"]["meta"]["doc"],
        "Apply business judgment to the answer.",
    )
    eq(
        "plain-prompt-rule",
        r[3]["ok"]["states"]["answering"]["rules"][0]["meta"]["doc"],
        "Explain your findings and remaining uncertainty.",
    )
    eq(
        "plain-prompt-agent-view",
        r[4]["ok"]["view"]["definition"]["role"]["meta"]["doc"],
        "Work independently and state uncertainty.",
    )
    eq("plain-prompts-preserve-behavior", r[0]["ok"]["digest"], plain["digest"])
    eq(
        "plain-prompts-not-admission-predicates",
        r[5]["state"]["state"]["outcome"],
        "complete",
    )
    sigil_raw = (ROOT / "contract-lab/contracts/v2/sigil-review.band").read_text()
    original = swap(
        swap(
            swap(sigil_raw, "~w[request revision]", "request|revision"),
            "~w[approve revise reject]",
            "approve|revise|reject",
        ),
        "~w[a b]",
        "{a,b}",
    )
    compiled = run([{"op": "compile", "handle": "c", "source": source(sigil_raw)}])[0][
        "ok"
    ]
    prior = run([{"op": "compile", "handle": "c", "source": source(original)}])[0]["ok"]
    eq("word-sigils-preserve-behavior", compiled["digest"], prior["digest"])
    eq(
        "word-sigils-change-file-pin",
        compiled["source_digest"] == prior["source_digest"],
        False,
    )
    eq(
        "word-sigil-enum-order",
        compiled["types"]["review"]["fields"]["decision"]["values"],
        ["approve", "revise", "reject"],
    )
    eq(
        "word-sigil-reference-types",
        compiled["types"]["review"]["fields"]["source"]["values"],
        ["request", "revision"],
    )
    # D-6: keywords and options are lowercase only; each uppercase alias is a syntax error.
    for label, lower, upper in [
        ("uppercase-option-removed", " as ", " AS "),
        ("uppercase-each-removed", " each ", " EACH "),
        ("uppercase-word-sigil-removed", "~w[", "~W["),
    ]:
        bad = run(
            [
                {
                    "op": "compile",
                    "handle": "c",
                    "source": source(swap(sigil_raw, lower, upper)),
                }
            ]
        )
        compile_error(
            label, bad[0], "syntax", {" as ": 49, " each ": 56, "~w[": 42}[lower]
        )

    def review_message(
        actor: str, ident: str, kind: str, payload: dict[str, Any], at: int
    ) -> dict[str, Any]:
        return {
            "op": "step",
            "handle": "s",
            "actor": actor,
            "now": at,
            "message": {"id": ident, "session": "s", "kind": kind, "payload": payload},
        }

    for kind in ["request", "revision"]:
        start = [
            {"op": "compile", "handle": "c", "source": source(sigil_raw)},
            {
                "op": "new",
                "contract": "c",
                "handle": "s",
                "config": {
                    "session": "s",
                    "bindings": {"lead": "lead", "a": "a", "b": "b"},
                    "now": 0,
                    "max_messages": 10,
                },
            },
            review_message("lead", "q", kind, {"label": "TASK_1"}, 0),
        ]
        replies = [
            review_message(
                "a", "bad", "review", {"source": "wrong", "decision": "approve"}, 1
            ),
            review_message(
                "a", "a1", "review", {"source": "q", "decision": "approve"}, 60000
            ),
            review_message(
                "b", "b1", "review", {"source": "q", "decision": "revise"}, 180000
            ),
        ]
        r = run(start + replies)
        eq(
            "word-sigil-" + kind + "-rejects-wrong-reference",
            r[3]["decision"]["reason"],
            "reference",
        )
        eq(
            "word-sigil-" + kind + "-waits-for-both",
            r[4]["state"]["state"]["state"],
            "reviewing",
        )
        eq(
            "word-sigil-" + kind + "-collects-both",
            r[5]["state"]["state"]["outcome"],
            "collected",
        )
        r = run(start + [replies[1], {"op": "tick", "handle": "s", "now": 300000}])
        eq(
            "word-sigil-" + kind + "-original-deadline",
            r[-1]["state"]["state"]["outcome"],
            "stalled",
        )

    # Explicit oracles for the three timeout scopes, including serialized recovery.
    clocks = (
        swap(
            swap(
                raw, "\ncontract @infinity => expired\n", "\ncontract @30m => expired\n"
            ),
            "state answering",
            "state answering @5m => phase_late",
        )
        + "\nend phase_late\n"
    )
    r = run(
        setup(clocks)
        + [
            step("r", "q", "question", 1000),
            {"op": "query", "contract": "c", "selector": "role", "name": "assistant"},
            {"op": "tick", "handle": "s", "now": 301000},
        ]
    )
    eq("inline-duty-wins-phase-tie", r[-1]["state"]["state"]["outcome"], "stalled")
    # V2-TIE-ORDER (spec M12): within a category IDs compare as strings. Rule 1 derives due-2 and rule 5 due-10;
    # both fall due at 1m, so due-10 fires first and its outcome stands. The assistant claims the work, so
    # its five duties are reports it owes, not replies to a request (reply_required).
    ties = "".join(
        f"  assistant -> requester: a{i}() @{1 if i in (1, 5) else 2}m => complete | late_{i}\n"
        for i in range(1, 6)
    )
    tied = (
        "---\nname: tie-duty-ids\ndescription: Equal-time duty IDs.\nengine: 0.2.0-lab.1\nroles: [requester, assistant]\n---\ncontract @1h => expired\ntype claim\n  text: text(100)\n"
        + "".join(f"type a{i}\n" for i in range(1, 6))
        + "state new\n  assistant -> requester: claim() => working\nstate working\n"
        + ties
        + "end complete\n"
        + "".join(f"end late_{i}\n" for i in range(1, 6))
    )
    t = run(
        setup(tied, max_messages=10)
        + [step("a", "q", "claim"), {"op": "tick", "handle": "s", "now": 60000}]
    )
    eq(
        "tie-duty-ids-derived",
        [(o["id"], o["due_at"]) for o in t[2]["state"]["state"]["obligations"]],
        [
            ("due-2", 60000),
            ("due-4", 120000),
            ("due-6", 120000),
            ("due-8", 120000),
            ("due-10", 60000),
        ],
    )
    eq(
        "tie-due-10-before-due-2",
        [
            t[3]["state"]["state"]["expiry"]["id"],
            t[3]["state"]["state"]["outcome"],
            t[3]["state"]["state"]["version"],
        ],
        ["due-10", "late_5", 2],
    )
    eq(
        "scope-clock-profile",
        r[0]["ok"]["timeout"]["profile"],
        "band-contract/timeout/1",
    )
    eq(
        "role-query-keeps-contract-clock",
        r[3]["ok"]["contract"]["timeout"],
        r[0]["ok"]["timeout"],
    )
    eq(
        "role-query-keeps-state-clock",
        r[3]["ok"]["states"]["answering"]["timeout"],
        r[0]["ok"]["states"]["answering"]["timeout"],
    )
    # Every profile-2 contract declares its whole-run clock; @infinity is the explicit opt-out.
    r = run(
        [
            {
                "op": "compile",
                "handle": "c",
                "source": source(
                    swap(raw, "\ncontract @infinity => expired\n", "\ncontract\n")
                ),
            }
        ]
    )
    compile_error("contract-clock-required", r[0], "contract_clock_required", 1)
    # A renewal target in a state without a clock is a plain v2 compile error (spec M10).
    r = run(
        [
            {
                "op": "compile",
                "handle": "c",
                "source": source(
                    swap(
                        raw,
                        "state answering\n",
                        "state answering\n  event requester -> assistant: question() => @state\n",
                    )
                ),
            }
        ]
    )
    compile_error("timer-target-plain-v2", r[0], "timer_target", 55)
    # A grapheme-only text bound has no byte bound (V2-TEXT).
    r = run(
        [
            {
                "op": "compile",
                "handle": "c",
                "source": source(swap(raw, "text(1..2000)", "text(graphemes: 1..20)")),
            }
        ]
    )
    compile_error("grapheme-only-bound-rejected", r[0], "type", 24)
    # An unknown engine identifier is refused (V2-FAIL-CLOSED). The reference reports a loader
    # error here, not the compiler code `engine`; only the refusal is asserted.
    r = run(
        [
            {
                "op": "compile",
                "handle": "c",
                "source": source(
                    swap(raw, "engine: 0.2.0-lab.1", "engine: 0.9.0-lab.1")
                ),
            }
        ]
    )
    eq("unknown-engine-refused", ("ok" in r[0], "error" in r[0]), (False, True))
    # The normalized v2 card keeps no finite idle timer (defaults-v2.md, spec L2).
    card = copy.deepcopy(plain["card"])
    card["inactivity_timeout"] = ["seconds", 300]
    eq(
        "card-inactivity-infinite-only",
        (
            plain["card"]["inactivity_timeout"],
            reply_validators["card"].is_valid(plain["card"]),
            reply_validators["card"].is_valid(card),
        ),
        ("infinite", True, False),
    )
    # Mention caps (D-3): the contract line sets the default; a rule overrides it.
    capped = swap(
        swap(
            raw,
            "\ncontract @infinity => expired\n",
            "\ncontract @infinity mentions 1 => expired\n",
        ),
        "question() awaits answering",
        "question() mention 1 awaits answering",
    )
    r = run(
        [
            {"op": "compile", "handle": "c", "source": source(capped)},
            {"op": "query", "contract": "c", "selector": "role", "name": "assistant"},
        ]
    )
    eq("mention-default-compiled", r[0]["ok"]["mentions"], {"max": 1})
    eq(
        "mention-rule-override",
        [x.get("mention_cap") for x in r[0]["ok"]["states"]["new"]["rules"]],
        [1],
    )
    # A state clock cannot shorten a reply window, so the phase-only clock sits on the state the
    # answer enters at 1000: no reply is owed there, and only the state clock can fire.
    phase = (
        swap(
            swap(clocks, "state answering @5m => phase_late", "state answering"),
            "& complete",
            "& answered",
        )
        + "\nstate answered @5m => phase_late\n"
    )
    r = run(
        setup(phase)
        + [
            step("r", "q", "question", 1000),
            answer_step(1000),
            {"op": "checkpoint", "handle": "s"},
        ]
    )
    checkpoint = r[-1]["ok"]
    r = run(
        [
            setup(phase)[0],
            {
                "op": "restore",
                "contract": "c",
                "handle": "s",
                "checkpoint": checkpoint,
                "now": 200000,
            },
            {"op": "tick", "handle": "s", "now": 301000},
            {"op": "checkpoint", "handle": "s"},
        ]
    )
    eq("state-clock-survives-restart", r[2]["state"]["state"]["outcome"], "phase_late")
    eq(
        "state-clock-retains-absolute-time",
        r[2]["state"]["state"]["expired_at"],
        301000,
    )
    expired_checkpoint = r[-1]["ok"]
    r = run(
        [
            setup(phase)[0],
            {
                "op": "restore",
                "contract": "c",
                "handle": "s",
                "checkpoint": expired_checkpoint,
                "now": 400000,
            },
        ]
    )
    eq(
        "state-expiry-restores-portably",
        r[-1]["ok"]["state"]["expiry"]["reason"],
        "state_timeout",
    )
    custom = (
        swap(phase, "contract @30m => expired", "contract @30m => run_late")
        + "end run_late\n"
    )
    r = run(setup(custom) + [{"op": "tick", "handle": "s", "now": 1800000}])
    eq(
        "whole-run-includes-initial-wait",
        r[-1]["state"]["state"]["outcome"],
        "run_late",
    )
    eq("whole-run-uses-creation-time", r[-1]["state"]["state"]["expired_at"], 1800000)
    r = run(setup(clocks) + [step("r", "q", "question", 1500001)])
    eq(
        "whole-run-keeps-full-response-window",
        r[-1]["decision"]["reason"],
        "insufficient_time",
    )
    scoped = (ROOT / "contract-lab/contracts/v2/scoped-review.band").read_text()
    start = [
        {"op": "compile", "handle": "c", "source": source(scoped)},
        {
            "op": "new",
            "contract": "c",
            "handle": "s",
            "config": {
                "session": "s",
                "bindings": {"lead": "lead", "reviewer": "reviewer"},
                "now": 0,
                "max_messages": 10,
                "capabilities": ["contract.replies/1"],
            },
        },
        review_message("lead", "q", "request", {"text": "Review this change."}, 0),
    ]
    r = run(
        start
        + [
            review_message(
                "reviewer", "r", "review", {"request": "q", "text": "The report."}, 1000
            ),
            {"op": "tick", "handle": "s", "now": 121000},
        ]
    )
    eq("scoped-example-phase-expiry", r[-1]["state"]["state"]["outcome"], "undecided")
    r = run(
        start
        + [
            review_message(
                "reviewer", "r", "review", {"request": "q", "text": "The report."}, 1000
            ),
            review_message("lead", "accept", "accept", {}, 2000),
        ]
    )
    eq("scoped-example-completion", r[-1]["state"]["state"]["outcome"], "collected")
    r = run(start + [{"op": "tick", "handle": "s", "now": 300000}])
    eq(
        "scoped-example-missing-review",
        r[-1]["state"]["state"]["outcome"],
        "review_missing",
    )
    signal_raw = (ROOT / "contract-lab/contracts/v2/presence-recovery.band").read_text()
    cfg = {
        "capabilities": ["host.presence/1", "host.membership/1", "contract.replies/1"],
        "max_controls": 16,
        "watch_bindings": {"supervisor": "human-supervisor"},
    }
    start = setup(signal_raw, **cfg) + [
        step("r", "q", "question"),
        signal(1, "offline", 1000),
    ]
    r = run(
        start
        + [
            {"op": "checkpoint", "handle": "s"},
            {"op": "view", "handle": "s", "actor": "a", "now": 2000},
        ]
    )
    checkpoint = r[4]["ok"]
    r = run(
        [
            start[0],
            {
                "op": "restore",
                "contract": "c",
                "handle": "s",
                "checkpoint": checkpoint,
                "now": 200000,
            },
            {"op": "tick", "handle": "s", "now": 301000},
            {"op": "tick", "handle": "s", "now": 600000},
            {"op": "tick", "handle": "s", "now": 901000},
            signal(2, "online", 902000),
            {"op": "tick", "handle": "s", "now": 1502000},
        ]
    )
    # A restart keeps the question's original deadline: no fresh reply window (V2-RESTORE/1).
    eq("original-duty-preserved", r[1]["ok"]["state"]["wait"]["due_at"], 600000)
    # The dwell then pauses the work. A pause leaves the reply wait, so the question's ten minutes
    # stop applying (timeouts-v2 "A reply wait"), and paused waits for the assistant with no clock.
    # The return moves to resumed, where the requester has ten minutes to ask again.
    paused = [
        r[2]["state"]["state"]["state"],
        r[2]["state"]["state"]["wait"],
        r[3]["state"]["state"]["state"],
        r[4]["state"]["state"]["state"],
        r[5]["state"]["state"]["state"],
        r[6]["state"]["state"]["outcome"],
    ]
    # Re-asking needs message budget for a second question and its answer.
    r = run(
        setup(signal_raw, **{**cfg, "max_messages": 4})
        + [
            step("r", "q", "question"),
            signal(1, "offline", 1000),
            {"op": "tick", "handle": "s", "now": 301000},
            signal(2, "online", 400000),
            step("r", "q2", "question", 500000),
        ]
    )
    asked = [
        r[5]["state"]["state"]["state"],
        r[6]["decision"]["status"],
        r[6]["state"]["state"]["state"],
        r[6]["state"]["state"]["wait"]["due_at"],
    ]
    eq(
        "restart-dwell-transition",
        [paused, asked],
        [
            ["paused", None, "paused", "paused", "resumed", "stalled"],
            ["resumed", "accepted", "working", 1100000],
        ],
    )
    # Back online before the dwell, the restored wait still ends the run at its original deadline.
    r = run(
        [
            start[0],
            {
                "op": "restore",
                "contract": "c",
                "handle": "s",
                "checkpoint": checkpoint,
                "now": 200000,
            },
            signal(2, "online", 250000),
            {"op": "tick", "handle": "s", "now": 599999},
            {"op": "tick", "handle": "s", "now": 600000},
        ]
    )
    eq(
        "original-duty-still-terminates",
        [r[3]["state"]["state"]["state"], r[4]["state"]["state"]["outcome"]],
        ["working", "stalled"],
    )
    r = run(
        start
        + [
            signal(1, "offline", 1000),
            signal(2, "online", 300999),
            {"op": "tick", "handle": "s", "now": 301000},
        ]
    )
    eq("host-retry-idempotent", r[4]["decision"]["status"], "duplicate_host_event")
    eq("reconnect-cancels-dwell", r[-1]["state"]["state"]["state"], "working")
    r = run(
        start
        + [signal(2, "unknown", 300999), {"op": "tick", "handle": "s", "now": 301000}]
    )
    eq(
        "unknown-presence-cancels-dwell",
        [r[-2]["decision"]["status"], r[-1]["state"]["state"]["state"]],
        ["host_accepted", "working"],
    )
    r = run(
        setup(signal_raw, **{**cfg, "max_controls": 2})
        + [step("r", "q", "question"), signal(1, "offline", 1), signal(2, "removed", 2)]
    )
    eq("removal-uses-final-reserve", r[-1]["state"]["state"]["outcome"], "left")
    eq("control-records-bounded", len(r[-1]["state"]["state"]["control_log"]), 2)
    r = run(setup(signal_raw, **{**cfg, "capabilities": []}))
    eq("unsupported-extension-refused", r[1]["error"], "unsupported_extension")

    # V2-CONTROL / spec L2: the reducer is normative and keys the budget on the contract's declared
    # signal extensions; the initialization schema keys on the same, so both agree on each creation.
    def budget(
        label: str, contract: str, config: dict[str, Any], expected: Any
    ) -> None:
        requests = hosted(setup(contract, **config))
        responses = adapter(requests)
        traces.append({"requests": requests, "responses": responses})
        vs = (
            reply_validators
            if "contract.replies/1" in responses[0]["ok"]["card"]["extensions"]
            else validators
        )
        if "error" in responses[1]:
            vs["creation_error"].validate(responses[1])
        init = {"contract": responses[0]["ok"], "config": requests[1]["config"]}
        eq(
            "control-budget-" + label,
            (
                responses[1].get("error", "accepted"),
                vs["initialization"].is_valid(init),
            ),
            expected,
        )

    declared = {k: v for k, v in cfg.items() if k != "max_controls"}
    for label, changes in [
        ("absent", {}),
        ("zero", {"max_controls": 0}),
        ("one", {"max_controls": 1}),
        ("over", {"max_controls": 1025}),
    ]:
        budget(
            label,
            signal_raw,
            {**declared, **changes},
            ("control_budget_required", False),
        )
    budget("two", signal_raw, {**declared, "max_controls": 2}, ("accepted", True))
    # A host capability alone enables nothing: a contract that declares no signal extension needs no budget.
    budget(
        "undeclared-capability",
        raw,
        {"capabilities": ["host.presence/1", "contract.replies/1"], "max_controls": 0},
        ("accepted", True),
    )
    # V2-POLICY (D-15): a missing room_policy fails closed on every engine. A checked session
    # needs fresh host evidence on each member call, exact retries included.
    policy = trusted_policy({"requester": "r", "assistant": "a"})
    ask = step("r", "q", "question")
    r = run(setup(raw), host=False)
    eq("policy-required-at-new", r[1]["error"], "host_policy_required")
    r = run(
        setup(raw, room_policy=policy)
        + [
            ask,
            {**ask, "room_policy": policy},
            ask,
            {"op": "view", "handle": "s", "actor": "r", "now": 0},
        ],
        host=False,
    )
    eq("policy-required-on-step", r[2]["decision"]["reason"], "host_policy_required")
    eq("policy-refusal-admits-nothing", r[2]["state"]["state"]["version"], 0)
    eq("policy-evidence-admits", r[3]["decision"]["status"], "accepted")
    eq(
        "policy-required-on-exact-retry",
        r[4]["decision"]["reason"],
        "host_policy_required",
    )
    eq("policy-required-on-view", r[5], {"error": "host_policy_required"})
    eq(
        "checked-session-unmarked",
        ("policy_mode" in r[1]["ok"]["state"], "policy" in r[3]["decision"]),
        (False, False),
    )
    revoked = copy.deepcopy(policy)
    revoked["principals"]["a"]["active"] = False
    answer = answer_step()
    r = run(
        setup(raw)
        + [
            ask,
            {**answer, "room_policy": revoked},
            {**answer, "room_policy": {**policy, "fixed_roles": {"assistant": "r"}}},
        ]
    )
    eq("revoked-member-room-access", r[3]["decision"]["reason"], "room_access")
    eq("fixed-role-mismatch", r[4]["decision"]["reason"], "role_assignment")
    # The explicit local mode is honored only at new, and only by a local-reference adapter
    # such as this one. It marks the state, every decision and every view, so it is never evidence.
    r = run(
        setup(raw, room_policy={"mode": "unchecked"})
        + [ask, {"op": "view", "handle": "s", "actor": "a", "now": 0}],
        host=False,
    )
    eq("unchecked-local-mode-accepted", r[2]["decision"]["status"], "accepted")
    eq("unchecked-decision-marked", r[2]["decision"]["policy"], "unchecked")
    eq("unchecked-state-marked", r[1]["ok"]["state"]["policy_mode"], "unchecked")
    eq("unchecked-view-marked", r[3]["ok"]["view"]["policy"], "unchecked")
    r = run(setup(raw) + [{**ask, "room_policy": {"mode": "unchecked"}}])
    eq(
        "unchecked-is-start-time-only",
        r[2]["decision"]["reason"],
        "invalid_room_policy",
    )
    for label, bad in [
        ("empty", {}),
        ("mixed", {**policy, "mode": "unchecked"}),
        ("unknown-mode", {"mode": "trusted"}),
    ]:
        requests = setup(raw, room_policy=bad)
        responses = adapter(requests)
        traces.append({"requests": requests, "responses": responses})
        eq(
            "policy-" + label + "-invalid",
            (responses[1].get("error"), validators["room_policy"].is_valid(bad)),
            ("invalid_room_policy", False),
        )
    # A session config names only schema keys: a misspelled cap is refused, never replaced by its default (impl L18).
    requests = hosted(setup(raw, max_byte=200))
    responses = adapter(requests)
    traces.append({"requests": requests, "responses": responses})
    eq(
        "unknown-config-key-invalid",
        (
            responses[1].get("error"),
            validators["config"].is_valid(requests[1]["config"]),
        ),
        ("invalid_config", False),
    )
    # L7: a byte cap below the smallest reply a duty can require is refused at creation, with an enumerated code.
    r = run(setup(raw, max_bytes=60))
    eq("completion-size-refused", r[1], {"error": "completion_size"})
    # Omar set P P-9: room_new errors are enumerated, and run() types them like every other boundary.
    r = run(
        [
            setup(raw)[0],
            {
                "op": "room_new",
                "contract": "c",
                "handle": "room",
                "config": {"bindings": {"requester": "r", "assistant": "a"}, "now": 0},
            },
        ]
    )
    eq("room-new-policy-required", r[1], {"error": "room_policy_required"})
    # Sofia set Q Q-L6: room_new refuses an invalid time and bindings that miss a role. The
    # safe-integer upper bound is pinned in contract-lab: protocol JSON cannot carry 2**53.
    room_policy = {
        "window_ms": 1000,
        "max_events": 4,
        "max_mentions": 4,
        "max_receipts": 8,
    }
    r = run(
        [
            setup(raw)[0],
            {
                "op": "room_new",
                "contract": "c",
                "handle": "room",
                "config": {
                    "bindings": {"requester": "r", "assistant": "a"},
                    "now": -1,
                    **room_policy,
                },
            },
        ]
    )
    eq("room-new-invalid-time", r[1], {"error": "invalid_time"})
    r = run(
        [
            setup(raw)[0],
            {
                "op": "room_new",
                "contract": "c",
                "handle": "room",
                "config": {"bindings": {"requester": "r"}, "now": 0, **room_policy},
            },
        ]
    )
    eq("room-new-invalid-bindings", r[1], {"error": "invalid_bindings"})
    # V2-HUMAN: a human may converse in the room but is never bound to a contract role.
    human = copy.deepcopy(policy)
    human["principals"]["r"]["kind"] = "human"
    r = run(setup(raw, room_policy=human))
    eq("human-binding-refused-at-new", r[1]["error"], "agent_binding_required")
    r = run(setup(raw) + [{**ask, "room_policy": human}])
    eq(
        "human-binding-refused-on-step",
        r[2]["decision"]["reason"],
        "agent_binding_required",
    )
    # M21: exact independent negative oracles, including deliberately invalid source envelopes.
    for case in fail_closed_cases():
        requests = hosted(case["requests"])
        responses = adapter(requests)
        for q, a in zip(requests, responses):
            typed_error(validators, q, a)
        traces.append(
            {
                "case": case["id"],
                "requirements": ["V2-ID", "V2-FAIL-CLOSED"],
                "requests": requests,
                "responses": responses,
            }
        )
        declared = {
            row["request"]: row["valid"]
            for row in case.get("source_schema_assertions", [])
        }
        for i, request in enumerate(requests):
            if "source" in request:
                eq(
                    case["id"] + "-schema-" + str(i),
                    validators["source"].is_valid(request["source"]),
                    declared.get(i, True),
                )
        for i, assertion in enumerate(case["assertions"]):
            eq(
                case["id"] + "-A" + str(i),
                pointer(responses[assertion["response"]], assertion["pointer"]),
                assertion["equals"],
            )
    # M22: precise compiler reasons and all six lifecycle forms, plus typed attribute bounds.
    for case in safety_cases():
        requests = hosted(case["requests"])
        responses = adapter(requests)
        for q, a in zip(requests, responses):
            typed_error(validators, q, a)
        traces.append(
            {
                "case": case["id"],
                "requirements": ["V2-PATTERN", "V2-ENTRY", "V2-ROSTER"],
                "requests": requests,
                "responses": responses,
            }
        )
        registry = participant_schema if case.get("schema_profile") == 4 else schema
        for request, response in zip(requests, responses):
            if request["op"] == "compile" and isinstance(response.get("error"), dict):
                jsonschema.validate(
                    response["error"], {**registry, "$ref": "#/$defs/compiler_error"}
                )
            if request["op"] == "new" and "ok" in response:
                jsonschema.validate(
                    response["ok"], {**registry, "$ref": "#/$defs/checkpoint_data"}
                )
        for i, assertion in enumerate(case["assertions"]):
            eq(
                case["id"] + "-A" + str(i),
                pointer(responses[assertion["response"]], assertion["pointer"]),
                assertion["equals"],
            )
    for _, doc, _ in catalog:
        assert (ROOT / "spec" / doc).is_file(), doc
    assert [i for i, _, _ in catalog] == list(trace), (
        "every catalog row needs profiles and acceptance"
    )
    catalog_data = {
        "profile": "band-contract/requirements/2",
        "engine": "0.2.0-lab.1",
        "requirements": [
            {
                "id": i,
                "level": "MUST",
                "profiles": list(trace[i][0]),
                "specification": d,
                "statement": s,
                "acceptance": list(trace[i][1]),
            }
            for i, d, s in catalog
        ],
    }
    expected = ROOT / "spec/requirements-v2.json"
    if "--write-catalog" in sys.argv:
        expected.write_text(json.dumps(catalog_data, indent=2) + "\n")
    assert json.loads(expected.read_text()) == catalog_data
    # Every check names the v2 requirements it evidences (spec M24, v2trace.py).
    requirements = verify_tags("checkv2", checks, catalog_data)
    report = {
        "passed": True,
        "assertions": len(checks),
        "schema_definitions": len(validators),
        "adapter_requests": sum(len(t["requests"]) for t in traces),
        "checks": checks,
        "requirements": requirements,
        "traces": traces,
    }
    output = ROOT / ".cache/review-v2-conformance.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    sys.stdout.write(
        str(
            json.dumps(
                {
                    k: v
                    for k, v in report.items()
                    if k not in ("traces", "checks", "requirements")
                }
            )
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
