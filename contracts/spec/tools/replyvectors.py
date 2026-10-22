"""Conformance vectors for reply alternatives (op-jgnxp6nc, op-5nwu4mxy; ubf-replies/DESIGN.md v2 + COVERAGE.md).

Surface syntax U with the implicit-correlation sugar (Eric decision 4, ubf-replies/SYNTAX-OPTIONS.md
"Option U" and "Implicit correlation sugar"; Theo's U grammar theo-promptdocs-out/replies/PRODUCTIONS.md);
codes and report lines per COVERAGE lead decision 3; prompts, block indentation and re-sends per lead
decision 6.
Every expected value here is written by hand from the design, never read back from an
implementation: error lines are computed from the vector's own source text (body lines, as
compiler errors report them), and ids follow DESIGN §5 numbering. The cases use the
conformance-case/1 shape of vectorsv2extras.py so generatevectors.py can pin them once the
compiler (Kenji) and engine (Omar) land. Until then runreplyvectors.py runs them RED.

Adversarial cases (overlap witnesses, the S4 blow-up, the C1 late reply, rebinding races,
multi-target renewal) belong to Felix's spec/conformance/replies-adversarial/ and are not here.
"""

from __future__ import annotations

from typing import Any

from contracts.spec.tools.vectorinputs import file_source

HEADER = (
    "---\nname: reply-deploy\ndescription: Reply alternatives conformance vector.\n"
    "engine: 0.2.0-lab.1\nroles: [{roles}]\n---\n"
)

# The explicit form (`as job`, `request=$job`): still legal, and the formatter keeps it.
DEPLOY = """contract @1h => expired

type deploy
  target: text(1..100)

type ok
  request: ref(deploy)

type error
  request: ref(deploy)
  retryable: boolean
  reason: text(200)

type abandon

state new
  lead -> deployer: deploy() as job awaits deploying
    => ok(request=$job) & deployed
     | error(request=$job, retryable=true) & retrying
     | error(request=$job, retryable=false) & failed
     | @10m & stalled

state deploying max_entries 3 => exhausted

state retrying
  lead -> deployer: deploy() as job => deploying
  lead -> deployer: abandon() => failed

end deployed

end failed

end stalled

end exhausted
"""

HEAD = "  lead -> deployer: deploy() as job awaits deploying"
OK_ROW = "    => ok(request=$job) & deployed"
TRUE_ROW = "     | error(request=$job, retryable=true) & retrying"
FALSE_ROW = "     | error(request=$job, retryable=false) & failed"
TIMEOUT = "     | @10m & stalled"
W_LINE = "state deploying max_entries 3 => exhausted"
RESEND = "  lead -> deployer: deploy() as job => deploying"
ABANDON = "  lead -> deployer: abandon() => failed"
TIMED = "  lead -> deployer: abandon() @5m => failed | stalled"

# RULE.md §A (sha 05bfa5a1): A1 replier exits, A1-1 rebinding, A4 entries, T1 timed heads.
QUIT_END = "  deployer -> lead: quit() => failed"
QUIT_MID = "  deployer -> lead: quit() => retrying"
REBIND_EVENT = "  event lead -> deployer: abandon() as job"
ENTRY = "  lead -> deployer: abandon() as job => deploying"
ENTRY_OTHER_ROUTE = "  deployer -> lead: abandon() as job => deploying"
T1_HEAD = "  lead -> deployer: deploy() as job @5m awaits deploying | stalled"


def sugared(body: str) -> str:
    """The same contract with both sugars: no `as` on any head, no `=$` on any alternative."""
    out = body.replace(" as job awaits ", " awaits ").replace(" as job => ", " => ")
    return out.replace("(request=$job, ", "(").replace("(request=$job)", "()")


SUGAR = sugared(
    DEPLOY
)  # SYNTAX-OPTIONS.md's deploy example, verbatim, plus the re-send in retrying
S_HEAD = "  lead -> deployer: deploy() awaits deploying"
S_OK_ROW = "    => ok() & deployed"
S_TRUE_ROW = "     | error(retryable=true) & retrying"
S_RESEND = "  lead -> deployer: deploy() => deploying"

ONE_REVIEW = """contract @1h => expired

type request
  text: text(1..2000)

type review
  request: ref(request)
  text: text(1..4000)

state new
  lead -> reviewer: request() as req awaits reviewing
    => review(request=$req) & collected
     | @5m & stalled

end collected

end stalled
"""

# SYNTAX-OPTIONS.md's one-review, verbatim: no `awaits`, so W is `request_wait`.
REVIEW_SUGAR = """contract @1h => expired

type request
  text: text(1..2000)

type review
  request: ref(request)
  text: text(1..4000)

state new
  lead -> reviewer: request()
    => review() & collected
     | @5m & stalled

end collected

end stalled
"""
R_HEAD = "  lead -> reviewer: request()"


def replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new)


def line_of(body: str, line: str, occurrence: int = 1) -> int:
    """The one-based body line of the `occurrence`-th line equal to `line`."""
    hits = [i + 1 for i, text in enumerate(body.split("\n")) if text == line]
    assert len(hits) >= occurrence, (line, hits)
    return hits[occurrence - 1]


# A4: retrying's abandon enters W with the head's route and binding; every ref lists abandon.
ENTRY_OK = replace_once(DEPLOY, ABANDON, ENTRY).replace(
    "  request: ref(deploy)\n", "  request: ref(deploy|abandon)\n"
)


def source(
    body: str, roles: str = "lead, deployer", name: str = "reply-deploy"
) -> dict[str, Any]:
    return file_source(HEADER.format(roles=roles).replace("reply-deploy", name) + body)


def compile_request(body: str, handle: str = "c", **kw: Any) -> dict[str, Any]:
    return {"op": "compile", "handle": handle, "source": source(body, **kw)}


def expected(index: int, path: str, value: Any) -> dict[str, Any]:
    return {"response": index, "pointer": path, "equals": value}


def new(
    contract: str = "c",
    handle: str = "s",
    bindings: dict[str, Any] | None = None,
    now: int = 0,
) -> dict[str, Any]:
    return {
        "op": "new",
        "contract": contract,
        "handle": handle,
        "config": {
            "session": handle,
            "bindings": bindings or {"lead": "l", "deployer": "d"},
            "now": now,
            "inactivity": "accepted",
            "max_messages": 100,
            "max_bytes": 16384,
            "capabilities": ["contract.replies/1"],
        },
    }  # the host grants the auto-recorded extension (S6)


def step(
    actor: str,
    now: int,
    ident: str,
    kind: str,
    payload: dict[str, Any],
    handle: str = "s",
    session: str | None = None,
) -> dict[str, Any]:
    return {
        "op": "step",
        "handle": handle,
        "actor": actor,
        "now": now,
        "message": {
            "id": ident,
            "session": session or handle,
            "kind": kind,
            "payload": payload,
        },
    }


def deploy(now: int, ident: str, handle: str = "s") -> dict[str, Any]:
    return step("l", now, ident, "deploy", {"target": "prod"}, handle)


def ok(
    now: int, ident: str, request: Any, handle: str = "s", session: str | None = None
) -> dict[str, Any]:
    return step("d", now, ident, "ok", {"request": request}, handle, session)


def error(
    now: int, ident: str, request: Any, retryable: bool, handle: str = "s"
) -> dict[str, Any]:
    return step(
        "d",
        now,
        ident,
        "error",
        {"request": request, "retryable": retryable, "reason": "x"},
        handle,
    )


def tick(now: int, handle: str = "s") -> dict[str, Any]:
    return {"op": "tick", "handle": handle, "now": now}


