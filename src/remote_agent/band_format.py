"""The .band formatter, byte-identical to ContractLab.Format (`mix contract.format`).

Layout only: top-level declarations at column 0, fields and rules at 2, collect options and
the first UBF reply alternative at 4, later `|` alternatives at 5;
one space around `->`, `=>`, `&` and an outcome `|`; `name: value` colons; exactly one blank line
between top-level declarations; at most one blank line; no trailing spaces; a final newline.
YAML frontmatter is kept byte for byte. Comments, author notes and documentation keep their
text and their place; a multiline literal moves only as a whole and stays put when it cannot.
A one-line prompt is written as a block with the same text, directly above its item
(spec/format.md, Prompts). Sigils, patterns, `{a,b}` selectors and `enum(a|b)` lists are never
respaced. In Markdown, the content of each ```band fence is formatted in place and every other
byte is kept (spec/format.md, Markdown).

Usage: python -m remote_agent.band_format [--check] [--require-prompts] [PATH...]

Directories are walked for regular `.band` and `.md` files only, skipping dot entries, `deps`,
`_build`, `node_modules`, symlinks, FIFOs and devices, and the paths a `.band-format-ignore` in
a walked directory or one of its ancestors lists (spec/format.md, Checking). A named file is
always formatted, but must be a regular file of at most 266,240 bytes (4,194,304 for `.md`): a
symlink, FIFO or device is refused (`unsafe_contract_path`) without being read.

`--require-prompts` also refuses a `.band` contract with frontmatter whose contract, roles,
message types or states lack a prompt, listing them as `mix contract.check --require-prompts`
does (`prompts_required`). Body-only references and Markdown are not contracts on their own;
they are listed under `prompts_skipped`.

Unlike `mix contract.format`, this formatter does not compile the contract (or a band fence)
before and after formatting: it has no compiler. Run the Elixir task where that guard matters.
"""

from __future__ import annotations

import argparse
import errno
import fnmatch
import json
import os
import re
import stat
import sys
from collections.abc import Iterable, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Literal, TypeAlias, TypedDict

from remote_agent.capabilities import require_linux
from remote_agent.graphemes import lookup
from remote_agent.output import write_line
from remote_agent.types import JsonObject, SourcePath

FormatItem: TypeAlias = (
    tuple[Literal["blank"]]
    | tuple[Literal["comment"], str]
    | tuple[Literal["doc"], str]
    | tuple[Literal["decl"], str]
    | tuple[Literal["block"], str, list[str]]
)
LeveledItem: TypeAlias = tuple[FormatItem, int]
MarkdownSegment: TypeAlias = (
    tuple[Literal["line"], str] | tuple[Literal["band"], str, list[str], str, str]
)


class FormatUnit(TypedDict):
    items: list[LeveledItem]
    leading: bool


__all__ = [
    "FormatError",
    "format_bytes",
    "format_markdown_bytes",
    "format_source",
    "main",
    "missing_prompts",
]

