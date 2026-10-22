"""Static syntax highlighting for ```band code blocks on the spec site.

A port of the deck's highlightContract (vendored verbatim in band_highlight_deck.js): the same
token classes (tok-keyword, tok-type, tok-field, tok-duration, tok-number, tok-reference,
tok-string, tok-comment, tok-operator) and colors, rendered at site build as static spans, with no
runtime JavaScript. highlight(source, deck=True) reproduces the deck byte for byte; the default
current mode follows today's compiler: lowercase keywords only, total, quorum and awaits added, the
mention caps added, and the removed forms (due ... with ... in, collect failure, activity) left plain.

KEYWORDS must equal band-keywords.json, which contract-lab/test/band_keywords_test.exs holds to the
compiler. The site build calls install(md, SRC) after the Mermaid hook; it checks every ```band
block under SRC before anything is written and fails the build on an unhighlighted block or on
keyword drift.

Check a tree by hand:  python3 -m contracts.spec.tools.bandhighlight --check spec
Check a built site:    python3 -m contracts.spec.tools.bandhighlight --check-build BUILD_DIR SRC_DIR
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RECORD = "band-keywords.json"

# JavaScript's \s, so a regex here matches what the deck's regex matches.
_WS = "[\t\n\v\f\r    -     　﻿]"

# Deck tables (verbatim from band_highlight_deck.js).
DECK_DECLARATIONS = frozenset(
    [
        "contract",
        "role",
        "type",
        "state",
        "end",
        "anystate",
        "room",
        "watch",
        "require",
        "event",
        "due",
        "on",
        "collect",
        "reply",
        "failure",
        "offline",
    ]
)
DECK_OPTIONS = frozenset(("as", "each", "activity"))
DECK_DUE_WORDS = frozenset(("all", "any", "of", "with", "in"))
ON_WORDS = frozenset(("online", "offline", "removed", "for"))

# Current tables: due and collect failure were removed, quorum is a collect line (DF-25), total is a
# state budget (DF-21), mention caps are rule and contract options, activity was removed, and awaits
# names a send's wait state before its reply alternatives (syntax U, op-wcvgonpv).
DECLARATIONS = frozenset(DECK_DECLARATIONS - {"due", "failure"} | {"quorum"})
OPTIONS = frozenset(("as", "each", "mentions", "mention", "awaits"))
CONTRACT_OPTIONS = frozenset(("max", "mentions", "mention"))
STATE_WORDS = frozenset(("max_entries", "total"))
KEYWORDS = frozenset(
    DECLARATIONS | OPTIONS | CONTRACT_OPTIONS | STATE_WORDS | ON_WORDS | {"from", "all"}
)

CSS = (
    ".tok-keyword{color:#c4b5fd;font-weight:600}.tok-type,.tok-field{color:#93c5fd}"
    ".tok-duration,.tok-number{color:#fdba74}.tok-reference{color:#67e8f9}.tok-string{color:#b7dfb5}"
    ".tok-comment{color:#9daec2;font-style:italic}.tok-operator{color:#d4dce6}"
    "pre.band-hl,pre.band-hl code{background:#0d1721;color:#e8edf2}"
)

_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
_TRIPLE = re.compile(r'~[Ss]"""')
_SIGIL = re.compile(r"~[SRWsrw]")
_SCOPE = re.compile(r"@(self|state|contract)\b", re.ASCII)
_BINDING = re.compile(r"\$[a-z][a-z0-9_]*")
_NUMBER = re.compile(r"@?[0-9]+(?:[smhd]\b)?", re.ASCII)
_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_-]*")
_DECK_OPERATOR = re.compile(r"(?:->|=>|\.\.|[|=:])")
_OPERATOR = re.compile(
    r"(?:->|=>|\.\.|[|=:&])"
)  # & joins a reply to its next state (syntax U)
_CLOSE = {"{": "}", "[": "]", "(": ")", "<": ">"}


class BandHighlightError(ValueError):
    pass


def esc(value: str) -> str:
    return "".join(_ESC.get(c, c) for c in value)


def _starts(pattern: str, text: str) -> bool:
    return re.match(pattern, text) is not None


