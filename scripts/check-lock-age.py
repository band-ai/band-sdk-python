#!/usr/bin/env python3
"""Supply-chain quarantine gate: fail if any locked package is too young.

The kit publish pipeline refuses to bake a dependency into the released image
until it has been public for a minimum age (default 7 days) — a
freshly-poisoned package is usually detected and yanked within days, so the
age window keeps a just-published compromise out of customer images.

The check reads the PEP 700 ``upload-time`` that uv records in ``uv.lock``
for every registry artifact. No resolution and no index access happen — the
committed lock is the single input, so the result is deterministic for a
given lock and cutoff. (An in-build ``uv sync --locked --exclude-newer``
gate does NOT work: the cutoff makes uv treat the committed lock itself as
outdated and every gated build fails — proven live on uv 0.9.13 and 0.11.19;
see astral-sh/uv#18775 for the underlying option-tracking behavior.)

Packages Band publishes itself (``FIRST_PARTY``) are exempt from the age
window: the gate models *upstream* compromise — a poisoned third-party
release that the ecosystem detects and yanks within days — and a release we
made carries no such signal. Ageing our own artifacts would only deadlock
our own pipeline for a week after every first-party dependency bump.
Exemptions are printed on every run so they stay visible in the gate log.

Exit codes: 0 = every artifact is old enough; 1 = violations (each listed on
stderr); 2 = bad usage / unreadable lock. Artifacts without an upload-time
are violations too — an undatable source must not slip the gate silently.

Usage:
    scripts/check-lock-age.py --lock uv.lock --max-age-days 7
    scripts/check-lock-age.py --lock uv.lock --cutoff 2026-01-01T00:00:00Z
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


# Band-published PyPI packages (PEP 503 canonical names). Ownership, not
# trust-by-name-pattern: extend only for packages this org actually releases.
FIRST_PARTY = frozenset(
    {
        "band-client-rest",
        "band-testing-python",
        "phoenix-channels-python-client",
    }
)


@dataclass(frozen=True)
class Violation:
    package: str
    version: str
    detail: str


def canonical(name: str) -> str:
    """PEP 503 canonical form, so lock spellings always match FIRST_PARTY."""
    return name.strip().lower().replace("_", "-").replace(".", "-")


def parse_upload_time(value: object) -> datetime | None:
    """uv writes upload-time as an RFC 3339 string; tolerate parsed datetimes."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def find_violations(
    lock_text: str,
    cutoff: datetime,
    first_party: frozenset[str] = FIRST_PARTY,
) -> list[Violation]:
    """Every registry artifact in the lock must predate ``cutoff``.

    Packages without any sdist/wheel entries (the project itself, path
    sources) carry no artifacts to date and are skipped, as are the
    ``first_party`` packages we publish ourselves; an artifact entry
    *missing* its upload-time is a violation, not a skip.
    """
    lock = tomllib.loads(lock_text)
    violations: list[Violation] = []
    for package in lock.get("package", []):
        name = package.get("name", "<unnamed>")
        if canonical(name) in first_party:
            continue
        version = str(package.get("version", "?"))
        artifacts = list(package.get("wheels", []))
        if sdist := package.get("sdist"):
            artifacts.append(sdist)
        for artifact in artifacts:
            uploaded = parse_upload_time(artifact.get("upload-time"))
            if uploaded is None:
                violations.append(
                    Violation(name, version, "artifact has no upload-time recorded")
                )
            elif uploaded > cutoff:
                violations.append(
                    Violation(
                        name,
                        version,
                        f"published {uploaded.isoformat()}, after the quarantine "
                        f"cutoff {cutoff.isoformat()}",
                    )
                )
    return violations


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path, help="Path to uv.lock")
    window = parser.add_mutually_exclusive_group()
    window.add_argument(
        "--max-age-days",
        type=int,
        default=7,
        help="Minimum public age of every locked artifact (default: 7)",
    )
    window.add_argument(
        "--cutoff",
        type=str,
        default=None,
        help="Explicit RFC 3339 cutoff instead of now minus --max-age-days",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.cutoff is not None:
        cutoff = datetime.fromisoformat(args.cutoff.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
    else:
        cutoff = datetime.now(tz=UTC) - timedelta(days=args.max_age_days)

    sys.stderr.write(
        f"first-party exemptions (Band-published): {', '.join(sorted(FIRST_PARTY))}\n"
    )
    violations = find_violations(args.lock.read_text(encoding="utf-8"), cutoff)
    if violations:
        for v in violations:
            sys.stderr.write(f"QUARANTINE: {v.package}=={v.version}: {v.detail}\n")
        sys.stderr.write(
            f"{len(violations)} artifact(s) violate the quarantine cutoff "
            f"{cutoff.isoformat()} — wait for them to age and re-run, or see "
            "docker/band_python_kit/RELEASING.md for the override procedure.\n"
        )
        return 1
    sys.stderr.write(f"quarantine gate: every artifact predates {cutoff.isoformat()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
