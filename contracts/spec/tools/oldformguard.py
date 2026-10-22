"""Repo-wide guard for the old send-then-answer form (ubf-replies/old-form/RULE.md section 1).

Finds every contract snippet in every tracked file and reports each ordinary send whose
target state holds an answer from the recipient (code `reply_required`). Snippets are:

- a whole `.band` file;
- every Markdown fence (any info string) and every indented code block, in any text file;
- every Python string constant (`ast`, so concatenated and escaped strings count);
- every Elixir heredoc and every escaped one-line Elixir string;
- every JSON string value.

Saved run logs and evidence are records of past runs and are skipped (COVERAGE.md), and so
are the rule's own negative examples (NEGATIVE).
Band-v1 compatibility fixtures and their authoring regression use the grammar of the
separately pinned engine; the current SDK migration rule excludes those inputs.
The rule is the Python port of RULE.md section 1 until Kenji's compiler code lands.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from contracts.spec.tools.paths import REPO_ROOT as ROOT

INVENTORY = Path(__file__).resolve().parent / "old_form_inventory.json"

RECORDS = (
    "contracts/agent-runtime/evidence/",
    "contracts/investigations/pr-4/",
    "contracts/engines/band-v1/",
)
HISTORICAL_SOURCES = "contracts/spec/conformance/reply-stability-sources.json"
NEGATIVE_PREFIXES = ("tests/contracts/spec/oldformguard/",)
RECORD_SUFFIXES = (".log", ".png", ".lock")
# Snippets that are the rule's own negative examples: this guard's tests and the compiler's.
NEGATIVE = ("contracts/contract-lab/test/reply_required_test.exs",)
BAND_V1_FIXTURES = "tests/contracts/runtime/fixtures/band/"
BAND_V1_AUTHORING_TEST = "tests/contracts/runtime/test_band_quality_authoring.py"

# Owners from ubf-replies/old-form/COVERAGE.md, first match wins.
OWNERS = (
    ("contracts/spec/conformance/sources-v2/", "Rafael"),
    ("contracts/spec/conformance/format/", "Rafael"),
    ("contracts/spec/conformance/", "Rafael (generated JSON)"),
    ("contracts/contract-lab/test/fixtures/", "Rafael"),
    ("tests/contracts/runtime/fixtures/", "Rafael"),
    ("contracts/spec/multi-agent-skills.md", "Theo"),
    ("contracts/spec/walkthrough.md", "Theo"),
    ("contracts/spec/band-integration.md", "Theo"),
    ("contracts/spec/contract-dsl.md", "Theo"),
    ("contracts/spec/timeouts-v2.md", "Theo"),
    ("contracts/spec/tools/", "Priya"),
    ("contracts/spec/", "Ivy"),
    ("contracts/skills/", "Ivy"),
    ("contracts/contract-lab/README.md", "Ivy"),
    ("contracts/contract-lab/contracts/", "Maya"),
    ("contracts/header-spikes/", "Maya"),
    ("contracts/examples/kg-track/", "Maya"),
    ("contracts/contract-lab/test/", "Priya"),
    ("contracts/contract-lab/lib/", "Priya"),
    ("tests/contracts/runtime/", "Priya"),
)
ROOT_DOCS = re.compile(
    r"^(?:contracts/)?(README|IMPLEMENTATION[\w-]*|PRODUCTION|multi-agent-skills-contract-and-engine-v3)\.md$"
)

IDENT = r"[A-Za-z_][\w]*"
ROLES = r"(?:\*|" + IDENT + r"|\{[^}]*\})"
RULE = re.compile(
    r"^(\s*)(event\s+)?("
    + ROLES
    + r")\s*->\s*("
    + ROLES
    + r")\s*:\s*("
    + IDENT
    + r")\s*\("
)
HEADER = re.compile(r"^(\s*)(?:(state|end)\s+(" + IDENT + r")\b|(anystate)\b)")
ROSTER = re.compile(r"^\s*(" + IDENT + r")\s*:\s*list\(")
FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})")


def owner(path: str) -> str:
    if ROOT_DOCS.match(path):
        return "Ivy"
    for prefix, name in OWNERS:
        if path.startswith(prefix):
            return name
    return "unassigned"


def tracked_files(root: str | Path = ROOT) -> list[str]:
    out = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "contracts",
            "src/remote_agent",
            "tests/contracts",
        ],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    return [p for p in out.decode().split("\0") if p]


def is_record(path: str) -> bool:
    return (
        path.startswith(RECORDS + NEGATIVE_PREFIXES + (BAND_V1_FIXTURES,))
        or path == HISTORICAL_SOURCES
        or path.endswith(RECORD_SUFFIXES)
        or path in NEGATIVE
        or path == BAND_V1_AUTHORING_TEST
    )


# --- snippet extraction: (first file line, lines) -------------------------------------------


def markdown_blocks(text: str) -> Iterator[tuple[int, list[str]]]:
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if m:
            indent, marker = len(m.group(1)), m.group(2)
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(
                marker[0] * len(marker)
            ):
                j += 1
            yield (
                i + 2,
                [
                    line[indent:] if line[:indent].isspace() else line
                    for line in lines[i + 1 : j]
                ],
            )
            i = j + 1
            continue
        if lines[i].startswith("    ") and (i == 0 or not lines[i - 1].strip()):
            j = i
            while j < len(lines) and (
                lines[j].startswith("    ") or not lines[j].strip()
            ):
                j += 1
            yield i + 1, [line[4:] for line in lines[i:j]]
            i = j
            continue
        i += 1


# An opener ends its line; closers are consumed with their opener, so a bare quote line opens.
HEREDOC_OPEN = re.compile(r'(?:~[a-zA-Z])?("""|\'\'\')\s*$')
ELIXIR_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*\\n(?:[^"\\\n]|\\.)*)"')


def elixir_strings(text: str) -> Iterator[tuple[int, list[str], bool]]:
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = HEREDOC_OPEN.search(lines[i])
        if m:
            quote = m.group(1)
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith(quote):
                j += 1
            if j < len(lines):
                indent = len(lines[j]) - len(lines[j].lstrip())
                yield i + 2, [line[indent:] for line in lines[i + 1 : j]], False
                i = j + 1
                continue
        i += 1
    for n, line in enumerate(lines, 1):
        for m in ELIXIR_STRING.finditer(line):
            body = re.sub(
                r"\\(.)",
                lambda e: {"n": "\n", "t": "\t"}.get(e.group(1), e.group(1)),
                m.group(1),
            )
            yield n, body.split("\n"), True


def python_strings(text: str) -> Iterator[tuple[int, list[str], bool]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            body = "".join(
                v.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
                else "{}"
                for v in node.values
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            body = node.value
        else:
            continue
        if "->" in body and "\n" in body:
            segment = ast.get_source_segment(text, node) or ""
            literal = segment.count("\n") == body.count("\n")
            yield node.lineno, body.split("\n"), not literal


JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def json_strings(text: str) -> Iterator[tuple[int, list[str], bool]]:
    for m in JSON_STRING.finditer(text):
        token = m.group(0)
        if "\\n" not in token or "->" not in token:
            continue
        try:
            body = json.loads(token)
        except ValueError:
            continue
        yield text.count("\n", 0, m.start()) + 1, body.split("\n"), True


def units(
    first: int, lines: list[str], escaped: bool = False
) -> Iterator[tuple[int, list[str], bool]]:
    """A body that holds fences is split into its fences; otherwise it is one snippet."""
    if any(FENCE.match(line) for line in lines):
        for start, block in markdown_blocks("\n".join(lines)):
            yield (first if escaped else first + start - 1), block, escaped
    else:
        yield first, lines, escaped


def snippets(path: str, text: str) -> Iterator[tuple[int, list[str], bool]]:
    """Yield (first line, lines, escaped); an escaped body reports its string's own line."""
    if path.endswith(".md"):
        for first, lines in markdown_blocks(text):
            yield first, lines, False
        return
    if path.endswith(".py"):
        found = python_strings(text)
    elif path.endswith((".ex", ".exs")):
        found = elixir_strings(text)
    elif path.endswith(".json"):
        found = json_strings(text)
    else:
        found = [(1, text.split("\n"), False)]
    for first, lines, escaped in found:
        yield from units(first, lines, escaped)


# --- RULE.md section 1 ----------------------------------------------------------------------


def roles(selector: str) -> list[str]:
    return [r.strip() for r in selector.strip("{} ").split(",") if r.strip()]


def balanced(text: str) -> bool:
    return text.count("(") <= text.count(")")


def parse(lines: list[str]) -> dict[str, Any] | None:
    """Rules, declared states, terminals and wait states of one snippet."""
    headers = [HEADER.match(line) for line in lines]
    indents = [len(m.group(1)) for m in headers if m]
    if not indents:
        return None
    base = min(indents)
    rules, states, ends, waits, rosters = [], set(), set(), set(), set()
    section, doc, note, i = None, False, False, 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        i += 1
        if note:
            note = "*/" not in line
            continue
        if stripped.startswith("/*"):
            note = "*/" not in stripped
            continue
        if re.fullmatch(r'(~[a-zA-Z])?"""', stripped):
            doc = not doc
            continue
        if doc or not stripped:
            continue
        m = ROSTER.match(line)
        if m:
            rosters.add(m.group(1))
        m = HEADER.match(line)
        if m and indent == base:
            kind, name = m.group(2) or m.group(4), m.group(3)
            if kind == "state" and name:
                section = name
                states.add(name)
            elif kind == "anystate":
                section = "*any*"
            else:
                ends.add(name)
                section = None
            continue
        if indent <= base:
            section = None
            continue
        m = RULE.match(line)
        if not m or section is None:
            continue
        text, start = line, i
        while not balanced(text) and i < len(lines):
            text += " " + lines[i].strip()
            i += 1
        while (
            i < len(lines)
            and lines[i].strip()[:1] in ("=", "|", "@")
            and len(lines[i]) - len(lines[i].lstrip()) > indent
        ):
            text += " " + lines[i].strip()
            i += 1
        depth, cut = 0, None
        for k in range(m.end() - 1, len(text)):
            depth += {"(": 1, ")": -1}.get(text[k], 0)
            if depth == 0:
                cut = k + 1
                break
        rest = text[cut:] if cut else ""
        head = re.search(r"\bawaits\s+(" + IDENT + r")", rest)
        if head:
            waits.add(head.group(1))
        before = re.sub(r"\bas\s+" + IDENT, "", rest.split("=>", 1)[0])
        target = re.search(r"=>\s*(@self|" + IDENT + r")", rest)
        rules.append(
            {
                "line": start,
                "section": section,
                "event": bool(m.group(2)),
                "frm": m.group(3),
                "to": m.group(4),
                "kind": m.group(5),
                "head": bool(head),
                "each": bool(re.search(r"\beach\b", before)),
                "timed": bool(re.search(r"@\S", before)) and not head,
                "target": target.group(1) if target else None,
            }
        )
    return {
        "rules": rules,
        "states": states,
        "ends": ends,
        "waits": waits,
        "rosters": rosters,
    }


def check(
    lines: list[str],
) -> Iterator[tuple[int, str, dict[str, Any], list[dict[str, Any]]]]:
    """Yield (line, verdict, rule, answers) for each request; verdict is error, group or exempt."""
    parsed = parse(lines)
    if not parsed:
        return
    states, ends, waits, rosters = (
        parsed["states"],
        parsed["ends"],
        parsed["waits"],
        parsed["rosters"],
    )
    rules = parsed["rules"]
    for p in rules:
        if p["event"] or p["head"] or not p["target"]:
            continue
        target = p["section"] if p["target"] == "@self" else p["target"]
        if target not in states or target in ends or target in waits:
            continue
        senders, recipients = roles(p["frm"]), roles(p["to"])
        answers = [
            r
            for r in rules
            if r["section"] == target
            and not r["event"]
            and r["target"]
            and r["target"] != "@self"
            and (set(roles(r["frm"])) & set(recipients) or r["frm"] == "*")
            and (set(roles(r["to"])) & set(senders) or r["to"] == "*")
        ]
        if not answers:
            continue
        group = (
            len(senders) != 1
            or len(recipients) != 1
            or p["each"]
            or "*" in recipients
            or recipients[0] in rosters
        )
        if not p["timed"] or any(r["timed"] for r in answers):
            verdict = "group" if group else "error"
        else:
            verdict = "exempt"
        yield p["line"], verdict, p, answers


def scan(
    root: Path | str = ROOT, files: list[str] | None = None
) -> list[dict[str, Any]]:
    """Every request found in tracked files, as dicts sorted by path and line."""
    hits = []
    for path in files if files is not None else tracked_files(root):
        if is_record(path):
            continue
        try:
            text = (Path(root) / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        if "->" not in text:
            continue
        seen = set()
        for first, lines, escaped in snippets(path, text):
            for offset, verdict, p, answers in check(lines):
                line = first if escaped else first + offset - 1
                key = (line, offset if escaped else 0, p["kind"], verdict)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    {
                        "path": path,
                        "line": line,
                        "verdict": verdict,
                        "owner": owner(path),
                        "request": f"{p['frm']} -> {p['to']}: {p['kind']}",
                        "state": p["section"],
                        "answers": sorted({r["kind"] for r in answers}),
                    }
                )
    return sorted(hits, key=lambda h: (h["path"], h["line"], h["request"]))


def inventory_key(hit: dict[str, Any]) -> tuple[str, str, str, str]:
    return (hit["path"], hit["state"], hit["request"], hit["verdict"])


def load_inventory(path: Path = INVENTORY) -> dict[str, Any]:
    return json.loads(path.read_text())


def shrink_inventory(
    path: Path = INVENTORY, hits: list[dict[str, Any]] | None = None
) -> int:
    """Drop the rows that no longer hit; never add one. Returns the number dropped."""
    data = load_inventory(path)
    live = {}
    for hit in hits if hits is not None else scan():
        live[inventory_key(hit)] = live.get(inventory_key(hit), 0) + 1
    kept = []
    for row in data["rows"]:
        if live.get(inventory_key(row), 0) > 0:
            live[inventory_key(row)] -= 1
            kept.append(row)
    dropped = len(data["rows"]) - len(kept)
    data["rows"] = kept
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return dropped


def report(hits: list[dict[str, Any]]) -> None:
    for h in hits:
        sys.stdout.write(
            str(
                f"{h['path']}:{h['line']} | {h['verdict']} | {h['owner']} | {h['state']} | "
                f"{h['request']} | {','.join(h['answers'])}"
            )
            + "\n"
        )


if __name__ == "__main__":
    if sys.argv[1:] == ["--shrink"]:
        sys.stdout.write(
            " ".join(
                str(value)
                for value in (
                    "dropped",
                    shrink_inventory(),
                    "rows",
                )
            )
            + "\n"
        )
        sys.exit(0)
    rows = scan()
    report(rows)
    counts = {}
    for h in rows:
        counts[h["verdict"]] = counts.get(h["verdict"], 0) + 1
    sys.stderr.write(
        " ".join(
            str(value)
            for value in (
                "TOTAL",
                counts,
            )
        )
        + "\n"
    )