# Exactly what Elixir's String.trim/1 removes (checked over every scalar value).
_WS = (
    "\t\n\x0b\x0c\x0d \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)
_TOP = {
    "contract",
    "role",
    "type",
    "state",
    "anystate",
    "room",
    "end",
    "watch",
    "require",
}
_OPTIONS = re.compile(r"^(reply|offline|quorum|failure)( |$)", re.ASCII)
_ROSTER = re.compile(r"^[a-z][a-z0-9_]*\s*:\s*list\(", re.ASCII)
_SIGIL = re.compile(rb"~[rsw]([/|{\[(<\"'])")
_CLOSERS = {
    b"/": b"/",
    b"|": b"|",
    b"{": b"}",
    b"[": b"]",
    b"(": b")",
    b"<": b">",
    b'"': b'"',
    b"'": b"'",
}
_SKIP = {"deps", "_build", "node_modules"}
# The largest file source the compiler accepts (ContractLab.FileSource).
_MAX_BYTES = 266_240
_MAX_MARKDOWN_BYTES = 4_194_304
_STR_CLOSERS = {k.decode(): v.decode() for k, v in _CLOSERS.items()}
_FENCE = re.compile(r"( *)(`{3,}|~{3,})(.*)", re.DOTALL)
_IGNORE_FILE = ".band-format-ignore"
_IGNORE_MAX_BYTES = 65_536


class FormatError(Exception):
    """A source the formatter refuses: `source_size`, `crlf` or `encoding`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def format_bytes(data: bytes) -> bytes:
    """Format one source given as bytes; raises FormatError for an over-size source, CRLF or
    invalid UTF-8."""
    if _oversized(data):
        raise FormatError("source_size")
    return _format_any(data)


# Band fences in Markdown are bounded by the Markdown file, not by compile/2, so they format
# through here without the source bound.
def _format_any(data: bytes) -> bytes:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise FormatError("encoding") from None
    if b"\r" in data:
        raise FormatError("crlf")
    header, body = _split_frontmatter(data)
    return header + _body(body.decode("utf-8")).encode("utf-8")


def format_source(text: str) -> str:
    """Format one source given as text."""
    return format_bytes(text.encode("utf-8")).decode("utf-8")


def format_markdown_bytes(data: bytes) -> bytes:
    """Format the ```band fences of a Markdown source given as bytes; every other byte is kept.

    Raises FormatError for CRLF or invalid UTF-8."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FormatError("encoding") from None
    if "\r" in text:
        raise FormatError("crlf")
    lines = []
    for segment in _md_segments(text.split("\n")):
        if segment[0] == "line":
            lines.append(segment[1])
        else:
            _, opening, content, indent, closing = segment
            lines += [opening, *_format_fence(content, indent, closing), closing]
    return "\n".join(lines).encode("utf-8")


# Markdown as ("line", line) and ("band", opening, content_lines, indent, closing) segments.
# Inside a fenced block nothing is parsed; only a fence whose info string is `band` is a band
# segment, and an unclosed fence is kept, line by line, to the end.
def _md_segments(lines: list[str]) -> list[MarkdownSegment]:
    segments, i = [], 0
    while i < len(lines):
        match = _FENCE.fullmatch(lines[i])
        if match is None:
            segments.append(("line", lines[i]))
            i += 1
            continue
        indent, marker, info = match.groups()
        end = i + 1
        while end < len(lines) and not _closing_fence(lines[end], marker):
            end += 1
        if end == len(lines):
            return segments + [("line", line) for line in lines[i:]]
        if _trim(info) == "band":
            segments.append(("band", lines[i], lines[i + 1 : end], indent, lines[end]))
        else:
            segments += [("line", line) for line in lines[i : end + 1]]
        i = end + 1
    return segments


# A closing fence repeats the opening character at least as many times, and nothing else.
def _closing_fence(line: str, marker: str) -> bool:
    trimmed = _trim(line)
    return (
        len(trimmed.encode("utf-8")) >= len(marker) and trimmed.strip(marker[0]) == ""
    )


# Format a fence's content at the fence's indentation. Content that is blank, has no top-level
# declaration, has a line indented less than the fence, or would gain a line closing the fence,
# is kept as written.
def _format_fence(content: list[str], indent: str, closing: str) -> list[str]:
    if not any(_trim(line) != "" for line in content):
        return content
    if not all(_trim(line) == "" or line.startswith(indent) for line in content):
        return content
    text = (
        "\n".join(
            line[len(indent) :] if line.startswith(indent) else line.lstrip(_WS)
            for line in content
        )
        + "\n"
    )
    if not _top_level(text):
        return content
    formatted = _format_any(text.encode("utf-8")).decode("utf-8")
    if formatted == "":
        return content
    lines = formatted.rstrip("\n").split("\n")
    marker = _trim(closing)
    if any(_closing_fence(line, marker) for line in lines):
        return content
    return [indent + line if line else "" for line in lines]


# A fragment of nested lines or header fields has no level-0 declaration to hang them on.
def _top_level(text: str) -> bool:
    _, body = _split_frontmatter(text.encode("utf-8"))
    return any(
        item[0] == "decl" and level == 0
        for item, level in _levels(_scan(body.decode("utf-8").split("\n")))
    )


# compile/2 and FileSource bounds (Sofia U8): a body over 262,144 bytes, or a whole source over
# 266,240 bytes, is refused before any scan of it.
_MAX_BODY_BYTES = 262_144


def _oversized(data: bytes) -> bool:
    return len(data) > _MAX_BYTES or len(_split_frontmatter(data)[1]) > _MAX_BODY_BYTES


def _split_frontmatter(source: bytes) -> tuple[bytes, bytes]:
    if not source.startswith(b"---\n"):
        return b"", source
    rest = source[4:]
    at = rest.find(b"\n---\n")
    if at >= 0:
        return source[: 4 + at + 5], source[4 + at + 5 :]
    if rest.endswith(b"\n---"):
        return source + b"\n", b""
    return b"", source


def _trim(text: str) -> str:
    return text.strip(_WS)


def _body(text: str) -> str:
    items = _drop_blanks_after_docs(_levels(_scan(text.split("\n"))))
    lines = _render(items)
    return "\n".join(lines) + "\n" if lines else ""


# Items: ("blank",), ("comment", text), ("doc", text), ("block", kind, raw_lines), ("decl", text).
def _scan(lines: list[str]) -> list[FormatItem]:
    items, i = [], 0
    while i < len(lines):
        raw = lines[i]
        text = _trim(raw)
        if text == "":
            items.append(("blank",))
            i += 1
        elif text.startswith("%%"):
            items.append(("comment", text))
            i += 1
        elif text.startswith("/*"):
            j = i
            while j < len(lines) and "*/" not in lines[j]:
                j += 1
            end = min(j + 1, len(lines))
            items.append(("block", "note", lines[i:end]))
            i = end
        elif text in ('"""', '~s"""'):
            j = i + 1
            while j < len(lines) and _trim(lines[j]) != '"""':
                j += 1
            end = min(j + 1, len(lines))
            items.append(("block", "doc", lines[i:end]))
            i = end
        elif text.startswith(('"', "~s")):
            items.append(("doc", text))
            i += 1
        else:
            items.append(("decl", text))
            i += 1
    return items


# Levels are indentation widths, not multiples of two: UBF pipes use five spaces.
# Collect options follow a collect; UBF alternatives are recognized by their leading
# marker alone. Comments, notes, docs and blanks take the following declaration's width.
def _levels(items: list[FormatItem]) -> list[LeveledItem]:
    decided: list[LeveledItem] = []
    previous = None
    for item in items:
        if item[0] == "decl":
            text = item[1]
            if _top(text):
                level = 0
            elif text.startswith("=>"):
                level = 4
            elif text.startswith("|"):
                level = 5
            elif (
                previous in ("collect", "option")
                and _OPTIONS.match(text)
                and " -> " not in text
            ):
                level = 4
            else:
                level = 2
            if level == 4 and not text.startswith("=>"):
                previous = "option"
            elif text.startswith("collect "):
                previous = "collect"
            else:
                previous = "other"
            decided.append((item, level))
        else:
            previous = "other"
            decided.append((item, 0))
    following = 0
    for index in range(len(decided) - 1, -1, -1):
        item, level = decided[index]
        if item[0] == "decl":
            following = level
        else:
            decided[index] = (item, following)
    return decided


def _top(text: str) -> bool:
    parts = text.split(" ", 1)
    if parts[0] in _TOP:
        return len(parts) == 1 or not parts[1].startswith("-> ")
    return bool(_ROSTER.match(text))


# A prompt sits directly above the item it documents: no blank line after a doc literal.
def _drop_blanks_after_docs(items: list[LeveledItem]) -> list[LeveledItem]:
    kept, after_doc = [], False
    for entry in items:
        item = entry[0]
        if item[0] == "blank" and after_doc:
            continue
        kept.append(entry)
        after_doc = item[0] == "doc" or item[:2] == ("block", "doc")
    return kept


# Group items into top-level units: leading comments, notes and docs, the declaration and its
# nested lines (and the blank lines after them).
def _units(items: list[LeveledItem]) -> list[FormatUnit]:
    start = 0
    while start < len(items) and items[start][0][0] == "blank":
        start += 1
    units = []
    for entry in items[start:]:
        item, level = entry
        if item[0] == "blank" and units:
            units[-1]["items"].append(entry)
        elif item[0] == "decl" and level == 0:
            if units and units[-1]["leading"]:
                units[-1]["items"].append(entry)
                units[-1]["leading"] = False
            else:
                units.append({"items": [entry], "leading": False})
        elif level == 0:
            if units and units[-1]["leading"]:
                units[-1]["items"].append(entry)
            else:
                units.append({"items": [entry], "leading": True})
        elif units:
            units[-1]["items"].append(entry)
            units[-1]["leading"] = False
        else:
            units.append({"items": [entry], "leading": False})
    return units


# Exactly one blank line between top-level units (Eric, 10-15: no same-keyword grouping).
def _render(items: list[LeveledItem]) -> list[str]:
    lines = []
    for unit in _units(items):
        body = _unit_lines(unit["items"])
        while body and body[-1] == "":
            body.pop()
        lines += ([""] if lines else []) + body
    return lines


# Render a unit's items, collapsing blank runs to one.
def _unit_lines(items: list[LeveledItem]) -> list[str]:
    out = []
    for item, level in items:
        for line in _item_lines(item, level):
            if line == "" and out and out[-1] == "":
                continue
            out.append(line)
    return out


def _item_lines(item: FormatItem, level: int) -> list[str]:
    if item[0] == "blank":
        return [""]
    if item[0] == "comment":
        return [" " * level + item[1]]
    if item[0] == "doc":
        # A one-line prompt is written as a block, its content as written (spec/format.md).
        block = _block_form(item[1])
        lines = [item[1]] if block is None else [block[0], block[1], '"""']
        return [" " * level + line for line in lines]
    if item[0] == "decl":
        return [" " * level + _respace(item[1])]
    return _block_lines(item[1], item[2], level)


def _block_lines(kind: str, lines: list[str], level: int) -> list[str]:
    delta = level - _leading(lines[0])
    shiftable = all(
        "\t" not in _leading_ws(line)
        and (line == "" or delta >= 0 or _leading(line) >= -delta)
        for line in lines
    )
    if not shiftable:
        return list(lines)
    if kind == "note":
        return [_shift(line, delta).rstrip(_WS) for line in lines]
    # Documentation content is kept exactly; only the delimiter lines lose trailing blanks.
    last = len(lines) - 1
    return [
        _shift(line, delta).rstrip(_WS) if i in (0, last) else _shift(line, delta)
        for i, line in enumerate(lines)
    ]


def _shift(line: str, delta: int) -> str:
    if line == "":
        return ""
    return " " * delta + line if delta >= 0 else line[-delta:]


def _leading(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(_WS))]


# --require-prompts without a compiler: the contract, every role, every message type and every
# state need a prompt that is not blank (ContractLab.Prompts, Eric 10-15). A prompt documents the
# next declaration, as the compiler attaches it: under `type offer`, before a field, it documents
# the field. A line is a body line: the prompt's when there is one, else the item's, and the
# compiler's default 1 for a contract or role that is never declared; `file_line` adds the
# frontmatter's lines. None for a body without frontmatter, which is not a contract on its own.
def missing_prompts(data: bytes) -> list[JsonObject] | None:
    header, body = _split_frontmatter(data)
    if not header:
        return None
    name, roles = _card(header.decode("utf-8"))
    lines = body.decode("utf-8").split("\n")
    found = _prompted_items(lines)
    items = [("contract", name, found["contract"].get("", (None, 1)))]
    items += [("role", role, found["role"].get(role, (None, 1))) for role in roles]
    items += [
        (kind, key, meta)
        for kind in ("type", "state")
        for key, meta in found[kind].items()
    ]
    offset = header.count(b"\n")
    # Lines are bounded source indices, not arbitrary sort keys. Bucket once,
    # then emit in source order. Contract/header-role defaults retain their
    # order at line 1; distinct body declarations have distinct metadata lines.
    by_line = {}
    for kind, key, (doc, line) in items:
        if doc is None or _trim(doc) == "":
            by_line.setdefault(line, []).append(
                {"item": kind, "name": key, "line": line, "file_line": line + offset}
            )
    return [
        entry for line in range(1, len(lines) + 1) for entry in by_line.get(line, ())
    ]


# The header's `name` and `roles` (a flow list or a block list).
def _card(header: str) -> tuple[str | None, list[str]]:
    name, roles, in_roles = None, [], False
    for line in header.split("\n")[1:]:
        if in_roles and re.match(r"[ \t]+- ", line):
            roles.append(_unquote(line.strip()[2:]))
            continue
        in_roles = False
        key, colon, value = line.partition(":")
        value = value.strip()
        if not colon or key != key.strip():
            continue
        if key == "name":
            name = _unquote(value)
        elif key == "roles" and value.startswith("[") and value.endswith("]"):
            roles = [_unquote(role) for role in value[1:-1].split(",") if role.strip()]
        elif key == "roles" and value == "":
            in_roles = True
    return name, roles


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


_RAW_CLOSE = re.compile(r'[ \t]*"""[ \t]*')
_STATE_NAME = re.compile(r"state ([a-z][a-z0-9_]*)")
_ROSTER_LINE = re.compile(r"([a-z][a-z0-9_]*): list\([a-z][a-z0-9_]*\)")
_WAIT_HEAD = re.compile(
    r"[a-z][a-z0-9_]*[ \t]*->[ \t]*[a-z][a-z0-9_]*[ \t]*:[ \t]*"
    r"(?P<kind>[a-z][a-z0-9_]*)\(.*\)(?:[ \t]+as[ \t]+[a-z][a-z0-9_]*)?"
    r"(?:[ \t]+awaits[ \t]+(?P<wait>[a-z][a-z0-9_]*))?",
    re.ASCII,
)
_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "d": "\x7f",
    "e": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "s": " ",
    "t": "\t",
    "v": "\v",
    "0": "\0",
    "\n": "",
}
_ESCAPED = re.compile(
    r"\\(?:u([0-9a-fA-F]{4})|u\{([0-9a-fA-F]{1,6})\}|x([0-9a-fA-F]{2})|([\s\S]))"
)


# {kind: {name: (doc or None, line)}}, the contract under "": each declaration takes the prompt
# pending above it, with the prompt's line, or none and its own line; any other declaration
# (a field, a rule, a collect, `end`) takes the pending prompt too (ContractLab.Compiler).
# A U head followed by alternatives creates W; its state section's metadata wins, even
# when blank/undocumented, otherwise W takes the head's metadata (lead decision 6).
def _prompted_items(lines: list[str]) -> dict[str, dict[str, tuple[str | None, int]]]:
    found: dict[str, dict[str, tuple[str | None, int]]] = {
        "contract": {},
        "role": {},
        "type": {},
        "state": {},
    }
    declarations = []
    pending, i = None, 0
    while i < len(lines):
        raw, line = lines[i], i + 1
        text = _trim(raw)
        i += 1
        if text == "" or text.startswith("%%"):
            continue
        if text.startswith("/*"):
            rest = text[2:]
            while "*/" not in rest and i < len(lines):
                rest, i = lines[i], i + 1
            continue
        if text in ('~s"""', '~S"""', '"""'):
            start = i
            closed = (
                _RAW_CLOSE.fullmatch
                if text != '"""'
                else (lambda raw: _trim(raw) == '"""')
            )
            while i < len(lines) and not closed(lines[i]):
                i += 1
            doc = "\n".join(lines[start:i])
            pending, i = (doc if text != '"""' else _unescape(doc), line), i + 1
            continue
        if text.startswith(("~s", "~S")):
            pending = (text[3:-1], line)
            continue
        if text.startswith('"'):
            pending = (_unescape(text[1:-1]), line)
            continue
        meta, pending = pending or (None, line), None
        declarations.append((text, meta))
        keyword, _, rest = text.partition(" ")
        roster = _ROSTER_LINE.fullmatch(text)
        state = _STATE_NAME.match(text)
        if text == "contract" or text.startswith("contract @"):
            found["contract"].setdefault("", meta)
        elif keyword == "role" and rest:
            found["role"][rest.split(": ", 1)[0]] = meta
        elif roster:
            found["role"][roster.group(1)] = meta
        elif keyword == "type" and rest and ": " not in rest:
            found["type"][rest] = meta
        elif state:
            found["state"][state.group(1)] = meta
    for (head, meta), (following, _) in pairwise(declarations):
        if following.startswith("=>"):
            match = _WAIT_HEAD.fullmatch(head)
            if match:
                wait = match["wait"] or match["kind"] + "_wait"
                found["state"].setdefault(wait, meta)
    return found


# Elixir string escapes, enough to tell a blank prompt ("\s", "\n") from a written one.
def _unescape(text: str) -> str:
    # Split literal backslash pairs first so native replacements cannot turn an
    # escaped backslash into a new escape. Spans and separators are copied once.
    if "\\\\" in text:
        return "\\".join(_unescape_single(part) for part in text.split("\\\\"))
    return _unescape_single(text)


def _unescape_single(text: str) -> str:
    if "\\" not in text:
        return text
    if "\\u" not in text and "\\x" not in text:
        # There are no escaped backslashes in this span. Each original slash
        # either introduces a simple escape or is the final, literal slash.
        # A fixed number of native passes avoids a callback per tiny escape.
        tail = "\\" if text.endswith("\\") else ""
        if tail:
            text = text[:-1]
        for escaped, decoded in _ESCAPES.items():
            text = text.replace("\\" + escaped, decoded)
        return text.replace("\\", "") + tail

    # Bounded hex alternatives, not a copy of the remaining suffix per escape.
    def replace(match: re.Match[str]) -> str:
        escaped = match[4]
        if escaped is not None:
            return _ESCAPES.get(escaped, escaped)
        return chr(min(int(match[1] or match[2] or match[3], 16), 0x10FFFF))

    return _ESCAPED.sub(replace, text)


# (opener, content) for a one-line literal that can be a block; None keeps it as written (text
# after the closing delimiter, empty content, or content that would close the block).
def _block_form(text: str) -> tuple[str, str] | None:
    if text.startswith('"'):
        return _as_block(_plain_content(text[1:]), '"""')
    if text.startswith("~s") and len(text) > 2 and text[2] in _STR_CLOSERS:
        content, found, after = text[3:].partition(_STR_CLOSERS[text[2]])
        if found and after == "":
            # A raw prompt reads the same as a plain block unless a backslash or #{ would change it.
            opener = '~s"""' if "\\" in content or "#{" in content else '"""'
            return _as_block(content, opener)
    return None


# The content of a plain one-line literal up to its unescaped closing quote, if nothing follows.
def _plain_content(rest: str) -> str | None:
    i = 0
    while i < len(rest):
        if rest[i] == "\\" and i + 1 < len(rest):
            i += 2
        elif rest[i] == '"':
            return rest[:i] if i == len(rest) - 1 else None
        else:
            i += 1
    return None


def _as_block(content: str | None, opener: str) -> tuple[str, str] | None:
    if not content or _trim(content) == '"""':
        return None
    return opener, content


# Respace a declaration outside sigils: one space between words, around -> and =>, after a
# colon, and around an outcome | or & (outside parentheses, where enum(a|b) lives).
def _respace(text: str) -> str:
    if "~" not in text:
        return _trim(_plain(text, 0)[0])
    out, depth = [], 0
    for kind, segment in _segments(text.encode("utf-8")):
        if kind == "sigil":
            out.append(segment)
        else:
            plain, depth = _plain(segment, depth)
            out.append(plain)
    return _trim("".join(out))


_HSPACE = re.compile(r"[ \t]+")
_SEND_ARROW = re.compile(r" ?-> ?")
_OUTCOME_ARROW = re.compile(r" ?=> ?")
_COLON = re.compile(r" ?: ?")
_SYNTAX = re.compile(r"[()|&]")
_PADDED_OUTCOME = re.compile(r" +([|&]) +")


class SyntaxSpacing:
    def __init__(self, text: str, depth: int) -> None:
        self.text = text
        self.depth = depth
        self.ascii_only = text.isascii()

    def replace(self, match: re.Match[str]) -> str:
        character, index = match[0], match.start()
        # These ASCII syntax characters count only when they form a whole grapheme.
        if not self.ascii_only:
            if (
                index
                and ord(self.text[index - 1]) > 127
                and lookup("gcb", ord(self.text[index - 1]), "Other") == "Prepend"
            ):
                return character
            if (
                index + 1 < len(self.text)
                and ord(self.text[index + 1]) > 127
                and lookup("gcb", ord(self.text[index + 1]), "Other")
                in ("Extend", "ZWJ", "SpacingMark")
            ):
                return character
        match character:
            case "(":
                self.depth += 1
            case ")":
                self.depth = max(self.depth - 1, 0)
            case _ if self.depth == 0:
                return " " + character + " "
        return character


def _plain(s: str, depth: int) -> tuple[str, int]:
    s = _HSPACE.sub(" ", s)
    s = _SEND_ARROW.sub(" -> ", s)
    s = _OUTCOME_ARROW.sub(" => ", s)
    s = _COLON.sub(": ", s)
    if _SYNTAX.search(s) is None:
        return s, depth
    ascii_only = s.isascii()
    if ascii_only and "(" not in s and ")" not in s:
        if depth == 0:
            s = s.replace("|", " | ").replace("&", " & ")
        return _PADDED_OUTCOME.sub(lambda match: " " + match[1] + " ", s), depth

    syntax = SyntaxSpacing(s, depth)
    spaced = _SYNTAX.sub(syntax.replace, s)
    return _PADDED_OUTCOME.sub(lambda match: " " + match[1] + " ", spaced), syntax.depth


# Split a declaration into plain text and sigils (~r/~s/~w with any supported delimiter).
def _segments(text: bytes) -> list[tuple[str, str]]:
    segments, cursor = [], 0
    while cursor < len(text):
        match = _SIGIL.search(text, cursor)
        if match is None:
            segments.append(("plain", text[cursor:].decode("utf-8")))
            break
        start, open_at = match.start(), match.start(1)
        end = _closing(text, _CLOSERS[match[1]], open_at + 1)
        if start > cursor:
            segments.append(("plain", text[cursor:start].decode("utf-8")))
        segments.append(("sigil", text[start:end].decode("utf-8")))
        cursor = end
    return segments


# Index after the closing delimiter (backslash escapes skip one byte). All
# callers retain the same buffer: copying the remaining suffix per sigil is O(n²).
def _closing(text: bytes, close: bytes, start: int = 0) -> int:
    i, closing = start, close[0]
    while i < len(text):
        if text[i] == 92 and i + 1 < len(text):
            i += 2
        elif text[i] == closing:
            return i + 1
        else:
            i += 1
    return i


# Directories are walked for regular .band and .md files only (lstat: symlinks are never followed,
# FIFOs and devices never opened), skipping dot entries, deps, _build, node_modules and what a
# .band-format-ignore in a walked directory or one of its ancestors lists. Named files are
# always taken.
def _expand(path: str) -> list[str]:
    try:
        is_dir = stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        is_dir = False
    if not is_dir:
        return [path]
    inherited = _ignored_in(_ancestors(os.path.abspath(path)))
    return sorted(_walk(path, inherited), key=os.fsencode)


def _ancestors(directory: str) -> list[str]:
    found = []
    parent = os.path.dirname(directory)
    while parent != directory:
        found.append(parent)
        directory, parent = parent, os.path.dirname(parent)
    return found


def _walk(directory: str, ignored: set[str]) -> list[str]:
    ignored = ignored | _ignored_in([os.path.abspath(directory)])
    found = []
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if name.startswith(".") or os.path.abspath(path) in ignored:
            continue
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            continue
        if stat.S_ISDIR(mode) and name not in _SKIP:
            found += _walk(path, ignored)
        elif stat.S_ISREG(mode) and os.path.splitext(name)[1] in (".band", ".md"):
            found.append(path)
    return found


# The absolute paths an ignore file lists: one pattern per line, relative to its directory;
# blank lines and `#` comments skipped. Only a regular ignore file counts.
def _ignored_in(directories: Iterable[str]) -> set[str]:
    found = set()
    for directory in directories:
        try:
            data = _read(os.path.join(directory, _IGNORE_FILE), _IGNORE_MAX_BYTES)
        except ReadError:
            continue
        for line in data.decode("utf-8", "surrogateescape").split("\n"):
            pattern = _trim(line)
            if pattern and not pattern.startswith("#"):
                found.update(
                    os.path.abspath(m)
                    for m in _wildcard(os.path.join(directory, pattern))
                )
    return found


# Existing paths matching a pattern where `*` and `?` match within one path segment and never
# a leading dot (spec/format.md, Checking); every other character is literal.
def _wildcard(pattern: str) -> list[str]:
    paths = ["/"] if pattern.startswith("/") else [""]
    for part in (p for p in pattern.split("/") if p):
        if "*" not in part and "?" not in part:
            paths = [os.path.join(base, part) for base in paths]
            continue
        # Python 3.12's translator atomically commits interior star/fixed
        # chunks; repeated stars cannot backtrack combinatorially. Brackets
        # are literal in this format, unlike ordinary shell patterns.
        matcher = re.compile(fnmatch.translate(part.replace("[", "[[]")))
        matched = []
        for base in paths:
            try:
                names = sorted(os.listdir(base or "."))
            except OSError:
                continue
            matched += [
                os.path.join(base, name)
                for name in names
                if (part.startswith(".") or not name.startswith("."))
                and matcher.fullmatch(name)
            ]
        paths = matched
    return [path for path in paths if os.path.lexists(path)]


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_size,
        stat.S_IFMT(info.st_mode),
        int(info.st_mtime),
        int(info.st_ctime),
        info.st_mode,
        info.st_nlink,
        info.st_dev,
        info.st_rdev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
    )


