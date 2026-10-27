# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Check the docs review's edits to the Fern tree before anything is pushed.

Compares the edited tree with a pristine copy. A path violation (a created,
deleted, or renamed file, a symlink, a file outside the docs pages, a
non-UTF-8 file, or too many pages) fails the command: nothing may be filed. A
content violation (a new import, a new JSX tag, a link outside the allowed
sites, too many changed lines) only marks the docs PR do-not-merge. Writes the
changed pages and content violations to ``--out`` and appends ``changed`` and
``mergeable`` to ``--outputs``. Logs counts and violation kinds only: the
pages are private.
"""

from __future__ import annotations

import argparse
import difflib
import logging
import re
from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

from releasedata import MAX_REVIEWED_PAGES, Frozen, is_docs_page
from stepoutputs import append_outputs
from textsafety import CODE_PATTERN, URL_PATTERN

logger = logging.getLogger(__name__)

MAX_CHANGED_LINES = 200
ALLOWED_LINK_PREFIXES = (
    "https://docs.band.ai/",
    "https://band.ai/",
    "https://app.band.ai/",
    "https://github.com/band-ai/",
    "https://pypi.org/project/band-sdk/",
)
IMPORT_LINE = re.compile(r"^\s*(?:import|export)\b")
# A whole MDX/JSX tag with its attributes, or an MDX `{expression}`;
# `<https://...>` autolinks are not tags.
MDX_CONSTRUCT = re.compile(r"<(?![\w+.-]+:)/?[A-Za-z][^>]*>|\{[^{}]*\}?")
# A Markdown link or reference-definition target.
LINK_TARGET = re.compile(r"\]\(\s*<?([^)\s>]+)|^\s*\[[^\]]+\]:\s*<?(\S+?)>?\s*$")
SCHEME = re.compile(r"^[A-Za-z][\w+.-]*:")


class PathViolation(StrEnum):
    CREATED = "creates a file"
    DELETED = "deletes a file"
    SYMLINK = "changes a symlink"
    OUTSIDE = "changes a file outside the docs pages"
    NOT_UTF8 = "changes a non-UTF-8 file"
    TOO_MANY = f"changes more than {MAX_REVIEWED_PAGES} pages"


class ContentViolation(StrEnum):
    IMPORT = "adds an import or export"
    JSX = "adds or changes a JSX tag or MDX expression"
    LINK = "links outside the allowed sites"
    TOO_LONG = f"changes more than {MAX_CHANGED_LINES} lines"


class Finding(Frozen):
    path: str
    kind: str

    def __str__(self) -> str:
        return f"`{self.path}`: {self.kind}"


class PatchCheck(Frozen):
    """The checked edits: the pages to file and why the PR must not merge."""

    files: list[str]
    content_violations: list[Finding]

    @property
    def mergeable(self) -> bool:
        return not self.content_violations


class PatchError(ValueError):
    """The edits break a path rule, so nothing may be filed."""

    def __init__(self, findings: list[Finding]) -> None:
        super().__init__(", ".join(sorted({finding.kind for finding in findings})))
        self.findings = findings


def tree_files(root: Path) -> dict[str, Path]:
    """Every file and symlink under ``root``, by ``/``-separated relative path."""
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_symlink() or path.is_file()
    }


def differs(before: Path, after: Path) -> bool:
    if before.is_symlink() or after.is_symlink():
        return not (before.is_symlink() and after.is_symlink()) or (
            before.readlink() != after.readlink()
        )
    return before.read_bytes() != after.read_bytes()


def is_utf8(path: Path) -> bool:
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def modified_problems(path: str, before: Path, after: Path) -> list[Finding]:
    if before.is_symlink() or after.is_symlink():
        return [Finding(path=path, kind=PathViolation.SYMLINK)]
    problems = (
        [] if is_docs_page(path) else [Finding(path=path, kind=PathViolation.OUTSIDE)]
    )
    if not (is_utf8(before) and is_utf8(after)):
        problems.append(Finding(path=path, kind=PathViolation.NOT_UTF8))
    return problems


def changed_pages(pristine: Path, edited: Path) -> list[str]:
    """The modified pages; raise ``PatchError`` on any path violation."""
    before, after = tree_files(pristine), tree_files(edited)
    findings = [
        Finding(path=p, kind=PathViolation.CREATED)
        for p in after.keys() - before.keys()
    ]
    findings += [
        Finding(path=p, kind=PathViolation.DELETED)
        for p in before.keys() - after.keys()
    ]
    modified = sorted(
        path
        for path in before.keys() & after.keys()
        if differs(before[path], after[path])
    )
    findings += [
        finding
        for path in modified
        for finding in modified_problems(path, before[path], after[path])
    ]
    if len(modified) > MAX_REVIEWED_PAGES:
        findings.append(Finding(path=".", kind=PathViolation.TOO_MANY))
    if findings:
        raise PatchError(findings)
    return modified


def diff_lines(before: str, after: str) -> tuple[list[str], int]:
    """The added lines, and the count of added plus removed lines."""
    diff = difflib.unified_diff(
        before.splitlines(), after.splitlines(), lineterm="", n=0
    )
    changed = [
        line for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]
    return [line[1:] for line in changed if line.startswith("+")], len(changed)


def mdx_constructs(text: str) -> Counter[str]:
    """Tags and expressions outside code; import lines have their own rule."""
    prose = CODE_PATTERN.sub("", text).splitlines()
    kept = "\n".join(line for line in prose if not IMPORT_LINE.match(line))
    return Counter(MDX_CONSTRUCT.findall(kept))


def is_allowed_url(url: str) -> bool:
    """An absolute URL on an allowed site, with no step out of its path."""
    path = url.lower().replace("%2e", ".")
    return (
        url.startswith(ALLOWED_LINK_PREFIXES)
        and "/../" not in path
        and not path.endswith("/..")
    )


def is_allowed_target(target: str) -> bool:
    """A link target: a relative path or anchor, or an allowed URL."""
    if target.startswith("//") or SCHEME.match(target):
        return is_allowed_url(target)
    return True


def links_allowed(lines: Sequence[str]) -> bool:
    urls = [url for line in lines for url in URL_PATTERN.findall(line)]
    targets = [
        target
        for line in lines
        for match in LINK_TARGET.findall(line)
        for target in match
        if target
    ]
    return all(map(is_allowed_url, urls)) and all(map(is_allowed_target, targets))


def page_violations(path: str, before: str, after: str) -> tuple[list[Finding], int]:
    """The content violations in one page's edit, and its changed-line count."""
    added, count = diff_lines(before, after)
    kinds = []
    if any(IMPORT_LINE.match(line) for line in added):
        kinds.append(ContentViolation.IMPORT)
    if mdx_constructs(after) - mdx_constructs(before):
        kinds.append(ContentViolation.JSX)
    if not links_allowed(added):
        kinds.append(ContentViolation.LINK)
    return [Finding(path=path, kind=kind) for kind in kinds], count