def cases() -> list[dict[str, Any]]:
    rows = []

    def add(
        name: str,
        requests: list[dict[str, Any]],
        assertions: list[dict[str, Any]],
        requirements: list[str],
        notes: str = "",
    ) -> None:
        rows.append(
            {
                "profile": "band-contract/conformance-case/1",
                "id": "v2-reply-" + name,
                "requirements": requirements,
                "requests": requests,
                "assertions": assertions,
                "notes": notes,
            }
        )

    def compile_error(
        case: str, body: str, code: str, line: int, notes: str = "", **kw: Any
    ) -> None:
        add(
            "compile-" + case,
            [compile_request(body, **kw)],
            [expected(0, "/error/code", code), expected(0, "/error/line", line)],
            ["DSL-004", "DSL-007", "DSL-010"],
            notes,
        )

    # ---- valid blocks -------------------------------------------------------------------
    head, w = line_of(DEPLOY, HEAD), "/ok/states/deploying"
    add(
        "compile-deploy",
        [compile_request(DEPLOY)],
        [
            expected(0, "/ok/card/extensions", ["contract.replies/1"]),
            expected(0, "/ok/states/new/rules/0/id", "rule-0"),
            expected(0, "/ok/states/new/rules/0/bind", "job"),
            expected(0, "/ok/states/new/rules/0/target", "deploying"),
            expected(0, "/ok/states/new/rules/0/meta/line", head),
            expected(0, w + "/wait/id", "due-4"),
            expected(0, w + "/wait/heads", ["rule-0", "rule-5"]),
            expected(0, w + "/wait/alternatives", ["rule-1", "rule-2", "rule-3"]),
            expected(0, w + "/wait/kinds", ["ok", "error"]),
            expected(0, w + "/wait/from", ["deployer"]),
            expected(0, w + "/wait/to", ["lead"]),
            expected(0, w + "/wait/after_ms", 600000),
            expected(0, w + "/wait/outcome", "stalled"),
            expected(0, w + "/wait/meta/line", line_of(DEPLOY, TIMEOUT)),
            expected(0, w + "/deadlines", []),
            expected(0, w + "/max_entries", {"limit": 3, "outcome": "exhausted"}),
            expected(0, w + "/rules/0/id", "rule-1"),
            expected(0, w + "/rules/0/target", "deployed"),
            expected(0, w + "/rules/0/literals", {}),
            expected(0, w + "/rules/0/matches", {"request": "job"}),
            expected(0, w + "/rules/0/wait", "due-4"),
            expected(0, w + "/rules/0/from", ["deployer"]),
            expected(0, w + "/rules/0/to", ["lead"]),
            expected(0, w + "/rules/0/meta/line", line_of(DEPLOY, OK_ROW)),
            expected(0, w + "/rules/1/id", "rule-2"),
            expected(0, w + "/rules/1/literals", {"retryable": True}),
            expected(0, w + "/rules/1/target", "retrying"),
            expected(0, w + "/rules/2/literals", {"retryable": False}),
            expected(0, w + "/rules/2/meta/line", line_of(DEPLOY, FALSE_ROW)),
            expected(0, "/ok/states/retrying/rules/0/id", "rule-5"),
            expected(0, "/ok/states/retrying/rules/0/target", "deploying"),
            expected(0, "/ok/states/retrying/rules/1/id", "rule-6"),
        ],
        ["DSL-004", "DSL-006", "DSL-008"],
        "Syntax U, explicit form. Core syntax: the compiler records contract.replies/1 in card.extensions (COVERAGE "
        "lead decisions 1 and 3). Shape per ubf-replies/COMPILED-SHAPE.md: alternatives first in W's rules, kinds in "
        "first-appearance order; `=>` opens the block, `|` adds an alternative, `| @d & T` closes it.",
    )

    add(
        "compile-one-review",
        [compile_request(ONE_REVIEW, roles="lead, reviewer", name="one-review")],
        [
            expected(0, "/ok/card/extensions", ["contract.replies/1"]),
            expected(0, "/ok/states/reviewing/wait/id", "due-2"),
            expected(0, "/ok/states/reviewing/wait/heads", ["rule-0"]),
            expected(0, "/ok/states/reviewing/wait/alternatives", ["rule-1"]),
            expected(0, "/ok/states/reviewing/wait/after_ms", 300000),
        ],
        ["DSL-004", "DSL-008"],
        "W needs no state section; a bare single alternative covers its kind.",
    )

    documented = replace_once(
        DEPLOY,
        OK_ROW,
        '    """\n    Deployed.\n    """\n    %% a comment\n'
        "    /* a note */\n" + OK_ROW,
    )
    documented = replace_once(
        documented, TRUE_ROW, TRUE_ROW.replace("$job, ", "$job , ")
    )
    documented = replace_once(
        documented, FALSE_ROW, '    """\n    Final.\n    """\n' + FALSE_ROW
    )
    add(
        "compile-doc-comment-note",
        [compile_request(documented)],
        [
            expected(0, "/ok/states/deploying/rules/0/meta/doc", "Deployed."),
            expected(
                0,
                "/ok/states/deploying/rules/0/meta/line",
                line_of(documented, '    """'),
            ),
            expected(0, "/ok/states/deploying/rules/1/literals", {"retryable": True}),
            expected(0, "/ok/states/deploying/rules/2/meta/doc", "Final."),
            expected(
                0,
                "/ok/states/deploying/rules/2/meta/line",
                line_of(documented, '    """', 3),
            ),
        ],
        ["DSL-004", "DSL-005"],
        "Documentation, %% comments and /* notes */ may sit before the `=>` line and before "
        "any `|` line; documentation attaches to the next alternative, whose meta.line is the prompt's opening line, "
        "as for every documented rule (Ada, op-amhns7e5); spaces around reply-call commas.",
    )

    # ---- the implicit-correlation sugar (SYNTAX-OPTIONS.md) -------------------------------
    add(
        "compile-sugar-deploy",
        [compile_request(SUGAR)],
        [
            expected(0, "/ok/card/extensions", ["contract.replies/1"]),
            expected(0, "/ok/states/new/rules/0/bind", "deploying"),
            expected(0, "/ok/states/new/rules/0/target", "deploying"),
            expected(0, "/ok/states/retrying/rules/0/bind", "deploying"),
            expected(0, w + "/wait/heads", ["rule-0", "rule-5"]),
            expected(0, w + "/wait/alternatives", ["rule-1", "rule-2", "rule-3"]),
            expected(0, w + "/rules/0/matches", {"request": "deploying"}),
            expected(0, w + "/rules/1/matches", {"request": "deploying"}),
            expected(0, w + "/rules/2/matches", {"request": "deploying"}),
            expected(0, w + "/rules/1/literals", {"retryable": True}),
        ],
        ["DSL-004", "DSL-008"],
        "Implicit head binding: every head without `as` (declaring head and re-send) binds W's "
        "own name. Implicit =$: the single ref(deploy) field of ok and error is filled with that name.",
    )
    explicit_w = DEPLOY.replace("$job", "$deploying").replace(
        " as job ", " as deploying "
    )
    add(
        "compile-sugar-equals-explicit",
        [compile_request(SUGAR), compile_request(explicit_w, handle="c2")],
        [
            expected(0, "/ok/states/new/rules/0/bind", "deploying"),
            expected(1, "/ok/states", {"$from": [0, "/ok/states"]}),
            expected(1, "/ok/card/extensions", {"$from": [0, "/ok/card/extensions"]}),
        ],
        ["DSL-004", "DSL-008"],
        "The sugar is pure surface: the same lines written `as deploying` / `request=$deploying` "
        "compile to the same states.",
    )
    head_only = (
        sugared(DEPLOY)
        .replace("(retryable=", "(request=$deploying, retryable=")
        .replace("ok()", "ok(request=$deploying)")
    )
    add(
        "compile-sugar-implicit-head-binding",
        [compile_request(head_only)],
        [
            expected(0, "/ok/states/new/rules/0/bind", "deploying"),
            expected(0, "/ok/states/retrying/rules/0/bind", "deploying"),
            expected(0, w + "/rules/0/matches", {"request": "deploying"}),
        ],
        ["DSL-004", "DSL-008"],
        "Heads without `as`; alternatives name the implicit binding explicitly.",
    )
    ref_only = DEPLOY.replace("(request=$job, ", "(").replace("(request=$job)", "()")
    add(
        "compile-sugar-implicit-ref",
        [compile_request(ref_only)],
        [
            expected(0, "/ok/states/new/rules/0/bind", "job"),
            expected(0, w + "/rules/0/matches", {"request": "job"}),
            expected(0, w + "/rules/1/matches", {"request": "job"}),
            expected(0, w + "/rules/2/matches", {"request": "job"}),
        ],
        ["DSL-004", "DSL-008"],
        "Heads bind `as job`; no alternative writes =$, so the compiler fills request=$job.",
    )
    add(
        "compile-sugar-one-review",
        [compile_request(REVIEW_SUGAR, roles="lead, reviewer", name="one-review")],
        [
            expected(0, "/ok/card/extensions", ["contract.replies/1"]),
            expected(0, "/ok/states/new/rules/0/target", "request_wait"),
            expected(0, "/ok/states/new/rules/0/bind", "request_wait"),
            expected(0, "/ok/states/request_wait/wait/id", "due-2"),
            expected(0, "/ok/states/request_wait/wait/heads", ["rule-0"]),
            expected(0, "/ok/states/request_wait/wait/alternatives", ["rule-1"]),
            expected(
                0,
                "/ok/states/request_wait/rules/0/matches",
                {"request": "request_wait"},
            ),
            expected(0, "/ok/states/request_wait/max_entries", None),
        ],
        ["DSL-004", "DSL-008"],
        "No `awaits`: W is named <kind>_wait (SYNTAX-OPTIONS.md U), and the head binds it.",
    )
    two_refs = replace_once(
        DEPLOY,
        "  retryable: boolean\n",
        "  origin: ref(deploy)\n  retryable: boolean\n",
    )
    both_refs = two_refs.replace(
        "error(request=$job, retryable=", "error(request=$job, origin=$job, retryable="
    )
    add(
        "compile-several-refs-explicit",
        [compile_request(both_refs)],
        [
            expected(0, w + "/rules/1/matches", {"request": "job", "origin": "job"}),
            expected(0, w + "/rules/2/matches", {"request": "job", "origin": "job"}),
        ],
        ["DSL-004", "DSL-008"],
        "Two ref(deploy) fields: legal when the error alternatives write the references; the "
        "existing unmatched_reference rule still needs every ref field matched, so both are written.",
    )
    timed_ok = replace_once(DEPLOY, ABANDON, TIMED)
    add(
        "compile-timed-rule-pipe",
        [compile_request(timed_ok)],
        [
            expected(0, "/ok/states/retrying/rules/1/target", "failed"),
            expected(0, "/ok/states/retrying/deadlines/0/kind", "abandon"),
            expected(0, "/ok/states/retrying/deadlines/0/after_ms", 300000),
            expected(0, "/ok/states/retrying/deadlines/0/outcome", "stalled"),
            expected(0, w + "/wait/alternatives", ["rule-1", "rule-2", "rule-3"]),
            expected(0, w + "/wait/after_ms", 600000),
        ],
        ["DSL-004", "DSL-008"],
        "Paired with syntax-pipe-after-timed-rule (N4): a timed rule's mid-line `|` keeps its "
        "meaning beside a block whose lines start with `|`.",
    )

    # ---- reply_route --------------------------------------------------------------------
    escape = (
        replace_once(DEPLOY, "type abandon\n", "type abandon\n\ntype quit\n")
        + "\nanystate\n"
        + QUIT_MID
        + "\n"
    )
    compile_error(
        "route-anystate",
        escape,
        "reply_route",
        line_of(escape, QUIT_MID),
        "RULE.md §A.3 L0: an anystate rule that applies in W obeys the W rule, so the replier's anystate "
        "move into a non-end state is reply_route. An anystate head is legal (A1b): compile-anystate-head.",
    )
    multi_head = "  lead -> {deployer,auditor}: deploy() as job awaits deploying"
    multi = replace_once(DEPLOY, "state new\n" + HEAD, "state new\n" + multi_head)
    compile_error(
        "route-two-recipients",
        multi,
        "reply_route",
        line_of(multi, multi_head),
        roles="lead, deployer, auditor",
    )
    event_head = "  event lead -> deployer: deploy() as job awaits deploying"
    event = replace_once(DEPLOY, "state new\n" + HEAD, "state new\n" + event_head)
    compile_error(
        "route-event-head",
        event,
        "reply_route",
        line_of(event, event_head),
        "Lead decision 3 pick 4: an event head is reply_route.",
    )
    renew_head = "  lead -> deployer: deploy() as job awaits @self"
    renew = replace_once(
        DEPLOY,
        "state retrying\n" + RESEND + "\n",
        "state retrying\n"
        + renew_head
        + "\n"
        + f"{OK_ROW}\n{TRUE_ROW}\n{FALSE_ROW}\n{TIMEOUT}"
        + "\n",
    )
    compile_error(
        "route-renewal-head",
        renew,
        "reply_route",
        line_of(renew, renew_head),
        "Lead decision 7 pick 3: a renewal after awaits is a code (the grammar accepts identifier or reset).",
    )
    arrow_renew_head = "  lead -> deployer: deploy() as job => @self"
    arrow_renew = replace_once(renew, renew_head, arrow_renew_head)
    compile_error(
        "route-renewal-arrow-head",
        arrow_renew,
        "reply_route",
        line_of(arrow_renew, arrow_renew_head),
        "PRODUCTIONS.md (U): a head with a target or renewal; a head names its wait with awaits or not at all.",
    )
    target_head = "  lead -> deployer: deploy() as job => deploying"
    targeted = replace_once(DEPLOY, "state new\n" + HEAD, "state new\n" + target_head)
    compile_error(
        "route-head-with-target",
        targeted,
        "reply_route",
        line_of(targeted, target_head),
        "The B-style head `=> W` followed by alternatives is reply_route in U (PRODUCTIONS.md).",
    )
    entry = replace_once(ENTRY_OK, ENTRY, ENTRY_OTHER_ROUTE)
    compile_error(
        "route-non-head-entry",
        entry,
        "reply_route",
        line_of(entry, ENTRY_OTHER_ROUTE),
        "RULE.md §A.2 A4: an entry of another kind needs the declaring head's route; the same entry on the "
        "head's route compiles (compile-entry-other-kind).",
    )
    aside = "  lead -> auditor: deploy() as job => deploying"
    other_route = replace_once(
        DEPLOY, "state retrying\n" + RESEND, "state retrying\n" + aside
    )
    compile_error(
        "route-resend-other-recipient",
        other_route,
        "reply_route",
        line_of(other_route, aside),
        "Lead decision 7 pick 1: a different route is not a re-send, so it is an entry into W that is not a head.",
        roles="lead, deployer, auditor",
    )
    other = replace_once(
        DEPLOY,
        "state retrying\n" + RESEND,
        "state retrying\n  lead -> deployer: deploy() as other => deploying",
    )
    compile_error(
        "correlation-resend-different-as",
        other,
        "reply_correlation",
        line_of(other, "  lead -> deployer: deploy() as other => deploying"),
        "PRODUCTIONS.md: heads with different as names are reply_correlation.",
    )
    quit_ = replace_once(DEPLOY, "type abandon\n", "type abandon\n\ntype quit\n")
    quit_ = replace_once(quit_, W_LINE + "\n", W_LINE + "\n" + QUIT_MID + "\n")
    compile_error(
        "route-replier-exit-in-w",
        quit_,
        "reply_route",
        line_of(quit_, QUIT_MID),
        "RULE.md §A A1: the replier leaves W only by a counter-request to W's requester or into an end "
        "terminal; retrying is neither. Into an end terminal it compiles (compile-replier-exit-to-end).",
    )
    timed = replace_once(DEPLOY, W_LINE + "\n", W_LINE + "\n" + TIMED + "\n")
    compile_error("route-timed-rule-in-w", timed, "reply_route", line_of(timed, TIMED))

    # ---- reply_match --------------------------------------------------------------------
    text_row = "     | error(request=$job, reason=boom) & failed"
    text = replace_once(DEPLOY, FALSE_ROW, text_row)
    compile_error(
        "match-text-literal",
        text,
        "reply_match",
        line_of(text, text_row),
        "Per-line checks run before the set checks (overlap, coverage).",
    )
    domain_row = "     | error(request=$job, retryable=maybe) & failed"
    domain = replace_once(DEPLOY, FALSE_ROW, domain_row)
    compile_error(
        "match-outside-domain", domain, "reply_match", line_of(domain, domain_row)
    )
    repeat_row = "     | error(request=$job, retryable=false, retryable=false) & failed"
    repeat = replace_once(DEPLOY, FALSE_ROW, repeat_row)
    compile_error(
        "match-repeated-field", repeat, "reply_match", line_of(repeat, repeat_row)
    )
    origin_row = "     | error(origin=$job, retryable=false) & failed"
    refs = replace_once(two_refs, FALSE_ROW, origin_row)
    compile_error(
        "match-different-references",
        refs,
        "reply_match",
        line_of(refs, origin_row),
        "Same kind, different =$ fields: reported at the later alternative.",
    )
    as_row = "     | error(request=$job, retryable=false) as answer & failed"
    mixed = replace_once(DEPLOY, FALSE_ROW, as_row)
    compile_error(
        "match-mixed-as",
        mixed,
        "reply_match",
        line_of(mixed, as_row),
        "Only the last alternative binds: the later line of the disagreement.",
    )

    # ---- reply_overlap / reply_not_exhaustive -------------------------------------------
    dup_row = "     | error(request=$job, retryable=true) & failed"
    overlap = replace_once(DEPLOY, FALSE_ROW + "\n", FALSE_ROW + "\n" + dup_row + "\n")
    compile_error(
        "overlap-duplicate-row",
        overlap,
        "reply_overlap",
        line_of(overlap, dup_row),
        "Reported at the later line.",
    )
    gap = replace_once(DEPLOY, FALSE_ROW + "\n", "")
    compile_error(
        "not-exhaustive-missing-false",
        gap,
        "reply_not_exhaustive",
        line_of(gap, HEAD),
        "Reported at the head line.",
    )
    s_gap = replace_once(SUGAR, "     | error(retryable=false) & failed\n", "")
    compile_error(
        "not-exhaustive-sugar",
        s_gap,
        "reply_not_exhaustive",
        line_of(s_gap, S_HEAD),
        "The same gap in the sugared form; the implicit =$ does not change coverage.",
    )

    # ---- reply_correlation (COVERAGE lead decision 2, SYNTAX-OPTIONS sugar) / reply_cycle -----
    mixed_heads = replace_once(
        DEPLOY, "state retrying\n" + RESEND, "state retrying\n" + S_RESEND
    )
    compile_error(
        "correlation-resend-implicit-vs-as",
        mixed_heads,
        "reply_correlation",
        line_of(mixed_heads, S_RESEND),
        "The declaring head binds `as job`; a re-send without `as` binds W's own name (deploying), so the "
        "heads disagree. Reported at the re-send.",
    )
    no_ref = replace_once(DEPLOY, "type ok\n  request: ref(deploy)\n", "type ok\n")
    no_ref = replace_once(no_ref, OK_ROW, S_OK_ROW)
    compile_error(
        "correlation-no-ref-field",
        no_ref,
        "reply_correlation",
        line_of(no_ref, S_OK_ROW),
        "Implicit =$ needs a ref field whose kinds include the head's kind; ok has none.",
    )
    other_ref = replace_once(
        DEPLOY,
        "type ok\n  request: ref(deploy)\n",
        "type ok\n  request: ref(abandon)\n",
    )
    other_ref = replace_once(other_ref, OK_ROW, S_OK_ROW)
    compile_error(
        "correlation-ref-other-kind",
        other_ref,
        "reply_correlation",
        line_of(other_ref, S_OK_ROW),
        "ok's only ref field references abandon, not the head's kind deploy.",
    )
    several = replace_once(two_refs, TRUE_ROW, S_TRUE_ROW)
    compile_error(
        "correlation-several-refs-implicit",
        several,
        "reply_correlation",
        line_of(several, S_TRUE_ROW),
        "error has two ref(deploy) fields and this alternative writes no =$: the author must choose.",
    )
    several_s = replace_once(
        SUGAR, "  retryable: boolean\n", "  origin: ref(deploy)\n  retryable: boolean\n"
    )
    compile_error(
        "correlation-several-refs-sugar",
        several_s,
        "reply_correlation",
        line_of(several_s, S_TRUE_ROW),
        "The same with an implicit head binding: the first error alternative is the offending line.",
    )
    rebind = DEPLOY + "\nanystate\n" + REBIND_EVENT + "\n"
    compile_error(
        "correlation-rebind-anystate",
        rebind,
        "reply_correlation",
        line_of(rebind, REBIND_EVENT),
        "RULE.md §A.3 A1-1: only a rule that always transitions may rebind W's name; an anystate event "
        "never does. The anystate rule form compiles (compile-rebind-anystate-rule).",
    )
    rebind_w = replace_once(
        DEPLOY, W_LINE + "\n", W_LINE + "\n  event lead -> deployer: abandon() as job\n"
    )
    compile_error(
        "correlation-rebind-in-w",
        rebind_w,
        "reply_correlation",
        line_of(rebind_w, "  event lead -> deployer: abandon() as job"),
        "Rebinding inside `state W`, not only anystate.",
    )
    rebind_s = replace_once(
        SUGAR,
        W_LINE + "\n",
        W_LINE + "\n  event lead -> deployer: abandon() as deploying\n",
    )
    compile_error(
        "correlation-rebind-implicit-name",
        rebind_s,
        "reply_correlation",
        line_of(rebind_s, "  event lead -> deployer: abandon() as deploying"),
        "The implicit binding (W's name) is protected like an explicit one.",
    )
    wrong = replace_once(
        DEPLOY,
        "state new\n" + HEAD,
        "state new\n  lead -> deployer: deploy() as other => prepared\n\n"
        "state prepared\n" + HEAD,
    )
    wrong_row = "    => ok(request=$other) & deployed"
    wrong = replace_once(wrong, OK_ROW, wrong_row)
    compile_error(
        "correlation-wrong-binding",
        wrong,
        "reply_correlation",
        line_of(wrong, wrong_row),
        "=$ names a bound deploy message, but not the head's own binding.",
    )
    cycle = replace_once(DEPLOY, W_LINE, "state deploying")
    compile_error(
        "cycle-without-max-entries",
        cycle,
        "reply_cycle",
        line_of(cycle, HEAD),
        "W reaches itself through retrying; reported at the declaring head line.",
    )

    # ---- duplicate: the implicit <kind>_wait name ---------------------------------------------
    clash = REVIEW_SUGAR + "\nend request_wait\n"
    compile_error(
        "wait-name-clash-end",
        clash,
        "duplicate",
        line_of(clash, "end request_wait"),
        "Without awaits W is request_wait, which the contract also declares as a terminal: the later line.",
        roles="lead, reviewer",
        name="one-review",
    )
    section = replace_once(
        REVIEW_SUGAR,
        "end collected\n",
        "state request_wait max_entries 3 => stalled\n\nend collected\n",
    )
    compile_error(
        "wait-state-section-without-awaits",
        section,
        "duplicate",
        line_of(section, "state request_wait max_entries 3 => stalled"),
        "A state W section requires awaits (SYNTAX-OPTIONS.md U); without it the section clashes with the "
        "implicit W.",
        roles="lead, reviewer",
        name="one-review",
    )
    branches = replace_once(
        REVIEW_SUGAR,
        "state new\n"
        + R_HEAD
        + "\n    => review() & collected\n     | @5m & stalled\n",
        "type pick_a\n\ntype pick_b\n\nstate new\n  lead -> reviewer: pick_a() => a\n"
        "  lead -> reviewer: pick_b() => b\n\nstate a\n"
        + R_HEAD
        + "\n    => review() & collected\n     | @5m & stalled\n\nstate b\n"
        + R_HEAD
        + "\n    => review() & collected\n     | @5m & stalled\n",
    )
    compile_error(
        "route-second-block-implied",
        branches,
        "reply_route",
        line_of(branches, R_HEAD, 2),
        'Two blocks without awaits on the same kind both declare request_wait: PRODUCTIONS.md (U) "a second '
        'block for the same W" is reply_route, at the later head.',
        roles="lead, reviewer",
        name="one-review",
    )

    # ---- reply_timeout ------------------------------------------------------------------
    missing = replace_once(DEPLOY, TIMEOUT + "\n\n" + W_LINE, W_LINE)
    compile_error(
        "timeout-missing",
        missing,
        "reply_timeout",
        line_of(missing, HEAD),
        "Lead decision 3: the head line.",
    )
    early = replace_once(DEPLOY, OK_ROW + "\n", OK_ROW + "\n" + TIMEOUT + "\n")
    early = replace_once(early, FALSE_ROW + "\n" + TIMEOUT + "\n", FALSE_ROW + "\n")
    compile_error(
        "timeout-before-reply",
        early,
        "reply_timeout",
        line_of(early, TIMEOUT),
        "The timeout must be the last alternative; an early one reports at its own line.",
    )
    lonely = replace_once(
        DEPLOY,
        OK_ROW + "\n" + TRUE_ROW + "\n" + FALSE_ROW + "\n" + TIMEOUT,
        "    => @10m & stalled",
    )
    compile_error(
        "timeout-no-reply",
        lonely,
        "reply_timeout",
        line_of(lonely, "    => @10m & stalled"),
    )
    twice = replace_once(DEPLOY, TIMEOUT + "\n", TIMEOUT + "\n     | @20m & stalled\n")
    compile_error(
        "timeout-repeated",
        twice,
        "reply_timeout",
        line_of(twice, "     | @20m & stalled"),
    )
    pipe_first = replace_once(DEPLOY, OK_ROW, "     | ok(request=$job) & deployed")
    compile_error(
        "timeout-first-leader-pipe",
        pipe_first,
        "reply_timeout",
        line_of(pipe_first, "     | ok(request=$job) & deployed"),
        "PRODUCTIONS.md (U): the first alternative must start with `=>`; reported at that alternative.",
    )
    arrow_later = replace_once(
        DEPLOY, TRUE_ROW, "    => error(request=$job, retryable=true) & retrying"
    )
    compile_error(
        "timeout-later-leader-arrow",
        arrow_later,
        "reply_timeout",
        line_of(arrow_later, "    => error(request=$job, retryable=true) & retrying"),
        "A later alternative must start with `|`; reported at that alternative.",
    )
    awaits_resend = "  lead -> deployer: deploy() as job awaits deploying"
    no_block = replace_once(
        DEPLOY, "state retrying\n" + RESEND, "state retrying\n" + awaits_resend
    )
    compile_error(
        "timeout-awaits-without-block",
        no_block,
        "reply_timeout",
        line_of(no_block, awaits_resend, 2),
        "Lead decision 7: `awaits W` with no block is reply_timeout at its head line (a re-send is `=> W`).",
    )
    nonterminal = replace_once(DEPLOY, TIMEOUT, "     | @10m & retrying")
    compile_error(
        "timeout-not-terminal",
        nonterminal,
        "unknown_state",
        line_of(nonterminal, "     | @10m & retrying"),
        "The existing deadline check (a timed rule naming a state as its outcome is unknown_state).",
    )

    # ---- reply_limit ----------------------------------------------------------------------
    members = [f"s{i}" for i in range(1, 18)]
    wide = replace_once(
        DEPLOY,
        "  request: ref(deploy)\n\ntype error",
        "  request: ref(deploy)\n  status: enum("
        + "|".join(members)
        + ")\n\ntype error",
    )
    wide = replace_once(
        wide,
        OK_ROW + "\n",
        "    => ok(request=$job, status=s1) & deployed\n"
        + "".join(
            f"     | ok(request=$job, status={m}) & deployed\n" for m in members[1:]
        ),
    )
    compile_error(
        "limit-17-alternatives",
        wide,
        "reply_limit",
        line_of(wide, "     | ok(request=$job, status=s17) & deployed"),
        "Reported at the 17th alternative.",
    )
    flags = "".join(f"  b{i}: boolean\n" for i in range(1, 6))
    five = replace_once(
        DEPLOY, "  retryable: boolean\n", "  retryable: boolean\n" + flags
    )
    five_row = "     | error(request=$job, retryable=false, b1=true, b2=true, b3=true, b4=true) & failed"
    five = replace_once(five, FALSE_ROW, five_row)
    compile_error(
        "limit-5-literals",
        five,
        "reply_limit",
        line_of(five, five_row),
        "The cap is checked before coverage (S4: bounded work first).",
    )
    states = []
    for i in range(17):
        target = f"s{i + 1}" if i < 16 else "done"
        alts = "".join(
            f"    {'=>' if j == 1 else ' |'} pong(request=$job, v=m{j}) & {target}\n"
            for j in range(1, 17)
        )
        states.append(
            f"state {'new' if i == 0 else f's{i}'}\n  lead -> deployer: ping() as job awaits w{i}\n"
            f"{alts}     | @10m & stalled\n"
        )
    big = (
        "contract @1h => expired\n\ntype ping\n\ntype pong\n  request: ref(ping)\n  v: enum("
        + "|".join(f"m{j}" for j in range(1, 17))
        + ")\n\n"
        + "\n".join(states)
        + "\nend done\n\nend stalled\n"
    )
    rows_ = [
        i + 1
        for i, t in enumerate(big.split("\n"))
        if t.startswith(("    => pong(", "     | pong("))
    ]
    assert len(rows_) == 272 and big.split("\n")[rows_[256] - 1].startswith(
        "    => pong("
    )
    compile_error(
        "limit-257-in-contract",
        big,
        "reply_limit",
        rows_[256],
        "The 257th alternative in the contract (the `=>` line of the 17th block).",
    )

    # ---- an ordinary rule outside the block accepting an alternative's (role, kind) ----------
    amb = DEPLOY + "\nanystate\n  deployer -> lead: ok() => deployed\n"
    compile_error(
        "correlation-anystate-accepts-alternative",
        amb,
        "reply_correlation",
        line_of(amb, "  deployer -> lead: ok() => deployed"),
        "PRODUCTIONS.md (supersedes DESIGN N1 ambiguous_rule).",
    )

    # ---- syntax: block lines no production allows; the two meanings of `|` (N4) -------------
    timed_head = replace_once(
        DEPLOY,
        "state new\n" + HEAD,
        "state new\n  lead -> deployer: deploy() as job @5m => deploying | stalled",
    )
    compile_error(
        "syntax-after-timed-rule",
        timed_head,
        "syntax",
        line_of(timed_head, OK_ROW),
        "A timed rule (mid-line `|`) cannot head a block: its `=>` line is a syntax error.",
    )
    pipe_after_timed = replace_once(
        DEPLOY, ABANDON + "\n", TIMED + "\n" + TIMEOUT + "\n"
    )
    compile_error(
        "syntax-pipe-after-timed-rule",
        pipe_after_timed,
        "syntax",
        line_of(pipe_after_timed, TIMEOUT, 2),
        "Paired with compile-timed-rule-pipe: a line-start `|` after a timed rule is not a second timeout target.",
    )
    stray = replace_once(DEPLOY, W_LINE + "\n", W_LINE + "\n" + OK_ROW + "\n")
    compile_error(
        "syntax-after-state-line",
        stray,
        "syntax",
        line_of(stray, OK_ROW, 2),
        "A `=>` line after a non-rule line.",
    )
    stray_pipe = replace_once(DEPLOY, W_LINE + "\n", W_LINE + "\n" + TRUE_ROW + "\n")
    compile_error(
        "syntax-pipe-after-state-line",
        stray_pipe,
        "syntax",
        line_of(stray_pipe, TRUE_ROW, 2),
        "A `|` line after a non-rule line (the state line itself contains `=>`).",
    )
    one_line = OK_ROW + " | error(request=$job, retryable=true) & retrying"
    two_on_one = replace_once(DEPLOY, OK_ROW + "\n" + TRUE_ROW + "\n", one_line + "\n")
    compile_error(
        "syntax-two-alternatives-one-line",
        two_on_one,
        "syntax",
        line_of(two_on_one, one_line),
        "A mid-line `|` inside an alternative: one alternative per line, and only a timed rule uses `|` mid-line.",
    )
    tev = replace_once(DEPLOY, "type abandon\n", "type abandon\n\ntype progress\n")
    tev = replace_once(
        tev,
        W_LINE + "\n",
        W_LINE
        + "\n  event deployer -> lead: progress() @5m => stalled\n"
        + OK_ROW
        + "\n",
    )
    compile_error("syntax-after-timed-event", tev, "syntax", line_of(tev, OK_ROW, 2))

    # ---- RULE.md §A.3 (sha 05bfa5a1): A1, A1-1, A1-2, A1b, A1b-4, A4, T1, T1-2 -----------------
    any_head = replace_once(
        DEPLOY, "state new\n" + HEAD, "state new\n" + ABANDON + "\n\nanystate\n" + HEAD
    )
    any_head = replace_once(
        any_head, "state retrying\n" + RESEND + "\n", "state retrying\n"
    )
    add(
        "compile-anystate-head",
        [compile_request(any_head)],
        [
            expected(0, "/ok/any_rules/0/id", "rule-1"),
            expected(0, "/ok/any_rules/0/bind", "job"),
            expected(0, "/ok/any_rules/0/target", "deploying"),
            expected(0, w + "/wait/id", "due-5"),
            expected(0, w + "/wait/heads", ["rule-1"]),
            expected(0, w + "/wait/alternatives", ["rule-2", "rule-3", "rule-4"]),
        ],
        ["DSL-004", "DSL-008"],
        "A1b: a head may sit in anystate; it applies everywhere except in its own wait, so "
        "retrying needs no re-send (one there would be ambiguous_rule with the anystate head).",
    )
    timed_any = replace_once(any_head, HEAD, T1_HEAD)
    compile_error(
        "route-timed-anystate-head",
        timed_any,
        "reply_route",
        line_of(timed_any, T1_HEAD),
        "RULE.md §A.3 A1b-4: anystate holds no duties, so an anystate head is untimed. The same line in a "
        "state compiles (compile-timed-head).",
    )

    t1 = replace_once(DEPLOY, "state new\n" + HEAD, "state new\n" + T1_HEAD)
    add(
        "compile-timed-head",
        [compile_request(t1)],
        [
            expected(0, "/ok/states/new/rules/0/id", "rule-0"),
            expected(0, "/ok/states/new/rules/0/target", "deploying"),
            expected(0, "/ok/states/new/deadlines/0/id", "due-1"),
            expected(0, "/ok/states/new/deadlines/0/after_ms", 300000),
            expected(0, "/ok/states/new/deadlines/0/outcome", "stalled"),
            expected(0, "/ok/states/new/deadlines/0/participants", ["lead"]),
            expected(0, w + "/wait/id", "due-5"),
            expected(0, w + "/wait/heads", ["rule-0", "rule-6"]),
            expected(0, w + "/wait/alternatives", ["rule-2", "rule-3", "rule-4"]),
            expected(0, w + "/wait/after_ms", 600000),
        ],
        ["DSL-004", "DSL-008"],
        "RULE.md §A.2 T1: head rule-N, its duty due-(N+1) in the head's state, alternatives "
        "rule-(N+2)…, the wait timeout due-(N+k+2); the next declaration is rule-(N+k+3).",
    )
    own = "  lead -> deployer: deploy() as job @5m awaits new | stalled"
    t12 = replace_once(t1, T1_HEAD, own)
    compile_error(
        "route-timed-head-own-state",
        t12,
        "reply_route",
        line_of(t12, own),
        "RULE.md §A.3 T1-2: a T1 head never awaits its own state; otherwise its duty and its wait would "
        "coexist on every entry. One line away from compile-timed-head.",
    )

    add(
        "compile-entry-other-kind",
        [compile_request(ENTRY_OK)],
        [
            expected(0, w + "/wait/heads", ["rule-0", "rule-5", "rule-6"]),
            expected(0, "/ok/states/retrying/rules/1/kind", "abandon"),
            expected(0, "/ok/states/retrying/rules/1/bind", "job"),
            expected(0, "/ok/states/retrying/rules/1/target", "deploying"),
        ],
        ["DSL-004", "DSL-008"],
        "RULE.md §A.2 A4: an entry of another kind with the head's route and binding is a "
        "head of W when every alternative's correlated ref lists its kind (B1: wait.heads includes it).",
    )
    no_kind = ENTRY_OK.replace(
        "  request: ref(deploy|abandon)\n", "  request: ref(deploy)\n", 1
    )
    compile_error(
        "correlation-entry-kind-not-in-ref",
        no_kind,
        "reply_correlation",
        line_of(no_kind, ENTRY),
        "A4: ok's ref(deploy) does not list abandon; reported at the entering line.",
    )
    other_bind = "  lead -> deployer: abandon() as other => deploying"
    rebound = replace_once(ENTRY_OK, ENTRY, other_bind)
    compile_error(
        "correlation-entry-other-binding",
        rebound,
        "reply_correlation",
        line_of(rebound, other_bind),
        "A4: an entry of another kind must bind the declaring head's name.",
    )

    exit_end = replace_once(DEPLOY, "type abandon\n", "type abandon\n\ntype quit\n")
    exit_end = replace_once(exit_end, W_LINE + "\n", W_LINE + "\n" + QUIT_END + "\n")
    add(
        "compile-replier-exit-to-end",
        [compile_request(exit_end)],
        [
            expected(0, w + "/rules/3/kind", "quit"),
            expected(0, w + "/rules/3/from", ["deployer"]),
            expected(0, w + "/rules/3/target", "failed"),
            expected(0, w + "/wait/alternatives", ["rule-1", "rule-2", "rule-3"]),
        ],
        ["DSL-004", "DSL-008"],
        "RULE.md §A A1: the replier may leave W by a rule into an end terminal, whose outcome "
        "the requester sees.",
    )
    rebind_rule = (
        replace_once(DEPLOY, ABANDON + "\n", "")
        + "\nanystate\n  lead -> deployer: abandon() as job => failed\n"
    )
    add(
        "compile-rebind-anystate-rule",
        [compile_request(rebind_rule)],
        [
            expected(0, "/ok/any_rules/0/bind", "job"),
            expected(0, "/ok/any_rules/0/event", False),
            expected(0, "/ok/any_rules/0/target", "failed"),
        ],
        ["DSL-004", "DSL-008"],
        "RULE.md §A.3 A1-1: a rule (not an event) that applies in W may rebind W's name, "
        "because the transition ends the wait.",
    )

    asked = replace_once(
        DEPLOY,
        "type abandon\n",
        "type abandon\n\ntype question\n\ntype answer\n  question: ref(question)\n",
    )
    block = "\n    => answer(question=$q) & retrying\n     | @5m & stalled\n"
    ask_w = "\nstate asking max_entries 2 => exhausted\n"
    to_lead = "  deployer -> lead: question() as q awaits asking"
    to_auditor = "  deployer -> auditor: question() as q awaits asking"
    counter = replace_once(
        asked, W_LINE + "\n", W_LINE + "\n" + to_lead + block + ask_w
    )
    add(
        "compile-counter-request-to-requester",
        [compile_request(counter, roles="lead, deployer, auditor")],
        [
            expected(0, w + "/rules/3/id", "rule-5"),
            expected(0, w + "/rules/3/target", "asking"),
            expected(0, w + "/rules/3/to", ["lead"]),
            expected(0, "/ok/states/asking/wait/heads", ["rule-5"]),
            expected(0, "/ok/states/asking/wait/from", ["lead"]),
            expected(0, "/ok/states/asking/wait/to", ["deployer"]),
        ],
        ["DSL-004", "DSL-008"],
        "RULE.md §A.3 A1-2: in W the replier may leave by a head of another block addressed "
        "to W's requester (here the lead), with a third role in the contract.",
    )
    third = replace_once(counter, to_lead, to_auditor)
    compile_error(
        "route-counter-request-third-role",
        third,
        "reply_route",
        line_of(third, to_auditor),
        "A1-2: a counter-request whose recipient is not W's requester is reply_route.",
        roles="lead, deployer, auditor",
    )
    any_third = asked + "\nanystate\n" + to_auditor + block + ask_w
    compile_error(
        "route-anystate-counter-request-third-role",
        any_third,
        "reply_route",
        line_of(any_third, to_auditor),
        "A1-2 with L0: the anystate head applies in deploying, where the deployer is the replier.",
        roles="lead, deployer, auditor",
    )

    # ---- replay ------------------------------------------------------------------------
    start = [compile_request(DEPLOY), new()]
    add(
        "replay-ok-lands",
        start + [deploy(0, "d1"), ok(1000, "ok1", "d1")],
        [
            expected(2, "/decision/status", "accepted"),
            expected(2, "/state/state/state", "deploying"),
            expected(2, "/state/state/wait/id", "due-4"),
            expected(2, "/state/state/wait/due_at", 600000),
            expected(3, "/decision/status", "accepted"),
            expected(3, "/state/state/state", "deployed"),
            expected(3, "/state/state/wait", None),
        ],
        ["ENG-005", "ENG-006"],
        "The engine derives the state from the reply.",
    )
    add(
        "replay-error-retryable-lands",
        start + [deploy(0, "d1"), error(1000, "e1", "d1", True)],
        [
            expected(3, "/decision/status", "accepted"),
            expected(3, "/state/state/state", "retrying"),
            expected(3, "/state/state/wait", None),
        ],
        ["ENG-005", "ENG-006"],
    )
    add(
        "replay-error-final-lands",
        start + [deploy(0, "d1"), error(1000, "e1", "d1", False)],
        [
            expected(3, "/decision/status", "accepted"),
            expected(3, "/state/state/state", "failed"),
        ],
        ["ENG-005", "ENG-006"],
    )
    add(
        "replay-rejections-keep-wait",
        start
        + [
            deploy(0, "d1"),
            ok(1000, "bad-ref", "other"),
            step("d", 2000, "bad-payload", "error", {"request": "d1", "reason": "x"}),
            step("l", 3000, "bad-role", "ok", {"request": "d1"}),
            ok(4000, "ok1", "d1"),
        ],
        [
            expected(3, "/decision/status", "rejected"),
            expected(3, "/decision/reason", "reference"),
            expected(3, "/state/state/state", "deploying"),
            expected(3, "/state/state/version", 1),
            expected(3, "/state/state/wait/id", "due-4"),
            expected(4, "/decision/reason", "payload"),
            expected(4, "/state/state/state", "deploying"),
            expected(5, "/decision/reason", "illegal_message"),
            expected(5, "/state/state/wait/due_at", 600000),
            expected(6, "/decision/status", "accepted"),
            expected(6, "/state/state/state", "deployed"),
        ],
        ["ENG-005", "ENG-007"],
        "Precedence illegal_message -> payload -> reference; the wait stays open after each.",
    )
    add(
        "replay-timeout-at-d",
        start + [deploy(0, "d1"), tick(599999), tick(600000), ok(600001, "late", "d1")],
        [
            expected(3, "/state/state/state", "deploying"),
            expected(4, "/state/state/state", "stalled"),
            expected(4, "/state/state/outcome", "stalled"),
            expected(5, "/decision/reason", "terminal"),
        ],
        ["ENG-009"],
        "Inclusive boundary: the timeout fires at exactly d.",
    )
    add(
        "replay-resend-new-wait",
        start
        + [
            deploy(0, "d1"),
            error(100000, "e1", "d1", True),
            deploy(200000, "d2"),
            tick(799999),
            tick(800000),
        ],
        [
            expected(4, "/decision/status", "accepted"),
            expected(4, "/state/state/state", "deploying"),
            expected(4, "/state/state/entries/deploying", 2),
            expected(4, "/state/state/wait/due_at", 800000),
            expected(5, "/state/state/state", "deploying"),
            expected(6, "/state/state/state", "stalled"),
        ],
        ["ENG-005", "ENG-009", "ENG-011"],
        "A re-send creates a new wait timed from re-entry. (A late reply to d1 is Felix's C1 vector.)",
    )
    add(
        "replay-max-entries",
        start
        + [
            deploy(0, "d1"),
            error(1, "e1", "d1", True),
            deploy(2, "d2"),
            error(3, "e2", "d2", True),
            deploy(4, "d3"),
            error(5, "e3", "d3", True),
            deploy(6, "d4"),
        ],
        [
            expected(6, "/state/state/entries/deploying", 3),
            expected(8, "/decision/status", "accepted"),
            expected(8, "/state/state/state", "exhausted"),
        ],
        ["ENG-011"],
        "The fourth entry into W selects max_entries' outcome.",
    )
    add(
        "replay-checkpoint-restore-in-wait",
        start
        + [
            deploy(0, "d1"),
            {"op": "checkpoint", "handle": "s"},
            {
                "op": "restore",
                "contract": "c",
                "handle": "s2",
                "checkpoint": {"$from": [3, "/ok"]},
                "now": 0,
            },
            {"op": "checkpoint", "handle": "s2"},
            ok(1000, "ok1", "d1", handle="s2", session="s"),
        ],
        [
            expected(4, "/ok/state/state", "deploying"),
            expected(4, "/ok/state/wait/id", "due-4"),
            expected(4, "/ok/state/wait/due_at", 600000),
            expected(5, "/ok", {"$from": [3, "/ok"]}),
            expected(6, "/decision/status", "accepted"),
            expected(6, "/state/state/state", "deployed"),
        ],
        ["ENG-015"],
        "The saved wait survives restore, and checkpoint(restore(checkpoint(s))) equals checkpoint(s).",
    )
    review = [
        compile_request(ONE_REVIEW, roles="lead, reviewer", name="one-review"),
        new(bindings={"lead": "l", "reviewer": "r"}),
    ]
    add(
        "replay-one-review",
        review
        + [
            step("l", 0, "q1", "request", {"text": "Check this"}),
            step("r", 10, "v1", "review", {"request": "q1", "text": "Fine"}),
            new(handle="t", bindings={"lead": "l", "reviewer": "r"}),
            step("l", 0, "q2", "request", {"text": "Check this"}, handle="t"),
            tick(300000, handle="t"),
        ],
        [
            expected(3, "/state/state/state", "collected"),
            expected(6, "/state/state/state", "stalled"),
        ],
        ["ENG-005", "ENG-009"],
    )
    add(
        "replay-sugar-reference-checked",
        [
            compile_request(SUGAR),
            new(),
            deploy(0, "d1"),
            ok(1000, "bad-ref", "other"),
            ok(2000, "ok1", "d1"),
        ],
        [
            expected(2, "/state/state/state", "deploying"),
            expected(3, "/decision/status", "rejected"),
            expected(3, "/decision/reason", "reference"),
            expected(3, "/state/state/state", "deploying"),
            expected(4, "/decision/status", "accepted"),
            expected(4, "/state/state/state", "deployed"),
        ],
        ["ENG-005", "ENG-007"],
        "SYNTAX-OPTIONS sugar: the engine still checks the implicit reference, so a reply to "
        "another request is rejected.",
    )
    add(
        "replay-sugar-resend",
        [
            compile_request(SUGAR),
            new(),
            deploy(0, "d1"),
            error(1000, "e1", "d1", True),
            deploy(2000, "d2"),
            ok(3000, "ok2", "d2"),
        ],
        [
            expected(4, "/decision/status", "accepted"),
            expected(4, "/state/state/state", "deploying"),
            expected(4, "/state/state/entries/deploying", 2),
            expected(4, "/state/state/wait/due_at", 602000),
            expected(5, "/decision/status", "accepted"),
            expected(5, "/state/state/state", "deployed"),
        ],
        ["ENG-005", "ENG-011"],
        "Lead decision 6: a re-send is a plain `=> W` rule with the implied binding; it reuses "
        "W's block, and the reply to the new request lands.",
    )
    add(
        "replay-sugar-one-review",
        [
            compile_request(REVIEW_SUGAR, roles="lead, reviewer", name="one-review"),
            new(bindings={"lead": "l", "reviewer": "r"}),
            step("l", 0, "q1", "request", {"text": "Check this"}),
            step("r", 10, "v1", "review", {"request": "q1", "text": "Fine"}),
        ],
        [
            expected(2, "/state/state/state", "request_wait"),
            expected(2, "/state/state/wait/id", "due-2"),
            expected(3, "/decision/status", "accepted"),
            expected(3, "/state/state/state", "collected"),
        ],
        ["ENG-005", "ENG-006"],
        "The implicit W request_wait is a real state.",
    )
    return rows


