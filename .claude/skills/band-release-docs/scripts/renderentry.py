# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Render ``release-notes.json`` as a Fern changelog entry.

``--out DIR`` writes the standalone ``<release_date>.md``. ``--merge-into DIR``
adds the entry to that directory's date file as a release block above the
earlier ones, leaving every other line unchanged, and writes the Fern PR body to
``--pr-body``. Either way the standalone entry must pass ``validateentry.py``
first.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path

from releasedata import Category, Item, ReleaseNotes
from textsafety import mdx_text, pr_body_text, without_fenced_code
from validateentry import CATEGORY_TAGS, TAG_ORDER, parse_tags, validate

logger = logging.getLogger(__name__)

SURFACE = "sdks"
# Removed's only accepted tag is the breaking-change tag.
BREAKING_TAG = CATEGORY_TAGS[Category.REMOVED][0]
FRONTMATTER_FENCE = "---"
TAGS_LINE = re.compile(r"^tags:\s*\[(.*)\]\s*$")
# A release block's opening line, `## <SDK> <version>`, for any SDK.
RELEASE_SUBTITLE = re.compile(r"^## .+ \d+\.\d+\.\d+\s*$")


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


def subtitle(notes: ReleaseNotes) -> str:
    return f"## {notes.product.label} {notes.version}"


def render_standalone(notes: ReleaseNotes) -> str:
    blocks = [[subtitle(notes)]]
    blocks += [breaking_block(notes)] if notes.breaking else []
    blocks += [category_block(notes, category) for category in kept_categories(notes)]
    body = "\n\n".join("\n".join(block) for block in blocks)
    frontmatter = [FRONTMATTER_FENCE, tags_line(entry_tags(notes)), FRONTMATTER_FENCE]
    return "\n".join(frontmatter) + "\n\n" + body + "\n"


def checked_standalone(notes: ReleaseNotes) -> str:
    """Render the standalone entry and fail unless it passes the validator."""
    text = render_standalone(notes)
    with tempfile.TemporaryDirectory() as scratch:
        path = entry_path(Path(scratch), notes)
        # The vendored validator reads `#` and `- ` lines inside code fences as
        # headings and bullets, so it checks the entry with fence bodies blanked.
        path.write_text(without_fenced_code(text), encoding="utf-8", newline="")
        violations = validate(
            path,
            surface=SURFACE,
            product_label=notes.product.label,
            product_tag=notes.product.tag,
            release_date=notes.release_date,
            release_version=notes.version,
            breaking=notes.breaking is not None,
        )
    if violations:
        raise EntryError("\n".join(violations))
    return text


def body_start(lines: list[str]) -> int:
    if not lines or lines[0] != FRONTMATTER_FENCE:
        return 0
    return lines.index(FRONTMATTER_FENCE, 1) + 1


def body(lines: list[str]) -> list[str]:
    """The lines after the frontmatter, without leading blank lines."""
    rest = lines[body_start(lines) :]
    first = next((index for index, line in enumerate(rest) if line.strip()), len(rest))
    return rest[first:]


def merged_tags(lines: list[str], notes: ReleaseNotes) -> list[str]:
    start = body_start(lines)
    if start == 0:
        return entry_tags(notes)
    existing = parse_tags("\n".join(lines[1 : start - 1])) or []
    known = set(existing) | set(entry_tags(notes))
    products = [tag for tag in [*existing, notes.product.tag] if tag not in TAG_ORDER]
    return [tag for tag in TAG_ORDER if tag in known] + list(dict.fromkeys(products))


def check_mergeable(existing: list[str], notes: ReleaseNotes) -> None:
    """Refuse a file that already holds this release, or whose releases have no subtitle."""
    if subtitle(notes) in existing:
        raise EntryError(
            f"the date file already holds '{subtitle(notes)}'; "
            "start again from a fresh worktree"
        )
    earlier = body(existing)
    if earlier and not RELEASE_SUBTITLE.match(earlier[0]):
        raise EntryError(
            "the date file's entries predate version subtitles; "
            "add their '## <SDK> <version>' subtitle first"
        )


def merge_entry(existing: str, notes: ReleaseNotes, standalone: str) -> str:
    """Put this release's block above the earlier ones, with the tags combined."""
    lines = existing.splitlines()
    tags = merged_tags(lines, notes)
    frontmatter = [FRONTMATTER_FENCE, tags_line(tags), FRONTMATTER_FENCE]
    ours = body(standalone.splitlines())
    return "\n".join([*frontmatter, "", *ours, "", *body(lines)]) + "\n"


def is_subsequence(needles: list[str], haystack: list[str]) -> bool:
    remaining = iter(haystack)
    return all(line in remaining for line in needles)


def check_merge(existing: str, merged: str, standalone: str) -> None:
    """Every earlier line survives in order, and every line we add is present."""
    before = [line for line in existing.splitlines() if not TAGS_LINE.match(line)]
    after = merged.splitlines()
    if not is_subsequence(before, after):
        raise EntryError("the merge dropped or reordered an existing line")
    ours = [line for line in body(standalone.splitlines()) if line.strip()]
    missing = [line for line in ours if line not in after]
    if missing:
        raise EntryError(f"the merge lost our lines: {missing}")


def held_back(
    notes: ReleaseNotes, drop: Sequence[int]
) -> tuple[ReleaseNotes, list[Item]]:
    """Split ``notes`` into the notes to file and the items held back by index."""
    unknown = sorted(set(drop) - set(range(len(notes.items))))
    if unknown:
        raise EntryError(
            f"no item has index {unknown}; items are numbered 0 to {len(notes.items) - 1}"
        )
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
    text = checked_standalone(notes)
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
    standalone = checked_standalone(kept)
    path = entry_path(directory, kept)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    check_mergeable(existing.splitlines(), kept)
    merged = merge_entry(existing, kept, standalone) if existing else standalone
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