def check_patch(pristine: Path, edited: Path) -> PatchCheck:
    files = changed_pages(pristine, edited)
    violations: list[Finding] = []
    total = 0
    for path in files:
        before = (pristine / path).read_text("utf-8")
        after = (edited / path).read_text("utf-8")
        found, count = page_violations(path, before, after)
        violations += found
        total += count
    if total > MAX_CHANGED_LINES:
        violations.append(Finding(path=".", kind=ContentViolation.TOO_LONG))
    return PatchCheck(files=files, content_violations=violations)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pristine", type=Path)
    parser.add_argument("edited", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--outputs", type=Path, help="append changed and mergeable here"
    )
    args = parser.parse_args(argv)

    try:
        check = check_patch(args.pristine, args.edited)
    except PatchError as exc:
        logger.error(
            "The docs edits break a path rule (%d finding(s)): %s",
            len(exc.findings),
            exc,
        )
        return 1
    args.out.write_text(check.model_dump_json(indent=2), encoding="utf-8")
    append_outputs(
        args.outputs, changed=str(len(check.files)), mergeable=check.mergeable
    )
    kinds = sorted({finding.kind for finding in check.content_violations})
    logger.info(
        "%d page(s) changed; %d content check(s) failed%s",
        len(check.files),
        len(check.content_violations),
        f": {', '.join(kinds)}" if kinds else "",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
