"""Shared data and helpers for specification checks."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator
from pathlib import Path

from tests.paths import CONTRACTS_ROOT as ROOT

TOOLS = ROOT / "spec/tools"

RECORD = json.loads((TOOLS / "band-keywords.json").read_text(encoding="utf-8"))

PROBE = (TOOLS / RECORD["probe"]).read_text(encoding="utf-8")

FENCE = re.compile(r"^(?P<indent> *)(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)$")

CONTRACT_LINE = re.compile(
    r"^\s*(contract @|state [a-z]|collect [a-z]|anystate$|end [a-z]|engine: "
    r"|[a-z{*~][^\n]* -> [^\n]*: [a-z_]+\()",
    re.MULTILINE,
)


def fences() -> Iterator[tuple[Path, int, str, str]]:
    """Yield (path, line, info, source) for every fenced block in the site's markdown."""
    for path in sorted((ROOT / "spec").rglob("*.md")):
        lines = path.read_text(encoding="utf-8").split("\n")
        i = 0
        while i < len(lines):
            m = FENCE.match(lines[i])
            if not m:
                i += 1
                continue
            indent, fence = len(m["indent"]), m["fence"]
            close = re.compile(r"^ *" + re.escape(fence[0]) + f"{{{len(fence)},}} *$")
            j = i + 1
            while j < len(lines) and not close.match(lines[j]):
                j += 1
            body = [
                line[indent:] if line[:indent].strip() == "" else line.lstrip()
                for line in lines[i + 1 : j]
            ]
            yield (
                path.relative_to(ROOT),
                i + 1,
                m["info"].strip().split(" ")[0],
                "\n".join(body) + "\n",
            )
            i = j + 1


def band_blocks() -> list[tuple[Path, int, str]]:
    return [(p, n, src) for p, n, info, src in fences() if info == "band"]


def text_of(rendered: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", rendered))


def keyword_words(rendered: str) -> set[str]:
    return set(re.findall(r'<span class="tok-keyword">([^<]*)</span>', rendered))


def prompt_problems(where: str, src: str) -> list[str]:
    """op-promptdocs (Eric, 10-15): prompts are triple-quote blocks on the contract, every role,
    message type and state; an excerpt declares each role and message type it uses."""
    declaration = re.compile(
        r"^(contract\b|role [a-z]|type [a-z]|state [a-z]|[a-z][a-z0-9_]*: list\()"
    )
    lines = src.split("\n")
    body = lines[lines.index("---", 1) + 1 :] if lines[0] == "---" else lines
    if not any(declaration.match(ln) or " -> " in ln for ln in body):
        return []  # a header-only excerpt has nothing to prompt
    problems, code, inside = [], [], False
    for k, ln in enumerate(
        body
    ):  # prompt text is not code: a prompt line may begin with "state"
        if ln.strip() == '"""':
            inside = not inside
            continue
        if inside:
            continue
        code.append(ln)
        if declaration.match(ln) and (k == 0 or body[k - 1].strip() != '"""'):
            problems.append(f'{where}: no """ prompt above {ln.split(" =>")[0]!r}')
        if re.match(r'^\s*("|~s)', ln):
            problems.append(f"{where}: one-line or sigil prompt {ln.strip()[:40]!r}")
    if not any(ln.startswith("contract") for ln in code):
        problems.insert(0, f"{where}: no contract line")
    text = "\n".join(code)
    declared_roles = set(
        re.findall(r"^role ([a-z][a-z0-9_]*)", text, re.MULTILINE)
    ) | set(re.findall(r"^([a-z][a-z0-9_]*): list\(", text, re.MULTILINE))
    used_roles = set()
    selector = r"(\{[a-z0-9_,]+\}|~w\[[a-z ]+\]|[a-z][a-z0-9_]*)"
    senders = re.findall(r"^\s*(?:event )?" + selector + r" -> ", text, re.MULTILINE)
    receivers = re.findall(r" -> " + selector + r": ", text, re.MULTILINE)
    for sel in senders + receivers:
        used_roles |= set(re.findall(r"[a-z][a-z0-9_]*", sel.removeprefix("~w")))
    for pair in re.findall(
        r"^\s*collect [a-z_]+ from ([a-z_]+) -> ([a-z_]+)", text, re.MULTILINE
    ):
        used_roles |= set(pair)
    used_roles |= set(
        re.findall(
            r"^\s*on ([a-z][a-z0-9_]*) (?:online|offline|removed)", text, re.MULTILINE
        )
    )
    used_roles |= set(re.findall(r"^require ([a-z][a-z0-9_]*)", text, re.MULTILINE))
    for role in sorted(used_roles - declared_roles):
        problems.append(f"{where}: role {role} has no prompted declaration")
    declared_types = set(re.findall(r"^type ([a-z][a-z0-9_]*)", text, re.MULTILINE))
    used_types = set(
        re.findall(r"(?:: |^\s*reply )([a-z][a-z0-9_]*)\(", text, re.MULTILINE)
    ) - {"text", "ref", "enum", "integer", "list"}
    for kind in sorted(used_types - declared_types):
        problems.append(f"{where}: message type {kind} has no prompted declaration")
    return problems