def _kind(value: str, before: str, after: str, line: str, deck: bool) -> str | None:
    """The token class of one identifier, from its line context (the deck's rules)."""
    word = value.lower() if deck else value
    if deck:
        if (
            word in DECK_DECLARATIONS
            and _starts(_WS + "*$", before)
            and not after.startswith(" -> ")
        ):
            return "keyword"
        if (
            word in DECK_OPTIONS
            and "->" in before
            and ")" in before
            and "=>" not in before[before.rfind(")") :]
        ):
            return "keyword"
        if (
            word == "max_entries"
            and _starts(_WS + r"*(?:state|\+STATE) ", line)
            and _starts(r" [0-9]+ => ", after)
        ):
            return "keyword"
        if word in DECK_DUE_WORDS and _starts(_WS + r"*(?:due|DUE) (?!->)", line):
            return "keyword"
        if word in ON_WORDS and _starts(_WS + r"*(?:on|ON) (?!->)", line):
            return "keyword"
    else:
        if (
            word in DECLARATIONS
            and _starts(_WS + "*$", before)
            and not after.startswith(" -> ")
        ):
            return "keyword"
        if (
            word in OPTIONS
            and "->" in before
            and ")" in before
            and "=>" not in before[before.rfind(")") :]
        ):
            return "keyword"
        if (
            word in STATE_WORDS
            and _starts(_WS + r"*state ", line)
            and (
                _starts(r" [0-9]+ => ", after)
                if word == "max_entries"
                else after.startswith(" @")
            )
        ):
            return "keyword"
        if (
            word == "all"
            and _starts(_WS + r"*quorum ", line)
            and after.startswith(" =>")
        ):
            return "keyword"
        if word in ON_WORDS and _starts(_WS + r"*on (?!->)", line):
            return "keyword"
        if (
            word in CONTRACT_OPTIONS - {"max"}
            and _starts(_WS + r"*contract ", line)
            and "=>" not in before
        ):
            return "keyword"
    if word == "from" and _starts(_WS + r"*collect ", line):
        return "keyword"
    if word == "max" and _starts(_WS + r"*contract ", line):
        return "keyword"
    if word == "infinity" and _starts(_WS + r"*contract ", line):
        return "duration"
    if word in ("text", "ref", "enum", "integer", "list") and after.startswith("("):
        return "type"
    if word == "boolean" and re.search(":" + _WS + "*$", before):
        return "type"
    if _starts(_WS + "*:", after) and "->" not in line:
        return "field"
    return None


