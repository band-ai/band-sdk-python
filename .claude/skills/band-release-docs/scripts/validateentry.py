"""Validate a Fern changelog entry against references/fern-changelog-format.md.

Deterministic gate for the one changelog standard: every rule the reference
states is checked here, so a run cannot improvise its own layout. Exit 0 clean,
1 with one `file:line: message` per violation, 2 on usage error.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

# Keep a Changelog order — also the order categories must appear in a file.
CATEGORIES = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]

# A section requires at least one of its tags. Checked in this direction only:
# published entries legitimately tag a `## Changed` breaking change
# `breaking-change` rather than `improvement`, and carry an `improvement` tag
# for improvements listed under `## Added`.
CATEGORY_TAGS = {
    "Added": ("feature",),
    "Changed": ("improvement", "breaking-change"),
    "Deprecated": ("deprecation", "breaking-change"),
    "Removed": ("breaking-change",),
    "Fixed": ("fix",),
    "Security": ("security",),
}
# Canonical tag order: category tags, then the product tag last.
TAG_ORDER = [
    "feature",
    "improvement",
    "breaking-change",
    "deprecation",
    "security",
    "documentation",
    "fix",
]
SUBSECTION_ONLY_HEADINGS = {"Action Required", "Migration"}
TAGGED_SURFACES = {"sdks"}
SURFACES = ["sdks", "platform", "rest-api", "websocket"]
FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


class Report:
    """Collects violations as `file:line: message`."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[str] = []

    def add(self, line: int, message: str) -> None:
        self.violations.append(f"{self.filename}:{line}: {message}")


def split_frontmatter(text: str) -> tuple[str | None, int]:
    """Return the frontmatter block and the line the body starts on (1-based)."""
    if not text.startswith("---\n"):
        return None, 1
    end = text.find("\n---\n", 3)
    if end == -1:
        return None, 1
    block = text[4 : end + 1]
    return block, text[: end + 5].count("\n") + 1


def parse_tags(block: str) -> list[str] | None:
    match = re.search(r"^tags:\s*\[(.*)\]\s*$", block, re.MULTILINE)
    if not match:
        return None
    return re.findall(r'"([^"]*)"', match.group(1))


def check_filename(path: Path, release_date: str | None, report: Report) -> None:
    if not FILENAME_RE.match(path.name):
        report.add(
            1, f"filename must be YYYY-MM-DD.md (the release date), got {path.name!r}"
        )
    elif release_date and path.stem != release_date:
        report.add(
            1, f"filename {path.name!r} must use the release date {release_date}.md"
        )


def check_frontmatter(
    block: str | None,
    surface: str,
    product_tag: str | None,
    categories: Sequence[str],
    report: Report,
) -> None:
    if surface not in TAGGED_SURFACES:
        if block is not None:
            report.add(
                1,
                f"the {surface} surface carries no frontmatter — remove the tags block",
            )
        return
    if block is None:
        report.add(1, "sdks entries require a frontmatter tags block")
        return

    tags = parse_tags(block)
    if tags is None:
        report.add(
            2,
            'frontmatter must define tags as an inline array, e.g. tags: ["feature", "python"]',
        )
        return

    allowed = set(TAG_ORDER) | ({product_tag} if product_tag else set())
    for tag in tags:
        if tag not in allowed:
            report.add(
                2, f"unknown tag {tag!r} — allowed: {', '.join(sorted(allowed))}"
            )

    known = [t for t in tags if t in TAG_ORDER]
    if known != sorted(known, key=TAG_ORDER.index):
        report.add(2, f"tags must follow the canonical order {TAG_ORDER}, got {known}")
    if product_tag and tags and tags[-1] != product_tag:
        report.add(2, f"the product tag {product_tag!r} must come last, got {tags}")
    if product_tag and product_tag not in tags:
        report.add(2, f"missing product tag {product_tag!r}")

    for category in categories:
        accepted = CATEGORY_TAGS[category]
        if not any(tag in tags for tag in accepted):
            wanted = " or ".join(repr(tag) for tag in accepted)
            report.add(2, f"section '## {category}' requires the {wanted} tag")