def format_cases() -> list[dict[str, Any]]:
    """Formatter vectors: hand-written canonical output (SYNTAX-OPTIONS.md U, "Format"): `=>` at 4
    spaces under a state rule, each `|` at 5 so the alternatives line up, one alternative per line,
    a doc, comment or note line before an alternative at that alternative's indent (lead decision 6:
    4 before `=>`, 5 before `|`), and the sugar never added or removed.
    The runner checks both formatters: once == expected, twice == once (idempotence), Elixir == Python."""
    header = HEADER.format(roles="lead, deployer")
    doc = '    """\n    Deployed.\n    """\n'
    canonical = replace_once(DEPLOY, OK_ROW + "\n", doc + OK_ROW + "\n")
    messy = replace_once(
        DEPLOY,
        HEAD
        + "\n"
        + OK_ROW
        + "\n"
        + TRUE_ROW
        + "\n"
        + FALSE_ROW
        + "\n"
        + TIMEOUT
        + "\n\n",
        HEAD.strip()
        + '\n  """\n  Deployed.\n  """\n'
        + OK_ROW.strip()
        + "\n        "
        + TRUE_ROW.strip()
        + "\n  "
        + FALSE_ROW.strip()
        + "\n      "
        + TIMEOUT.strip()
        + "\n\n\n\n",
    )
    timed = replace_once(DEPLOY, ABANDON, TIMED)
    timed_messy = replace_once(timed, TIMED, "   " + TIMED)
    timed_messy = replace_once(
        timed_messy, TIMEOUT, TIMEOUT.strip().rjust(len(TIMEOUT) - 3)
    )
    pipe_doc = replace_once(
        DEPLOY,
        FALSE_ROW + "\n",
        '     %% retry later\n     """\n     Final.\n     """\n' + FALSE_ROW + "\n",
    )
    pipe_doc_messy = replace_once(
        DEPLOY,
        FALSE_ROW + "\n",
        '%% retry later\n  """\n  Final.\n  """\n' + FALSE_ROW + "\n",
    )
    return [
        {
            "id": "v2-reply-format-block",
            "input": header + messy,
            "expected": header + canonical,
            "notes": "Indentation of the head, `=>`, every `|` and a prompt inside the block; runs of blank lines.",
        },
        {
            "id": "v2-reply-format-canonical",
            "input": header + canonical,
            "expected": header + canonical,
            "notes": "The canonical explicit form is a fixed point: the formatter never removes `as`/`=$`.",
        },
        {
            "id": "v2-reply-format-sugar-canonical",
            "input": header + SUGAR,
            "expected": header + SUGAR,
            "notes": "The sugared form is a fixed point: the formatter never adds `as`/`=$`.",
        },
        {
            "id": "v2-reply-format-timed-pipe",
            "input": header + timed_messy,
            "expected": header + timed,
            "notes": "N4 pair: a timed rule indented like an alternative goes back to 2 spaces with its mid-line `|` "
            "untouched; a block `|` line indented like a rule goes to 5.",
        },
        {
            "id": "v2-reply-format-doc-before-pipe",
            "input": header + pipe_doc_messy,
            "expected": header + pipe_doc,
            "notes": "Lead decision 6: a comment and a prompt before a `|` alternative sit at its indent (5).",
        },
    ]