def highlight(source: Any, deck: bool = False) -> str:
    """Escaped HTML with token spans. Removing the tags and unescaping gives back the source."""
    text, out = str(source), []

    def emit(value: str, kind: str | None = None) -> None:
        out.append(
            f'<span class="tok-{kind}">{esc(value)}</span>' if kind else esc(value)
        )

    i, n = 0, len(text)
    while i < n:
        start = i
        if text.startswith("%%", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            emit(text[start:i], "comment")
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            emit(text[start:i], "comment")
            continue
        triple = 5 if _TRIPLE.match(text, i) else 3 if text.startswith('"""', i) else 0
        if triple:
            end = text.find('"""', i + triple)
            i = n if end < 0 else end + 3
            emit(text[start:i], "string")
            continue
        if _SIGIL.match(text, i) and i + 2 < n and text[i + 2] in "/|{[(<\"'":
            close = _CLOSE.get(text[i + 2], text[i + 2])
            i += 3
            while i < n and text[i] != close:
                i += 2 if text[i] == "\\" and i + 1 < n else 1
            if i < n:
                i += 1
            emit(text[start:i], "string")
            continue
        if text[i] in "\"'":
            quote = text[i]
            i += 1
            while i < n and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
            if i < n:
                i += 1
            i = min(i, n)
            emit(text[start:i], "string")
            continue
        m = _SCOPE.match(text, i) or _BINDING.match(text, i)
        if m:
            i = m.end()
            emit(m.group(0), "reference")
            continue
        m = _NUMBER.match(text, i)
        if m:
            i = m.end()
            emit(
                m.group(0), "duration" if re.search("[@smhd]", m.group(0)) else "number"
            )
            continue
        m = _WORD.match(text, i)
        if m:
            value = m.group(0)
            line_start = text.rfind("\n", 0, i) + 1
            line_end = text.find("\n", i)
            line = text[line_start : n if line_end < 0 else line_end]
            i = m.end()
            emit(value, _kind(value, text[line_start:start], text[i:], line, deck))
            continue
        m = (_DECK_OPERATOR if deck else _OPERATOR).match(text, i)
        if m:
            i = m.end()
            emit(m.group(0), "operator")
            continue
        emit(text[i])
        i += 1
    return "".join(out)


def plain_text(rendered: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", rendered))


def render_block(source: Any) -> str:
    """One ```band block as a deck code block; raises unless it is highlighted and copy-exact."""
    body = highlight(source)
    if '<span class="tok-' not in body:
        raise BandHighlightError("no token is highlighted; is this band source?")
    if plain_text(body) != source:
        raise BandHighlightError("the highlighted text differs from the source")
    return f'<pre class="band-hl"><code class="language-band">{body}</code></pre>'


def keyword_drift(record_path: Path) -> str:
    """'' when KEYWORDS equals the recorded compiler keywords, else what differs."""
    try:
        recorded = set(
            json.loads(Path(record_path).read_text(encoding="utf-8"))["keywords"]
        )
    except (OSError, ValueError, KeyError) as error:
        return f"keyword drift: cannot read {Path(record_path).name}: {error}"
    if recorded == KEYWORDS:
        return ""
    return (
        f"keyword drift: the compiler accepts {sorted(recorded - KEYWORDS)} that the highlighter lacks; "
        f"the highlighter has {sorted(KEYWORDS - recorded)} that the compiler does not accept"
    )


_FENCE = re.compile(r"^(?P<indent> *)(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)$")


def _band_fences(text: str, md: Any = None) -> list[tuple[int, str]]:
    """(line, source) for each ```band fence: markdown-it's tokens when md is given, else a scan."""
    if md is not None:
        return [
            (t.map[0] + 1, t.content)
            for t in md.parse(text)
            if t.type == "fence" and t.info.strip().split(" ")[0] == "band"
        ]
    found, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        m = _FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        indent, fence = len(m["indent"]), m["fence"]
        close = re.compile(r"^ *" + re.escape(fence[0]) + f"{{{len(fence)},}} *$")
        j = i + 1
        while j < len(lines) and not close.match(lines[j]):
            j += 1
        if m["info"].strip().split(" ")[0] == "band":
            body = [
                ln[indent:] if ln[:indent].strip() == "" else ln.lstrip()
                for ln in lines[i + 1 : j]
            ]
            found.append((i + 1, "\n".join(body) + "\n"))
        i = j + 1
    return found


def check_tree(src: str | Path, md: Any = None) -> list[str]:
    """Every error that must fail the site build for the spec tree at src ([] when clean)."""
    src = Path(src)
    errors = []
    drift = keyword_drift(src / "tools" / RECORD)
    if drift:
        errors.append(drift)
    for path in sorted(src.rglob("*.md")):
        for line, source in _band_fences(path.read_text(encoding="utf-8"), md):
            try:
                render_block(source)
            except BandHighlightError as error:
                errors.append(f"{path.relative_to(src).as_posix()}:{line}: {error}")
    return errors


def install(md: Any, src: str | Path) -> Any:
    """The site-build hook: check every ```band block under src first, then render them."""
    errors = check_tree(src, md)
    if errors:
        sys.stderr.write(
            str("bandhighlight: the build stops:\n  " + "\n  ".join(errors)) + "\n"
        )
        raise SystemExit(1)
    blocks = sum(
        len(_band_fences(p.read_text(encoding="utf-8"), md))
        for p in Path(src).rglob("*.md")
    )
    sys.stderr.write(
        str(
            f"bandhighlight: {blocks} band blocks, {len(KEYWORDS)} keywords match the compiler record"
        )
        + "\n"
    )
    default = md.renderer.rules["fence"]

    def fence(self, tokens: list[Any], idx: int, options: Any, env: Any) -> str:
        token = tokens[idx]
        if token.info.strip().split(" ")[0] != "band":
            return default(tokens, idx, options, env)
        style = "" if env.get("band_style") else f"<style>{CSS}</style>\n"
        env["band_style"] = True
        return (
            style + render_block(token.content) + "\n"
        )  # markdown-it ends a fence with a newline

    md.add_render_rule("fence", fence)
    return md


def check_build(build: str | Path, src: str | Path) -> list[str]:
    """Each page under build has as many highlighted blocks as its source has ```band fences."""
    errors = []
    for path in sorted(Path(src).rglob("*.md")):
        rel = path.relative_to(src).as_posix()
        page = Path(build) / (
            "index.html" if rel == "README.md" else rel[:-3] + ".html"
        )
        want = len(_band_fences(path.read_text(encoding="utf-8")))
        got = (
            page.read_text(encoding="utf-8").count(
                '<pre class="band-hl"><code class="language-band">'
            )
            if page.exists()
            else 0
        )
        if want != got:
            errors.append(
                f"{rel}: {want} band blocks in the source, {got} highlighted in {page.name}"
            )
    return errors


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--check":
        problems = check_tree(args[1])
    elif len(args) == 3 and args[0] == "--check-build":
        problems = check_build(args[1], args[2]) + check_tree(args[2])
    else:
        sys.exit(__doc__)
    sys.stdout.write(
        str("\n".join(problems) if problems else "bandhighlight: clean") + "\n"
    )
    sys.exit(1 if problems else 0)
