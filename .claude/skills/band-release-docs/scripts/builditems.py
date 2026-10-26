# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Check the model's report against the collected release and build ``release-notes.json``.

Fails without writing when an item cites nothing or a source outside the
release, a breaking change has no breaking block, or any text holds a
credential pattern or a URL. Appends ``has_entry`` to ``--outputs``.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from releasedata import ChangelogReport, Product, ReleaseNotes, ReleasePrs, ReportStatus
from stepoutputs import append_outputs
from textsafety import unsafe_findings

logger = logging.getLogger(__name__)


def report_problems(report: ChangelogReport, release: ReleasePrs) -> list[str]:
    """List every way the report fails to trace to the release or is unsafe."""
    known = release.sources
    problems = [
        f"item {item.label!r} cites no source"
        for item in report.items
        if not item.sources
    ]
    problems += [
        f"item {item.label!r} cites {source!r}, which is not in this release"
        for item in report.items
        for source in item.sources
        if source not in known
    ]
    if release.breaking_sources and report.breaking is None:
        problems.append(
            f"{', '.join(release.breaking_sources)} is breaking but the report has no breaking block"
        )
    texts = [text for item in report.items for text in (item.label, item.sentence)]
    if report.breaking is not None:
        texts += [report.breaking.title, report.breaking.markdown]
    problems += [
        f"unsafe text: {finding}" for text in texts for finding in unsafe_findings(text)
    ]
    return problems


def build_notes(
    report: ChangelogReport, release: ReleasePrs, product: Product
) -> ReleaseNotes | None:
    """Return the bundle's notes, or ``None`` when nothing is user-facing."""
    problems = report_problems(report, release)
    if problems:
        raise ValueError("; ".join(problems))
    if report.status is ReportStatus.NO_USER_FACING_CHANGES:
        return None
    return ReleaseNotes(
        tag=release.tag,
        previous_tag=release.previous_tag,
        release_url=release.release_url,
        release_date=release.release_date,
        product=product,
        items=report.items,
        breaking=report.breaking,
    )


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("release_prs", type=Path)
    parser.add_argument("--product-name", required=True)
    parser.add_argument("--product-label", required=True)
    parser.add_argument("--product-tag", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, help="append has_entry here")
    args = parser.parse_args(argv)

    report = ChangelogReport.model_validate_json(
        args.report.read_text(encoding="utf-8")
    )
    release = ReleasePrs.model_validate_json(
        args.release_prs.read_text(encoding="utf-8")
    )
    product = Product(
        name=args.product_name, label=args.product_label, tag=args.product_tag
    )
    try:
        notes = build_notes(report, release, product)
    except ValueError as exc:
        logger.error("The report is rejected: %s", exc)
        return 1
    if notes is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(notes.model_dump_json(indent=2), encoding="utf-8")
    logger.info("%d item(s) built", len(notes.items) if notes else 0)
    append_outputs(args.outputs, has_entry=notes is not None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
