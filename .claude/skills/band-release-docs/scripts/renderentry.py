# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Render ``release-notes.json`` as a Fern changelog entry.

``--out DIR`` writes the standalone ``<release_date>.md``. ``--merge-into DIR``
merges the entry into that directory's date file, leaving every other line
unchanged, and writes the Fern PR body to ``--pr-body``. Either way the
standalone entry must pass ``validateentry.py`` first.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from releasedata import Category, Item, ReleaseNotes
from textsafety import mdx_text, pr_body_text
from validateentry import CATEGORY_TAGS, TAG_ORDER, validate

logger = logging.getLogger(__name__)

SURFACE = "sdks"
# Removed's only accepted tag is the breaking-change tag.
BREAKING_TAG = CATEGORY_TAGS[Category.REMOVED][0]
FRONTMATTER_FENCE = "---"
TAGS_LINE = re.compile(r"^tags:\s*\[(.*)\]\s*$")
SECTION_HEADING = re.compile(r"^## (.*?)\s*$")
CATEGORY_TITLES = frozenset(Category)


class EntryError(ValueError):
    """The rendered or merged entry breaks the format or the merge invariant."""


def entry_path(directory: Path, notes: ReleaseNotes) -> Path:
    return directory / f"{notes.release_date}.md"


def product_bullet(notes: ReleaseNotes) -> str:
    return f"- **{notes.product.label}:**"


def item_line(item: Item) -> str:
    return f"  - **{mdx_text(item.label)}** — {mdx_text(item.sentence)}"


def kept_categories(notes: ReleaseNotes) -> list[Category]:
    present = {item.category for item in notes.items}
    return [category for category in Category if category in present]


def entry_tags(notes: ReleaseNotes) -> list[str]:
    tags = {CATEGORY_TAGS[category][0] for category in kept_categories(notes)}
    if notes.breaking is not None:
        tags.add(BREAKING_TAG)
    return [tag for tag in TAG_ORDER if tag in tags] + [notes.product.tag]


def tags_line(tags: Sequence[str]) -> str:
    return f"tags: {json.dumps(list(tags))}"


def breaking_block(notes: ReleaseNotes) -> list[str]:
    if notes.breaking is None:
        return []
    label = notes.product.label
    title = notes.breaking.title.removeprefix(f"{label}:").strip()
    markdown = mdx_text(notes.breaking.markdown.strip())
    return [f"## {label}: {mdx_text(title)}", "", *markdown.splitlines()]


def category_block(notes: ReleaseNotes, category: Category) -> list[str]:
    items = notes.items_in({category})
    return [f"## {category}", product_bullet(notes), *map(item_line, items)]


def render_standalone(notes: ReleaseNotes) -> str:
    blocks = [breaking_block(notes)] if notes.breaking else []
    blocks += [category_block(notes, category) for category in kept_categories(notes)]
    body = "\n\n".join("\n".join(block) for block in blocks)
    frontmatter = [FRONTMATTER_FENCE, tags_line(entry_tags(notes)), FRONTMATTER_FENCE]
    return "\n".join(frontmatter) + "\n\n" + body + "\n"


def check_standalone(notes: ReleaseNotes, text: str) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        path = entry_path(Path(scratch), notes)
        path.write_text(text, encoding="utf-8", newline="")
        violations = validate(
            path,
            surface=SURFACE,
            product_label=notes.product.label,
            product_tag=notes.product.tag,
            release_date=notes.release_date,
            breaking=notes.breaking is not None,
        )
    if violations:
        raise EntryError("\n".join(violations))


@dataclass(frozen=True)
class Section:
    """A ``## `` section: heading line, content, and trailing blank lines."""

    title: str
    start: int
    end: int
    content_end: int


def body_start(lines: list[str]) -> int:
    if not lines or lines[0] != FRONTMATTER_FENCE:
        return 0
    return lines.index(FRONTMATTER_FENCE, 1) + 1


def sections(lines: list[str]) -> list[Section]:
    starts = [
        (index, match.group(1))
        for index in range(body_start(lines), len(lines))
        if (match := SECTION_HEADING.match(lines[index]))
    ]
    found = []
    for position, (start, title) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        content_end = end
        while content_end > start + 1 and not lines[content_end - 1].strip():
            content_end -= 1
        found.append(Section(title, start, end, content_end))
    return found


def is_category(section: Section) -> bool:
    return section.title in CATEGORY_TITLES


def merged_tags(lines: list[str], notes: ReleaseNotes) -> list[str]:
    if body_start(lines) == 0:
        return entry_tags(notes)
    fence_end = body_start(lines) - 1
    existing = next(
        (
            re.findall(r'"([^"]*)"', match.group(1))
            for line in lines[1:fence_end]
            if (match := TAGS_LINE.match(line))
        ),
        [],
    )
    known = set(existing) | set(entry_tags(notes))
    products = [tag for tag in [*existing, notes.product.tag] if tag not in TAG_ORDER]
    return [tag for tag in TAG_ORDER if tag in known] + list(dict.fromkeys(products))


def with_tags(lines: list[str], tags: list[str]) -> list[str]:
    if body_start(lines) == 0:
        return [FRONTMATTER_FENCE, tags_line(tags), FRONTMATTER_FENCE, "", *lines]
    fence_end = body_start(lines) - 1
    head = [
        tags_line(tags) if TAGS_LINE.match(line) else line for line in lines[:fence_end]
    ]
    return head + lines[fence_end:]


