"""Shared data and helpers for specification checks."""

from __future__ import annotations

import re
from collections.abc import Iterator

from tests.paths import CONTRACT_SPEC_ROOT as SPEC

RULE = re.compile(
    r"^\s*(?:event\s+)?([\w{}\[\], ~]+?)\s*->\s*([\w{}\[\], ~]+?):\s*(\w+)\(.*?\)(.*)$"
)


def fences(text: str) -> Iterator[tuple[int, list[str]]]:
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if lines[i] == "```band":
            j = lines.index("```", i + 1)
            yield i + 2, lines[i + 1 : j]
            i = j
        i += 1


def old_pairs(first: int, lines: list[str]) -> list[tuple[int, str, bool]]:
    """Ada's heuristic: a non-event send A->B in some state moves to W, and W has a non-event
    rule B->A. Returns (line, description, recipient is a group or roster)."""
    rosters = set(
        re.findall(r"^([a-z][a-z0-9_]*): list\(", "\n".join(lines), re.MULTILINE)
    )
    state, states = None, {}
    for n, line in enumerate(lines):
        m = re.match(r"^state\s+(\w+)", line)
        if m:
            state = m.group(1)
            states.setdefault(state, [])
            continue
        if re.match(r"^(end|role|type|contract|anystate|room|types)\b", line):
            state = None
            continue
        r = RULE.match(line)
        if r and state is not None and "awaits" not in r.group(4):
            target = re.search(r"=>\s*(\w+)", r.group(4))
            states[state].append(
                (
                    r.group(1).strip(),
                    r.group(2).strip(),
                    r.group(3),
                    target and target.group(1),
                    first + n,
                    line.strip().startswith("event"),
                )
            )
    hits = []
    for rules in states.values():
        for frm, to, kind, target, line, event in rules:
            if event or target not in states:
                continue
            back = [
                x for x in states[target] if x[0] == to and x[1] == frm and not x[5]
            ]
            if back:
                group = bool(re.search(r"[{,~]", to)) or to in rosters
                hits.append(
                    (
                        line,
                        f"{frm} -> {to}: {kind}() => {target}, answered by {back[0][2]}()",
                        group,
                    )
                )
    return hits


def spec_hits(group: bool) -> list[str]:
    return [
        f"{path.name}:{line}: {what}"
        for path in sorted(SPEC.glob("*.md"))
        for first, lines in fences(path.read_text(encoding="utf-8"))
        for line, what, is_group in old_pairs(first, lines)
        if is_group == group
    ]