PROMPTED_REVIEW = '''"""
One lead asks one reviewer for a review.
"""
contract @1h => expired
"""
The lead asks for the review.
"""
role lead
"""
The reviewer answers with a review.
"""
role reviewer
"""
A request for a review.
"""
type request
  text: text(1..2000)
"""
The review of one request.
"""
type review
  request: ref(request)
  text: text(1..4000)
"""
Nothing has been asked yet.
"""
state new
  """
  Waiting for the reviewer's review of this request.
  """
  lead -> reviewer: request()
    => review() & collected
     | @5m & stalled
end collected
end stalled
'''
HEAD_PROMPT = '  """\n  Waiting for the reviewer\'s review of this request.\n  """\n'


def prompt_cases() -> list[dict[str, Any]]:
    """--require-prompts vectors (lead decision 6): W's doc is the prompt of its `state W` section when
    there is one, else the head's prompt. The runner checks `mix contract.check --require-prompts` and
    remote_agent.band_format.missing_prompts against the hand-written `missing` list (and each other).
    A missing item's line is the prompt's or else the item's (the declaring head for a W without a
    section); `file_line` adds the 6 header lines."""
    header = HEADER.format(roles="lead, reviewer").replace("reply-deploy", "one-review")
    offset = header.count("\n")
    bare = replace_once(PROMPTED_REVIEW, HEAD_PROMPT, "")
    awaits = PROMPTED_REVIEW.replace("request()\n", "request() awaits reviewing\n")
    section_doc = '"""\nThe reviewer owes a review.\n"""\nstate reviewing max_entries 2 => stalled\n'
    sectioned = replace_once(awaits, "end collected\n", section_doc + "end collected\n")
    undocumented_section = replace_once(sectioned, HEAD_PROMPT, "")
    section_only_head = replace_once(
        sectioned, '"""\nThe reviewer owes a review.\n"""\n', ""
    )
    w_line = "state reviewing max_entries 2 => stalled"

    def missing(name: str, line: int) -> list[dict[str, Any]]:
        return [
            {"item": "state", "name": name, "line": line, "file_line": line + offset}
        ]

    return [
        {
            "id": "v2-reply-prompts-implied-documented",
            "input": header + PROMPTED_REVIEW,
            "missing": [],
            "notes": "The implied request_wait takes the documented head's prompt.",
        },
        {
            "id": "v2-reply-prompts-implied-undocumented",
            "input": header + bare,
            "missing": missing("request_wait", line_of(bare, R_HEAD)),
            "notes": "No head prompt and no section: request_wait is missing, at the head line.",
        },
        {
            "id": "v2-reply-prompts-awaits-no-section",
            "input": header + awaits,
            "missing": [],
            "notes": "awaits W with no state section: the head's prompt documents W.",
        },
        {
            "id": "v2-reply-prompts-section-documented",
            "input": header + undocumented_section,
            "missing": [],
            "notes": "The state W section's prompt documents W; the head (a rule) needs none.",
        },
        {
            "id": "v2-reply-prompts-section-undocumented",
            "input": header + section_only_head,
            "missing": missing("reviewing", line_of(section_only_head, w_line)),
            "notes": "When the section exists its prompt is W's doc, so a documented head does not cover it.",
        },
    ]
