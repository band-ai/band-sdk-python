"""Merging adds our release block above the date file's earlier releases and keeps every other line."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.samples import (
    RECONNECT,
    RELEASE_DATE,
    RELEASE_URL,
    SEND_RETRIES,
    TOOL_PREFIX,
)

ANOTHER_PRODUCT = """---
tags: ["fix", "typescript"]
---

## TypeScript SDK 0.1.10

## Fixed
- **TypeScript SDK:**
  - **LangGraph Reliability** — Agents stay responsive across turns.
"""
ANOTHER_PRODUCT_MERGED = """---
tags: ["feature", "fix", "typescript", "python"]
---

## Python SDK 6.1.0

## Added
- **Python SDK:**
  - **Send Retries** — Sends retry with backoff.

## Fixed
- **Python SDK:**
  - **Reconnect** — Rooms rejoin after a drop.

## TypeScript SDK 0.1.10

## Fixed
- **TypeScript SDK:**
  - **LangGraph Reliability** — Agents stay responsive across turns.
"""
EARLIER_RELEASE = """---
tags: ["breaking-change", "fix", "python"]
---

## Python SDK 6.0.1

## Python SDK: Legacy Client Removed

The legacy client is gone.

## Fixed
- **Python SDK:**
  - **Earlier Fix** — Fixed earlier today.
"""
EARLIER_RELEASE_MERGED = """---
tags: ["feature", "breaking-change", "fix", "python"]
---

## Python SDK 6.1.0

## Added
- **Python SDK:**
  - **Send Retries** — Sends retry with backoff.

## Fixed
- **Python SDK:**
  - **Reconnect** — Rooms rejoin after a drop.

## Python SDK 6.0.1

## Python SDK: Legacy Client Removed

The legacy client is gone.

## Fixed
- **Python SDK:**
  - **Earlier Fix** — Fixed earlier today.
"""
EARLIER_RELEASE_BREAKING_MERGED = """---
tags: ["improvement", "breaking-change", "fix", "python"]
---

## Python SDK 6.1.0

## Python SDK: Platform Tool Names Now Prefixed

Tools now carry a prefix.

## Changed
- **Python SDK:**
  - **Tool Prefix** — Tool names start with a prefix.

## Python SDK 6.0.1

## Python SDK: Legacy Client Removed

The legacy client is gone.

## Fixed
- **Python SDK:**
  - **Earlier Fix** — Fixed earlier today.
"""
# Filed before Fern's version subtitles: which release these items belong to
# cannot be told apart from ours once our block sits above them.
WITHOUT_SUBTITLE = """---
tags: ["fix", "python"]
---

## Fixed
- **Python SDK:**
  - **Earlier Fix** — Fixed earlier today.
"""
NEWER_RELEASE = """---
tags: ["fix", "python"]
---

## Python SDK 6.1.1

## Fixed
- **Python SDK:**
  - **Hotfix** — Fixed later today.
"""
# A newer release filed above ours, then ours filed again.
NEWER_ABOVE_OURS = NEWER_RELEASE + ANOTHER_PRODUCT_MERGED.split("---\n\n", 1)[1]
PR_BODY = f"""## Summary

Python SDK band-sdk-v6.1.0 changelog entry.

- **Send Retries** — Sends retry with backoff.
- **Reconnect** — Rooms rejoin after a drop.

## Source

- Release: {RELEASE_URL}
- Range: `band-sdk-v6.0.1...band-sdk-v6.1.0`
- Product: Python SDK

## Holdbacks

None.
"""
PR_BODY_WITH_HOLDBACK = f"""## Summary

Python SDK band-sdk-v6.1.0 changelog entry.

- **Send Retries** — Sends retry with backoff.

## Source

- Release: {RELEASE_URL}
- Range: `band-sdk-v6.0.1...band-sdk-v6.1.0`
- Product: Python SDK

## Holdbacks