def insert(lines: list[str], at: int, new: list[str]) -> list[str]:
    return lines[:at] + new + lines[at:]


def with_items(lines: list[str], notes: ReleaseNotes, category: Category) -> list[str]:
    """Place one category's items under its section and product bullet."""
    items = list(map(item_line, notes.items_in({category})))
    found = sections(lines)
    section = next((s for s in found if s.title == category), None)
    if section is not None:
        bullet = product_bullet(notes)
        own = range(section.start + 1, section.content_end)
        product = next((index for index in own if lines[index] == bullet), None)
        if product is None:
            return insert(lines, section.content_end, [bullet, *items])
        nested_end = next(
            (
                index
                for index in own
                if index > product and not lines[index].startswith(" ")
            ),
            section.content_end,
        )
        return insert(lines, nested_end, items)
    block = category_block(notes, category)
    order = list(Category)
    categories = [s for s in found if is_category(s)]
    later = [
        s for s in categories if order.index(Category(s.title)) > order.index(category)
    ]
    if later:
        return insert(lines, later[0].start, [*block, ""])
    at = categories[-1].content_end if categories else len(lines)
    return insert(lines, at, ["", *block])


def with_breaking(lines: list[str], notes: ReleaseNotes) -> list[str]:
    """Place the breaking block after any existing ones, before the categories."""
    block = breaking_block(notes)
    if not block:
        return lines
    found = sections(lines)
    breaking = [s for s in found if not is_category(s)]
    if breaking:
        return insert(lines, breaking[-1].content_end, ["", *block])
    if found:
        return insert(lines, found[0].start, [*block, ""])
    return [*lines, "", *block]


def merge_entry(existing: str, notes: ReleaseNotes) -> str:
    lines = existing.splitlines()
    merged = with_tags(lines, merged_tags(lines, notes))
    merged = with_breaking(merged, notes)
    for category in kept_categories(notes):
        merged = with_items(merged, notes, category)
    return "\n".join(merged) + "\n"


def is_subsequence(needles: list[str], haystack: list[str]) -> bool:
    remaining = iter(haystack)
    return all(line in remaining for line in needles)


def check_merge(existing: str, merged: str, standalone: str) -> None:
    """Every earlier line survives in order, and every line we add is present."""
    before = [line for line in existing.splitlines() if not TAGS_LINE.match(line)]
    after = merged.splitlines()
    if not is_subsequence(before, after):
        raise EntryError("the merge dropped or reordered an existing line")
    standalone_lines = standalone.splitlines()
    body = standalone_lines[body_start(standalone_lines) :]
    ours = [line for line in body if line.strip()]
    missing = [line for line in ours if line not in after]
    if missing:
        raise EntryError(f"the merge lost our lines: {missing}")


def held_back(
    notes: ReleaseNotes, drop: Sequence[int]
) -> tuple[ReleaseNotes, list[Item]]:
    """Split ``notes`` into the notes to file and the items held back by index."""
    held = [item for index, item in enumerate(notes.items) if index in drop]
    kept = [item for index, item in enumerate(notes.items) if index not in drop]
    return notes.model_copy(update={"items": kept}), held


def item_summary(item: Item) -> str:
    return pr_body_text(f"- **{item.label}** — {item.sentence}")


def pr_body(notes: ReleaseNotes, held: list[Item]) -> str:
    holdbacks = list(map(item_summary, held)) or ["None."]
    return "\n".join(
        [
            "## Summary",
            "",
            f"{notes.product.label} {notes.tag} changelog entry.",
            "",
            *map(item_summary, notes.items),
            "",
            "## Source",
            "",
            f"- Release: {notes.release_url}",
            f"- Range: `{notes.previous_tag}...{notes.tag}`",
            f"- Product: {notes.product.label}",
            "",
            "## Holdbacks",
            "",
            *holdbacks,
            "",
        ]
    )


def write_standalone(notes: ReleaseNotes, directory: Path) -> Path:
    text = render_standalone(notes)
    check_standalone(notes, text)
    path = entry_path(directory, notes)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def merge_into(
    notes: ReleaseNotes, directory: Path, drop: Sequence[int], body: Path
) -> Path:
    kept, held = held_back(notes, drop)
    if not kept.items:
        raise EntryError("every item is held back; there is no entry to file")
    standalone = render_standalone(kept)
    check_standalone(kept, standalone)
    path = entry_path(directory, kept)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    merged = merge_entry(existing, kept) if existing else standalone
    check_merge(existing, merged, standalone)
    path.write_text(merged, encoding="utf-8", newline="")
    body.write_text(pr_body(kept, held), encoding="utf-8", newline="")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notes", type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--out", type=Path, help="write the standalone entry here")
    target.add_argument(
        "--merge-into", type=Path, help="merge into this date-file directory"
    )
    parser.add_argument(
        "--drop", type=int, nargs="*", default=[], help="item indexes to hold back"
    )
    parser.add_argument(
        "--pr-body", type=Path, help="with --merge-into: write the PR body here"
    )
    args = parser.parse_args(argv)

    notes = ReleaseNotes.model_validate_json(args.notes.read_text(encoding="utf-8"))
    try:
        if args.out is not None:
            path = write_standalone(notes, args.out)
        elif args.pr_body is None:
            parser.error("--merge-into needs --pr-body")
        else:
            path = merge_into(notes, args.merge_into, args.drop, args.pr_body)
    except EntryError as exc:
        logger.error("The entry is rejected:\n%s", exc)
        return 1
    logger.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
