"""Merging into an existing Fern date file places our bullets and leaves every other line intact."""

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

## Fixed
- **TypeScript SDK:**
  - **LangGraph Reliability** — Agents stay responsive across turns.
"""
ANOTHER_PRODUCT_MERGED = """---
tags: ["feature", "fix", "typescript", "python"]
---

## Added
- **Python SDK:**
  - **Send Retries** — Sends retry with backoff.

## Fixed
- **TypeScript SDK:**
  - **LangGraph Reliability** — Agents stay responsive across turns.
- **Python SDK:**
  - **Reconnect** — Rooms rejoin after a drop.
"""
ANOTHER_PRODUCT_BREAKING_MERGED = """---
tags: ["improvement", "breaking-change", "fix", "typescript", "python"]
---

## Python SDK: Platform Tool Names Now Prefixed

Tools now carry a prefix.

## Changed
- **Python SDK:**
  - **Tool Prefix** — Tool names start with a prefix.

## Fixed
- **TypeScript SDK:**
  - **LangGraph Reliability** — Agents stay responsive across turns.
"""
BAND_MCP = """---
tags: ["feature", "python"]
---

## Added
- **Band MCP:**
  - **Room Tools** — The MCP server lists room tools.
"""
BAND_MCP_MERGED = """---
tags: ["feature", "fix", "python"]
---

## Added
- **Band MCP:**
  - **Room Tools** — The MCP server lists room tools.
- **Python SDK:**
  - **Send Retries** — Sends retry with backoff.

## Fixed
- **Python SDK:**
  - **Reconnect** — Rooms rejoin after a drop.
"""
SAME_DAY = """---
tags: ["fix", "typescript", "python"]
---

## Fixed
- **Python SDK:**
  - **Earlier Fix** — Fixed earlier today.
- **TypeScript SDK:**
  - **Typed Rooms** — Room ids are typed.
"""
SAME_DAY_MERGED = """---
tags: ["feature", "fix", "typescript", "python"]
---

## Added
- **Python SDK:**
  - **Send Retries** — Sends retry with backoff.

## Fixed
- **Python SDK:**
  - **Earlier Fix** — Fixed earlier today.
  - **Reconnect** — Rooms rejoin after a drop.
- **TypeScript SDK:**
  - **Typed Rooms** — Room ids are typed.
"""
UNUSED_CATEGORY = """---
tags: ["deprecation", "typescript"]
---

## Deprecated
- **TypeScript SDK:**
  - **Old Client** — The old client is deprecated.
"""
UNUSED_CATEGORY_MERGED = """---
tags: ["feature", "deprecation", "fix", "typescript", "python"]
---

## Added
- **Python SDK:**
  - **Send Retries** — Sends retry with backoff.

## Deprecated
- **TypeScript SDK:**
  - **Old Client** — The old client is deprecated.

## Fixed
- **Python SDK:**
  - **Reconnect** — Rooms rejoin after a drop.
"""
TWO_BREAKING = """---
tags: ["breaking-change", "typescript", "python"]
---

## TypeScript SDK: Old Rename

TypeScript details.

## Python SDK: Earlier Break

Python details.

## Changed
- **TypeScript SDK:**
  - **Rename** — Clients are renamed.
"""
TWO_BREAKING_MERGED = """---
tags: ["improvement", "breaking-change", "typescript", "python"]
---

## TypeScript SDK: Old Rename

TypeScript details.

## Python SDK: Earlier Break

Python details.

## Python SDK: Platform Tool Names Now Prefixed

Tools now carry a prefix.

## Changed
- **TypeScript SDK:**
  - **Rename** — Clients are renamed.
- **Python SDK:**
  - **Tool Prefix** — Tool names start with a prefix.
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
            (BAND_MCP, BAND_MCP_MERGED),
            (SAME_DAY, SAME_DAY_MERGED),
            (UNUSED_CATEGORY, UNUSED_CATEGORY_MERGED),
        ],
        ids=["another-product", "band-mcp", "same-day-python", "unused-category"],
    )
    def test_items_join_the_date_file(
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
        renderentry.merge_into(notes, tmp_path, [], tmp_path / "pr-body.md")
        assert date_file.read_text(encoding="utf-8") == merged

    @pytest.mark.parametrize(
        ("existing", "merged"),
        [
            (TWO_BREAKING, TWO_BREAKING_MERGED),
            (ANOTHER_PRODUCT, ANOTHER_PRODUCT_BREAKING_MERGED),
        ],
        ids=["after-breaking-blocks", "before-the-categories"],
    )
    def test_breaking_block_joins_the_date_file(
        self,
        renderentry: ModuleType,
        releasedata: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        existing: str,
        merged: str,
    ) -> None:
        date_file = tmp_path / f"{RELEASE_DATE}.md"
        date_file.write_text(existing, encoding="utf-8", newline="")
        breaking = releasedata.Breaking(
            title="Platform Tool Names Now Prefixed",
            markdown="Tools now carry a prefix.",
        )
        notes = make_notes([make_item(*TOOL_PREFIX)], breaking)
        renderentry.merge_into(notes, tmp_path, [], tmp_path / "pr-body.md")
        assert date_file.read_text(encoding="utf-8") == merged

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
        "damaged",
        [
            SAME_DAY_MERGED.replace("  - **Earlier Fix** — Fixed earlier today.\n", ""),
            SAME_DAY_MERGED.replace(
                "- **TypeScript SDK:**\n  - **Typed Rooms** — Room ids are typed.\n", ""
            ).replace(
                "## Fixed\n",
                "## Fixed\n- **TypeScript SDK:**\n  - **Typed Rooms** — Room ids are typed.\n",
            ),
            SAME_DAY_MERGED.replace(
                "  - **Reconnect** — Rooms rejoin after a drop.\n", ""
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
    ) -> None:
        notes = make_notes([make_item(*SEND_RETRIES), make_item(*RECONNECT)])
        standalone = renderentry.render_standalone(notes)
        with pytest.raises(renderentry.EntryError):
            renderentry.check_merge(SAME_DAY, damaged, standalone)

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