def check_headings(
    lines: Sequence[str],
    offset: int,
    breaking: bool,
    product_label: str | None,
    subtitle: str | None,
    report: Report,
) -> list[str]:
    categories: list[str] = []
    entry_titles = 0
    level_two = 0
    saw_subtitle = False

    for index, line in enumerate(lines):
        lineno = offset + index
        heading = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if not heading:
            continue
        level, title = len(heading.group(1)), heading.group(2)

        if level == 1:
            report.add(lineno, "entries start at '## <Category>' — no H1")
            continue
        if level == 2:
            level_two += 1
            if subtitle is not None and title == subtitle:
                saw_subtitle = True
                if level_two != 1:
                    report.add(lineno, f"'## {subtitle}' must open the release block")
                continue
            if title in CATEGORIES:
                if title in categories:
                    report.add(
                        lineno, f"duplicate section '## {title}' — merge the bullets"
                    )
                categories.append(title)
            elif title in SUBSECTION_ONLY_HEADINGS:
                report.add(lineno, f"'{title}' is a '### ' subsection, never '## '")
            elif breaking:
                entry_titles += 1
                if entry_titles > 1:
                    report.add(
                        lineno,
                        f"a breaking-change entry carries one '## ' title, got {title!r} too",
                    )
            else:
                report.add(
                    lineno,
                    f"'## {title}' is not a Keep a Changelog category "
                    f"({', '.join(CATEGORIES)}); entries carry no title outside breaking-change entries",
                )
            continue
        if level == 3 and (re.search(r"\bSDKs?\b", title) or title == product_label):
            report.add(
                lineno,
                f"'### {title}' groups by heading — group products with a "
                f"'- **{title}:**' bullet and nest the entries two spaces under it",
            )

    if subtitle is not None and not saw_subtitle:
        report.add(offset, f"a release block starts with '## {subtitle}'")
    ordered = sorted(categories, key=CATEGORIES.index)
    if categories != ordered:
        report.add(
            offset,
            f"sections must follow Keep a Changelog order {ordered}, got {categories}",
        )
    if not categories:
        report.add(
            offset,
            f"no category section found — expected at least one of {', '.join(CATEGORIES)}",
        )
    return categories


def check_bullets(
    lines: Sequence[str],
    offset: int,
    surface: str,
    product_label: str | None,
    report: Report,
) -> None:
    in_category = False
    saw_bullet = False
    heading_lineno = offset

    for index, line in enumerate(lines):
        lineno = offset + index
        heading = re.match(r"^(#{2,3})\s+(.*?)\s*$", line)
        if heading:
            if in_category and not saw_bullet and heading.group(1) == "##":
                report.add(heading_lineno, "section has no bullets")
            in_category = heading.group(1) == "##" and heading.group(2) in CATEGORIES
            saw_bullet = False
            heading_lineno = lineno
            continue

        bullet = re.match(r"^(\s*)- (.*)$", line)
        if not bullet:
            if in_category and not saw_bullet and line.strip() == "":
                report.add(
                    lineno,
                    "no blank line between a category heading and its first bullet",
                )
            continue

        indent, body = len(bullet.group(1)), bullet.group(2)
        if in_category:
            saw_bullet = True
        if re.match(r"^\*\*[^*]+\*\* - ", body):
            report.add(
                lineno,
                "bullets separate label and sentence with an em dash (—), not a hyphen",
            )
        if not in_category or indent:
            continue
        if surface in TAGGED_SURFACES and product_label:
            expected = f"**{product_label}:**"
            if body.strip() != expected:
                report.add(
                    lineno,
                    f"top-level bullets under a category must be '- {expected}' with the "
                    f"entries nested two spaces under it, got '- {body.strip()}'",
                )

    if in_category and not saw_bullet:
        report.add(heading_lineno, "section has no bullets")


def validate(
    path: Path,
    *,
    surface: str,
    product_label: str | None = None,
    product_tag: str | None = None,
    release_date: str | None = None,
    release_version: str | None = None,
    breaking: bool = False,
) -> list[str]:
    text = path.read_text(encoding="utf-8")
    report = Report(path.name)

    check_filename(path, release_date, report)
    if "<Badge" in text:
        lineno = text[: text.index("<Badge")].count("\n") + 1
        report.add(
            lineno,
            "Badge components were removed from the changelog (thenvoi/fern#325) — tags render them",
        )

    block, body_start = split_frontmatter(text)
    body_lines = text.splitlines()[body_start - 1 :]
    subtitle = (
        f"{product_label} {release_version}"
        if surface in TAGGED_SURFACES and product_label and release_version
        else None
    )
    categories = check_headings(
        body_lines, body_start, breaking, product_label, subtitle, report
    )
    check_bullets(body_lines, body_start, surface, product_label, report)
    check_frontmatter(block, surface, product_tag, categories, report)
    return report.violations


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry", help="path to the changelog entry file")
    parser.add_argument("--surface", required=True, choices=SURFACES)
    parser.add_argument(
        "--product-label", help='product grouping label, e.g. "Python SDK"'
    )
    parser.add_argument("--product-tag", help="product frontmatter tag, e.g. python")
    parser.add_argument("--release-date", help="expected YYYY-MM-DD filename stem")
    parser.add_argument(
        "--release-version", help="the X.Y.Z the '## <label> <version>' subtitle names"
    )
    parser.add_argument(
        "--breaking", action="store_true", help="allow one breaking-change entry title"
    )
    args = parser.parse_args(argv)

    path = Path(args.entry)
    if not path.is_file():
        logger.error("error: %s does not exist", path)
        return 2

    violations = validate(
        path,
        surface=args.surface,
        product_label=args.product_label,
        product_tag=args.product_tag,
        release_date=args.release_date,
        release_version=args.release_version,
        breaking=args.breaking,
    )
    if violations:
        for violation in violations:
            logger.error("%s", violation)
        logger.error(
            "\n%d violation(s) — see references/fern-changelog-format.md",
            len(violations),
        )
        return 1

    sys.stdout.write(f"{path.name}: ok\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
