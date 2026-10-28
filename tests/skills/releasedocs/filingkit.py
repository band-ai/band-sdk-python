"""Kit: drive filefern.py as the workflow does, against the gh and git peers."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from tests.skills.releasedocs.fernremote import FernRemote, write_tree
from tests.skills.releasedocs.ghpeer import FakeGh
from tests.skills.releasedocs.holdbackfiles import release_prs
from tests.skills.releasedocs.samples import (
    DOCS_PAGE,
    DOCS_PAGE_TEXT,
    DOCS_REPORT,
    RELEASE_TAG,
)

EDITED_PAGE = DOCS_PAGE_TEXT.replace("Agent.create()", "Agent.start()")


def read_state(fernstate: ModuleType, gh: FakeGh, tmp_path: Path, *extra: str) -> Path:
    """Record the Fern state; return the directory the filing reads it from."""
    out = tmp_path / "state"
    assert fernstate.main([RELEASE_TAG, "--out", str(out), *extra], gh=gh) == 0
    return out


def docs_args(
    checkpatch: ModuleType,
    fern_files: dict[str, str | bytes],
    tmp_path: Path,
    page: str,
    supersede: tuple[int, ...] = (),
) -> list[str]:
    """Review ``page`` into an edited copy of Fern and check it, as the workflow does."""
    pristine = write_tree(tmp_path / "pristine", fern_files)
    edited = tmp_path / "edited"
    shutil.copytree(pristine, edited)
    write_tree(edited, {DOCS_PAGE: page})
    check, report, docs_range = (
        tmp_path / name for name in ("patch.json", "report.json", "range.json")
    )
    assert checkpatch.main([str(pristine), str(edited), "--out", str(check)]) == 0
    report.write_text(json.dumps(DOCS_REPORT), encoding="utf-8")
    docs_range.write_text(
        json.dumps({"baseline": "band-sdk-v6.0.0", "supersede": list(supersede)}),
        "utf-8",
    )
    return [
        "--docs-check",
        str(check),
        "--docs-report",
        str(report),
        "--docs-range",
        str(docs_range),
        "--edited",
        str(edited),
        "--pristine",
        str(pristine),
    ]


def notes_args(
    make_notes: Callable[..., object],
    items: list[object],
    tmp_path: Path,
    *held: int,
    breaking: tuple[str, ...] = (),
) -> list[str]:
    path, prs = tmp_path / "release-notes.json", tmp_path / "release-prs.json"
    path.write_text(make_notes(items).model_dump_json(), encoding="utf-8")  # type: ignore[attr-defined]
    prs.write_text(json.dumps(release_prs(breaking)), encoding="utf-8")
    return ["--notes", str(path), "--release-prs", str(prs), "--held", *map(str, held)]


def file_fern(
    filefern: ModuleType,
    gh: FakeGh,
    remote: FernRemote,
    tmp_path: Path,
    state: Path,
    mode: str,
    *extra: str,
    wait: Callable[[], None] = lambda: None,
    checks: Callable[..., str] | None = None,
) -> int:
    """File from a fresh clone, as each workflow run does; ``wait`` runs between polls."""
    # A new directory per run: Windows can't delete a clone's read-only objects.
    fern = remote.clone(Path(tempfile.mkdtemp(prefix="fern-write-", dir=tmp_path)))
    argv = [RELEASE_TAG, "--fern", str(fern), "--state", str(state), "--mode", mode]
    argv += ["--outputs", str(tmp_path / "outputs"), *extra]
    return filefern.main(argv, gh=gh, poll=filefern.Poll(2, wait), checks=checks)
