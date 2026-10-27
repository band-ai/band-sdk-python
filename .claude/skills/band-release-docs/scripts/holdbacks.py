# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3", "pyyaml>=6,<7"]
# ///
"""Hold back the release items Fern's held-items ledger keeps unpublished.

An item is held when the holdbacks review flags its index or when a ledger
entry for the ``sdks`` surface cites one of its PRs. Anything that cannot be
decided fails closed, writing nothing: an unreadable ledger or report, an
unknown index, or a held breaking change, which has no index to drop. Writes
the kept notes to ``--out`` and appends ``has_entry`` and ``held`` (the held
indexes) to ``--outputs``. Logs counts only: the ledger is private.
"""

from __future__ import annotations

import argparse
import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeVar

import yaml
from collectprs import ANNOUNCED_ASSET
from pydantic import BaseModel, ConfigDict, ValidationError
from releasedata import HoldbacksReport, ReleaseNotes, ReleasePrs
from renderentry import SURFACE
from stepoutputs import append_outputs

logger = logging.getLogger(__name__)

SDK_REPO_NAME = "band-sdk-python"
# A ledger source: `<repo>#<number>`, the repo optionally owner-qualified.
LEDGER_SOURCE = re.compile(r"^(?:[\w.-]+/)?(?P<repo>[\w.-]+)#(?P<number>\d+)$")
FLAGGED_BY_REVIEW = "flagged by the holdbacks review"

Model = TypeVar("Model", bound=BaseModel)


class HoldbacksError(ValueError):
    """The holdbacks cannot be decided, so nothing may be announced or filed."""


class LedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    source: str
    reason: str
    surfaces: list[str]
    released_surfaces: list[str] = []

    @property
    def holds_sdks(self) -> bool:
        return SURFACE in self.surfaces and SURFACE not in self.released_surfaces

    @property
    def sdk_pr(self) -> str | None:
        """The band-sdk-python PR number this entry cites, if any."""
        match = LEDGER_SOURCE.match(self.source)
        if match is None or match["repo"] != SDK_REPO_NAME:
            return None
        return match["number"]

    def cites(self, sources: Sequence[str]) -> bool:
        """Whether this entry names one of ``sources``, by PR number or verbatim."""
        return self.source in sources or self.sdk_pr in sources


class Ledger(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    held_items: list[LedgerEntry]


def load_ledger(path: Path) -> list[LedgerEntry]:
    """The ledger entries still held from the ``sdks`` surface."""
    try:
        ledger = Ledger.model_validate(yaml.safe_load(path.read_text("utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        raise HoldbacksError(
            f"the ledger is unreadable ({type(exc).__name__})"
        ) from exc
    except ValidationError as exc:
        raise HoldbacksError(
            f"the ledger does not match its shape ({exc.error_count()} error(s))"
        ) from exc
    return [entry for entry in ledger.held_items if entry.holds_sdks]


def ledger_matches(
    notes: ReleaseNotes, entries: Sequence[LedgerEntry]
) -> dict[int, LedgerEntry]:
    """Map each item index to the first ledger entry citing one of its sources."""
    return {
        index: entry
        for index, item in enumerate(notes.items)
        if (entry := next((e for e in entries if e.cites(item.sources)), None))
    }


def held_indexes(
    notes: ReleaseNotes,
    release: ReleasePrs,
    entries: Sequence[LedgerEntry],
    report: HoldbacksReport,
) -> list[int]:
    """The union of flagged and ledger-matched indexes; fail closed on the rest."""
    unknown = sorted(set(report.held) - set(range(len(notes.items))))
    if unknown:
        raise HoldbacksError(f"the review flagged unknown item index(es) {unknown}")
    breaking = release.breaking_sources
    if report.hold_breaking or any(entry.cites(breaking) for entry in entries):
        raise HoldbacksError("the breaking change is held; file this release by hand")
    return sorted(set(report.held) | set(ledger_matches(notes, entries)))


def held_reasons(
    notes: ReleaseNotes, held: Sequence[int], entries: Sequence[LedgerEntry]
) -> dict[int, str]:
    """Explain each held index with its ledger entry, or as the review's flag."""
    matches = ledger_matches(notes, entries)
    return {
        index: f"{entry.id}: {entry.reason}"
        if (entry := matches.get(index))
        else FLAGGED_BY_REVIEW
        for index in held
    }


def kept_items(notes: ReleaseNotes, held: Sequence[int]) -> ReleaseNotes:
    items = [item for index, item in enumerate(notes.items) if index not in held]
    return notes.model_copy(update={"items": items})


def write_holdbacks(
    notes: ReleaseNotes, held: Sequence[int], out: Path, outputs: Path | None
) -> None:
    kept = kept_items(notes, held)
    if kept.items:
        out.mkdir(parents=True, exist_ok=True)
        (out / ANNOUNCED_ASSET).write_text(
            kept.model_dump_json(indent=2), encoding="utf-8"
        )
    append_outputs(outputs, has_entry=bool(kept.items), held=" ".join(map(str, held)))


def read_model(model: type[Model], path: Path, name: str) -> Model:
    try:
        return model.model_validate_json(path.read_bytes())
    except OSError as exc:
        raise HoldbacksError(f"no {name} ({type(exc).__name__})") from exc
    except ValidationError as exc:
        raise HoldbacksError(
            f"the {name} does not match its shape ({exc.error_count()} error(s))"
        ) from exc


def summary_counts(held: Sequence[int], matched: Mapping[int, LedgerEntry]) -> str:
    by_ledger = len(set(held) & set(matched))
    return f"{len(held)} held ({by_ledger} by the ledger, {len(held) - by_ledger} by the review only)"


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notes", type=Path, help="the full release-notes.json")
    parser.add_argument("release_prs", type=Path)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, help="append has_entry and held here")
    args = parser.parse_args(argv)

    try:
        notes = read_model(ReleaseNotes, args.notes, "release notes")
        release = read_model(ReleasePrs, args.release_prs, "release PRs")
        report = read_model(HoldbacksReport, args.report, "holdbacks report")
        entries = load_ledger(args.ledger)
        held = held_indexes(notes, release, entries, report)
    except HoldbacksError as exc:
        logger.error("Holdbacks failed closed: %s", exc)
        return 1
    write_holdbacks(notes, held, args.out, args.outputs)
    logger.info(
        "%d item(s): %s",
        len(notes.items),
        summary_counts(held, ledger_matches(notes, entries)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
