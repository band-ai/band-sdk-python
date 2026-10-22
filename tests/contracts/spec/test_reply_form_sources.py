"""Eric 10-15 (op-p4o2fa6d): a request that waits for an answer is written with reply
alternatives. This applies the `reply_required` rule (ubf-replies/old-form/RULE.md section 1)
to the pinned conformance sources, the formatter vectors and the test fixtures.

A request is a non-event send from one role to one role, in a `state` or `anystate` section,
into a nonterminal state W. It is an error when W's own section holds a non-event rule from the
recipient back to the sender that moves the state, unless the request is timed and every such
answer is untimed (an owed report). Group requests (`{a,b}`, `~w[...]`, a roster or `each`)
are held for RULE.md section 3 and listed apart."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.paths import CONTRACT_RUNTIME_TESTS, REPO_ROOT
from tests.paths import CONTRACTS_ROOT as ROOT

SOURCES = sorted((ROOT / "spec/conformance/sources-v2").glob("*.band"))
VECTORS = sorted((ROOT / "spec/conformance/format").glob("*/*.band"))
FIXTURES = [
    ROOT / "contract-lab/test/fixtures/room-work-v030.band",
    CONTRACT_RUNTIME_TESTS / "fixtures/adversarial-plan.band",
    CONTRACT_RUNTIME_TESTS / "fixtures/h7-preclaim.band",
]
RULE = re.compile(
    r"^\s*(event\s+)?([\w{}\[\], ~]+?)\s*->\s*([\w{}\[\], ~]+?):\s*(\w+)\((.*?)\)(.*)$"
)
TIMED = re.compile(r"@\d+[smhd]\s*=>")

# Group requests stay in their ca1147d form in the no-group landing (op-uyux3xf3). Their
# conversions (RULE.md section 3, `awaits W from R|any|each`) wait on rafael/old-form-pending
# and Maya's branch.
GROUP_FILES = {
    f"contracts/spec/conformance/sources-v2/{name}.band"
    for name in (
        "assignment",
        "first-responder",
        "history-review",
        "history-review-v2",
        "team-review",
    )
} | {
    f"contracts/spec/conformance/format/02-spacing/{name}.band"
    for name in ("input", "expected")
}


def roles(text: str) -> set[str]:
    return {
        r.strip()
        for r in re.sub(r"^~w\[|\]$", "", text.strip("{} ")).replace(",", " ").split()
    }


def flags(path: Path) -> list[tuple[int, bool, str]]:
    """(line, group, description) for every request that breaks the rule."""
    lines = path.read_text(encoding="utf-8").split("\n")
    rosters = set(
        re.findall(r"^\s*([a-z][a-z0-9_]*): list\(", "\n".join(lines), re.MULTILINE)
    )
    section, rules, terminals = None, [], set()
    for n, line in enumerate(lines, 1):
        m = re.match(r"^state\s+(\w+)", line)
        if m:
            section = m.group(1)
            continue
        m = re.match(r"^end\s+(\w+)", line)
        if m:
            terminals.add(m.group(1))
            section = None
            continue
        if re.match(r"^anystate\b", line):
            section = "*anystate*"
            continue
        if re.match(r"^\S", line):
            section = None
            continue
        r = RULE.match(line)
        if not (r and section):
            continue
        event, sender, recipient, kind, _args, rest = r.groups()
        if "awaits" in rest:
            continue
        target = re.search(r"=>\s*(@?\w+)", rest)
        rules.append(
            {
                "section": section,
                "event": bool(event),
                "sender": sender.strip(),
                "recipient": recipient.strip(),
                "kind": kind,
                "line": n,
                "target": target and target.group(1),
                "timed": bool(TIMED.search(rest)),
                "each": rest.lstrip().startswith("each"),
            }
        )
    sections = {r["section"] for r in rules}
    found = []
    for p in rules:
        if (
            p["event"]
            or not p["target"]
            or p["target"] in terminals
            or p["target"] not in sections
        ):
            continue
        a, b = roles(p["sender"]), roles(p["recipient"])
        answers = [
            r
            for r in rules
            if r["section"] == p["target"]
            and not r["event"]
            and r["target"]
            and roles(r["sender"]) & b
            and roles(r["recipient"]) & a
        ]
        if not answers or (p["timed"] and not any(r["timed"] for r in answers)):
            continue
        group = len(a) != 1 or len(b) != 1 or bool(b & rosters) or p["each"]
        found.append(
            (
                p["line"],
                group,
                (
                    f"{p['sender']} -> {p['recipient']}: {p['kind']}() => "
                    f"{p['target']}, answered by {answers[0]['kind']}() at {answers[0]['line']}"
                ),
            )
        )
    return found


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def report(paths: list[Path], group: bool) -> list[str]:
    return [
        f"{rel(path)}:{line}: {what}"
        for path in paths
        for line, is_group, what in flags(path)
        if is_group == group
    ]


class TestReplyFormSources(unittest.TestCase):
    def test_no_single_recipient_send_then_answer_pair_is_left(self) -> None:
        self.assertEqual(
            report(SOURCES + VECTORS + FIXTURES, group=False),
            [],
            "write the request with reply alternatives",
        )

    def test_only_the_held_group_files_keep_group_pairs(self) -> None:
        self.assertEqual(
            sorted(
                {
                    h.split(":")[0]
                    for h in report(SOURCES + VECTORS + FIXTURES, group=True)
                }
            ),
            sorted(GROUP_FILES),
        )

    def test_assignment_states_its_temporary_cancel_narrowing(self) -> None:
        # op-yqwswp23: the single-recipient cancel stop leaves the reviewer uninformed until
        # group replies land; both copies say so in the cancel's prompt.
        for path in (
            ROOT / "spec/conformance/sources-v2/assignment.band",
            ROOT / "contract-lab/contracts/assignment.band",
        ):
            text = " ".join(path.read_text(encoding="utf-8").split())
            self.assertIn(
                "Until group replies (RULE.md §3) land, the reviewer is not told of a cancel "
                "and gets no cancel_ack.",
                text,
                rel(path),
            )


if __name__ == "__main__":
    unittest.main()