- **Reconnect** — Rooms rejoin after a drop.
"""


class TestMergeEntry:
    @pytest.mark.parametrize(
        ("existing", "merged"),
        [
            (ANOTHER_PRODUCT, ANOTHER_PRODUCT_MERGED),
            (EARLIER_RELEASE, EARLIER_RELEASE_MERGED),
        ],
        ids=["another-product", "earlier-python-release"],
    )
    def test_release_block_goes_above_the_earlier_ones(
        self,
        renderentry: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        existing: str,
        merged: str,
    ) -> None:
        date_file = tmp_path / f"{RELEASE_DATE}.md"
        date_file.write_text(existing, encoding="utf-8", newline="")
        notes = make_notes([make_item(*SEND_RETRIES), make_item(*RECONNECT)])
        body = tmp_path / "pr-body.md"
        renderentry.merge_into(notes, tmp_path, [], body)
        assert date_file.read_text(encoding="utf-8") == merged
        assert body.read_text(encoding="utf-8") == PR_BODY

    def test_breaking_release_block_keeps_its_breaking_section(
        self,
        renderentry: ModuleType,
        releasedata: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        date_file = tmp_path / f"{RELEASE_DATE}.md"
        date_file.write_text(EARLIER_RELEASE, encoding="utf-8", newline="")
        breaking = releasedata.Breaking(
            title="Platform Tool Names Now Prefixed",
            markdown="Tools now carry a prefix.",
        )
        notes = make_notes([make_item(*TOOL_PREFIX)], breaking)
        renderentry.merge_into(notes, tmp_path, [], tmp_path / "pr-body.md")
        assert date_file.read_text(encoding="utf-8") == EARLIER_RELEASE_BREAKING_MERGED

    @pytest.mark.parametrize(
        ("existing", "error"),
        [
            (WITHOUT_SUBTITLE, "predate version subtitles"),
            (NEWER_RELEASE, "newest first"),
            (NEWER_ABOVE_OURS, "already holds '## Python SDK 6.1.0'"),
        ],
        ids=["without-subtitles", "newer-release", "re-run-below-a-newer-release"],
    )
    def test_unmergeable_date_file_is_left_unchanged(
        self,
        renderentry: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        existing: str,
        error: str,
    ) -> None:
        date_file = tmp_path / f"{RELEASE_DATE}.md"
        date_file.write_text(existing, encoding="utf-8", newline="")
        notes = make_notes([make_item(*SEND_RETRIES), make_item(*RECONNECT)])
        with pytest.raises(renderentry.EntryError, match=error):
            renderentry.merge_into(notes, tmp_path, [], tmp_path / "pr-body.md")
        assert date_file.read_text(encoding="utf-8") == existing

    def test_unwritable_pr_body_leaves_the_date_file_unchanged(
        self,
        renderentry: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        date_file = tmp_path / f"{RELEASE_DATE}.md"
        date_file.write_text(EARLIER_RELEASE, encoding="utf-8", newline="")
        notes = make_notes([make_item(*SEND_RETRIES)])
        with pytest.raises(FileNotFoundError):
            renderentry.merge_into(notes, tmp_path, [], tmp_path / "no-dir" / "b.md")
        assert date_file.read_text(encoding="utf-8") == EARLIER_RELEASE

    def test_new_date_file_is_the_standalone_entry(
        self,
        renderentry: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        notes = make_notes([make_item(*SEND_RETRIES)])
        path = renderentry.merge_into(notes, tmp_path, [], tmp_path / "pr-body.md")
        assert path.read_text(encoding="utf-8") == renderentry.render_standalone(notes)

    @pytest.mark.parametrize(
        ("damaged", "error"),
        [
            (
                EARLIER_RELEASE_MERGED.replace(
                    "  - **Earlier Fix** — Fixed earlier today.\n", ""
                ),
                "dropped or reordered",
            ),
            (
                EARLIER_RELEASE_MERGED.replace(
                    "The legacy client is gone.\n\n## Fixed",
                    "## Fixed",
                ).replace(
                    "  - **Earlier Fix** — Fixed earlier today.\n",
                    "  - **Earlier Fix** — Fixed earlier today.\nThe legacy client is gone.\n",
                ),
                "dropped or reordered",
            ),
            (
                EARLIER_RELEASE_MERGED.replace(
                    "  - **Reconnect** — Rooms rejoin after a drop.\n", ""
                ),
                "lost our lines",
            ),
        ],
        ids=["dropped-line", "reordered-lines", "lost-our-line"],
    )
    def test_structural_check_rejects_a_damaged_merge(
        self,
        renderentry: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        damaged: str,
        error: str,
    ) -> None:
        notes = make_notes([make_item(*SEND_RETRIES), make_item(*RECONNECT)])
        standalone = renderentry.render_standalone(notes)
        with pytest.raises(renderentry.EntryError, match=error):
            renderentry.check_merge(EARLIER_RELEASE, damaged, standalone)

    def test_held_item_is_omitted_and_listed_in_the_pr_body(
        self,
        renderentry: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        notes = make_notes([make_item(*SEND_RETRIES), make_item(*RECONNECT)])
        notes_file = tmp_path / "release-notes.json"
        notes_file.write_text(notes.model_dump_json(), encoding="utf-8")
        fern, body = tmp_path / "fern", tmp_path / "pr-body.md"
        fern.mkdir()
        argv = [
            str(notes_file),
            "--merge-into",
            str(fern),
            "--drop",
            "1",
            "--pr-body",
            str(body),
        ]
        assert renderentry.main(argv) == 0
        assert (fern / f"{RELEASE_DATE}.md").read_text(encoding="utf-8") == (
            renderentry.render_standalone(make_notes([make_item(*SEND_RETRIES)]))
        )
        assert body.read_text(encoding="utf-8") == PR_BODY_WITH_HOLDBACK

    @pytest.mark.parametrize(
        "drop",
        [["2"], ["-1"], ["0", "1"]],
        ids=["past-the-end", "negative", "every-item"],
    )
    def test_unusable_holdback_files_nothing(
        self,
        renderentry: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        drop: list[str],
    ) -> None:
        notes = make_notes([make_item(*SEND_RETRIES), make_item(*RECONNECT)])
        notes_file = tmp_path / "release-notes.json"
        notes_file.write_text(notes.model_dump_json(), encoding="utf-8")
        fern, body = tmp_path / "fern", tmp_path / "pr-body.md"
        fern.mkdir()
        argv = [str(notes_file), "--merge-into", str(fern), "--drop", *drop]
        assert renderentry.main([*argv, "--pr-body", str(body)]) == 1
        assert (list(fern.iterdir()), body.exists()) == ([], False)

    def test_entry_already_in_the_date_file_is_not_merged_again(
        self,
        renderentry: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        notes = make_notes([make_item(*SEND_RETRIES), make_item(*RECONNECT)])
        path = renderentry.merge_into(notes, tmp_path, [], tmp_path / "pr-body.md")
        first = path.read_text(encoding="utf-8")
        with pytest.raises(renderentry.EntryError, match="already holds"):
            renderentry.merge_into(notes, tmp_path, [1], tmp_path / "pr-body.md")
        assert path.read_text(encoding="utf-8") == first
