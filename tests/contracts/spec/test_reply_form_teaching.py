"""Eric 10-15 (op-kz2a3som): a request that waits for one answer is written with reply
alternatives. Theo's test_reply_form_examples.py covers the ```band fences of five spec pages;
this one covers the other pages and the files that teach agents to write contracts (the four
skills, the root docs and the contract-lab README), in every fence language. It applies
RULE.md §1 (ubf-replies/old-form): an untimed single-recipient send A->B into W, answered by a
state-moving rule B->A in W, or a timed send whose answer in W is timed. Group requests
(`{a,b}`, `~w[...]` or a roster) are reported separately until the group design (§3)."""

from __future__ import annotations

import re
import textwrap
import unittest
from collections.abc import Iterator
from pathlib import Path

from tests.paths import CONTRACTS_ROOT as ROOT

THEO = {
    "multi-agent-skills.md",
    "walkthrough.md",
    "band-integration.md",
    "contract-dsl.md",
    "timeouts-v2.md",
}
TEACHING = [
    "skills/multi-agent-contracts/SKILL.md",
    "skills/multi-agent-contracts/references/authoring.md",
    "skills/create-multi-agent-contract/SKILL.md",
    "skills/iterate-multi-agent-contract/SKILL.md",
    "contract-lab/README.md",
]
AUTHORING = "skills/multi-agent-contracts/references/authoring.md"
RULE = re.compile(
    r"^(event\s+)?([\w{}\[\], ~]+?)\s*->\s*([\w{}\[\], ~]+?):\s*(\w+)\((.*?)\)(.*)$"
)


def files() -> list[Path]:
    spec = [p for p in (ROOT / "spec").glob("*.md") if p.name not in THEO]
    spec += list((ROOT / "spec" / "design").glob("*.md"))
    root = [
        ROOT / n
        for n in (
            "README.md",
            "IMPLEMENTATION.md",
            "IMPLEMENTATION-EXPLAINER.md",
            "PRODUCTION.md",
            "multi-agent-skills-contract-and-engine-v3.md",
            "contract-lab/README.md",
        )
    ]
    return sorted(spec + root + list((ROOT / "skills").rglob("*.md")))


def fences(text: str) -> Iterator[tuple[int, list[str]]]:
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        s = lines[i].lstrip()
        if s.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("```"):
                j += 1
            yield i + 2, textwrap.dedent("\n".join(lines[i + 1 : j])).split("\n")
            i = j
        i += 1


def roles(s: str) -> list[str]:
    return [x.strip() for x in s.strip("{} ").split(",")]


def old_pairs(first: int, lines: list[str]) -> list[tuple[int, str, bool]]:
    rosters = set(
        re.findall(r"^([a-z][a-z0-9_]*): list\(", "\n".join(lines), re.MULTILINE)
    )
    state, rules, ends = None, [], set()
    for n, raw in enumerate(lines):
        line = raw.strip()
        m = re.match(r"^state\s+(\w+)", line)
        if m:
            state = m.group(1)
            continue
        m = re.match(r"^end\s+(\w+)", line)
        if m:
            ends.add(m.group(1))
            state = None
            continue
        if re.match(r"^(anystate|role|type|contract|room)\b", line):
            state = None
            continue
        r = RULE.match(line)
        if r and state and "awaits" not in r.group(6):
            ev, frm, to, kind, _, rest = r.groups()
            target = re.search(r"=>\s*(\w+)", rest)
            timed = bool(
                re.match(r"\s*(each\s+)?(as \w+\s*)?(mentions? \w+\s*)?@\d", rest)
            )
            rules.append(
                {
                    "state": state,
                    "event": bool(ev),
                    "frm": frm.strip(),
                    "to": to.strip(),
                    "kind": kind,
                    "target": target and target.group(1),
                    "timed": timed,
                    "line": first + n,
                }
            )
    states = {r["state"] for r in rules}
    hits = []
    for p in rules:
        if p["event"] or p["target"] in ends or p["target"] not in states:
            continue
        back = [
            r
            for r in rules
            if r["state"] == p["target"]
            and not r["event"]
            and r["target"]
            and set(roles(r["frm"])) & set(roles(p["to"]))
            and set(roles(r["to"])) & set(roles(p["frm"]))
        ]
        if back and (not p["timed"] or any(r["timed"] for r in back)):
            group = len(roles(p["to"])) != 1 or "~" in p["to"] or p["to"] in rosters
            hits.append(
                (
                    p["line"],
                    (
                        f"{p['frm']} -> {p['to']}: {p['kind']}() => {p['target']}, "
                        f"answered by {back[0]['kind']}()"
                    ),
                    group,
                )
            )
    return hits


def hits(group: bool) -> list[str]:
    return [
        f"{path.relative_to(ROOT)}:{line}: {what}"
        for path in files()
        for first, lines in fences(path.read_text(encoding="utf-8"))
        for line, what, is_group in old_pairs(first, lines)
        if is_group == group
    ]


class TestReplyFormTeaching(unittest.TestCase):
    def test_no_single_recipient_send_then_answer_pair_in_any_fence(self) -> None:
        self.assertEqual(
            hits(group=False), [], "write the request with reply alternatives"
        )

    def test_group_requests_wait_for_the_group_design(self) -> None:
        # authoring.md ({worker,reviewer}), defaults-v2 (roster) and lowercase-v2 ({a,b}):
        # RULE.md §3, after Eric approves `from`. Group cases are marked, never narrowed.
        self.assertEqual(
            sorted({hit.split(":")[0] for hit in hits(group=True)}),
            [AUTHORING, "spec/defaults-v2.md", "spec/lowercase-v2.md"],
        )

    def test_the_group_offer_is_marked_and_the_group_design_left_open(self) -> None:
        text = (ROOT / AUTHORING).read_text()
        self.assertIn("lead -> {worker,reviewer}: offer() as task => offered", text)
        self.assertIn("no reply form yet", text)
        for path in files():
            self.assertNotRegex(
                path.read_text(),
                r"fan-out to a group stays",
                f"{path.relative_to(ROOT)} decides the group design (RULE.md §3)",
            )

    def test_the_teaching_files_teach_the_reply_form_and_its_error(self) -> None:
        for name in TEACHING:
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertIn("awaits", text)
                self.assertIn("reply_required", text)

    def test_no_teaching_file_prefers_a_bare_timed_answer(self) -> None:
        bare = re.compile(
            r"[Pp]refer `reviewer -> lead: review\(\) @5m|[Ff]or a timed reply, prefer"
        )
        self.assertEqual(
            [
                n
                for n in TEACHING
                if bare.search((ROOT / n).read_text(encoding="utf-8"))
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main()