class ReadError(Exception):
    def __init__(self, error: JsonObject) -> None:
        super().__init__(error)
        self.error = error


def _io(error: OSError) -> ReadError:
    return ReadError({"code": "file_io", "detail": [_errno_code(error)]})


# HeaderSpikes.FileSnapshot.read: a regular file only, at most _MAX_BYTES, unchanged between
# the check, the open and the read.
def _read(path: SourcePath, limit: int = _MAX_BYTES) -> bytes:
    require_linux()
    try:
        before = os.lstat(path)
    except OSError as error:
        raise _io(error) from None
    if not stat.S_ISREG(before.st_mode):
        raise ReadError({"code": "unsafe_contract_path"})
    if before.st_size > limit:
        raise ReadError({"code": "source_size"})
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ReadError({"code": "source_changed"}) from None
        raise _io(error) from None
    try:

        def unchanged() -> None:
            if not (
                _identity(before)
                == _identity(os.fstat(fd))
                == _identity(os.lstat(path))
            ):
                raise ReadError({"code": "source_changed"})

        unchanged()
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, limit + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > limit:
            raise ReadError({"code": "source_size"})
        unchanged()
        return b"".join(chunks)
    except OSError as error:
        raise _io(error) from None
    finally:
        os.close(fd)


def _fail(error: JsonObject) -> int:
    write_line(
        json.dumps({"error": error}, separators=(",", ":"), ensure_ascii=False),
        file=sys.stderr,
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m remote_agent.band_format",
        description="Format .band contracts in place.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 1 when a file would change",
    )
    parser.add_argument(
        "--require-prompts",
        action="store_true",
        help="exit 1 when a contract, role, message type or state has no prompt",
    )
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    files = list(dict.fromkeys(f for p in (args.paths or ["."]) for f in _expand(p)))
    results, skipped = [], []
    for path in files:
        markdown = os.path.splitext(path)[1] == ".md"
        try:
            source = _read(path, _MAX_MARKDOWN_BYTES if markdown else _MAX_BYTES)
        except ReadError as error:
            return _fail({**error.error, "path": path})
        try:
            text = format_markdown_bytes(source) if markdown else format_bytes(source)
        except FormatError as error:
            return _fail({"code": error.code, "path": path})
        if args.require_prompts:
            missing = None if markdown else missing_prompts(source)
            if missing:
                return _fail(
                    {"code": "prompts_required", "path": path, "missing": missing}
                )
            if missing is None:
                skipped.append(path)
        results.append((path, source, text))
    changed = [(path, text) for path, source, text in results if text != source]
    if args.check:
        if changed:
            return _fail(
                {"code": "unformatted", "files": [path for path, _ in changed]}
            )
        report: JsonObject = {"checked": len(results), "unformatted": []}
        if args.require_prompts:
            report.update(prompts="complete", prompts_skipped=skipped)
        write_line(json.dumps(report, separators=(",", ":"), ensure_ascii=False))
        return 0
    for path, text in changed:
        Path(path).write_bytes(text)
    write_line(
        json.dumps(
            {
                "formatted": [path for path, _ in changed],
                "unchanged": len(results) - len(changed),
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    return 0


def _errno_code(error: OSError) -> str:
    if error.errno is None:
        return "error"
    return errno.errorcode.get(error.errno, "error").lower()


if __name__ == "__main__":
    raise SystemExit(main())
