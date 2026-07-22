#!/usr/bin/env python3
"""Classify an OCI registry reference as existing or absent via ORAS.

Only the OCI Distribution Specification's ``MANIFEST_UNKNOWN`` and
``NAME_UNKNOWN`` responses mean that a reference is absent. ORAS 1.3.1 also
renders a registry's absent-manifest response as ``failed to find ...: not
found``; that exact form is accepted. Authentication, authorization, rate-limit,
transport, and other registry failures are errors; publish workflows must stop
instead of treating them as permission to overwrite an immutable tag.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from typing import Literal

RegistryState = Literal["absent", "exists"]

ABSENT_ERROR = re.compile(
    r"(?:\bMANIFEST_UNKNOWN\b|\bNAME_UNKNOWN\b|\bmanifest unknown\b|\bname unknown\b|failed to find .+: not found\s*$)",
    re.IGNORECASE,
)


def registry_ref_state(reference: str, *, oras: str = "oras") -> RegistryState:
    """Return the registry state, raising on anything except known absence."""
    try:
        result = subprocess.run(
            [oras, "manifest", "fetch", "--descriptor", reference],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(f"could not execute {oras!r}: {exc}") from exc

    if result.returncode == 0:
        return "exists"

    detail = "\n".join(
        part.strip() for part in (result.stderr, result.stdout) if part.strip()
    )
    if ABSENT_ERROR.search(detail):
        return "absent"

    message = detail or f"ORAS exited with status {result.returncode} and no output"
    raise RuntimeError(f"registry probe failed for {reference}: {message}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "reference", help="OCI reference in registry/repository:tag form"
    )
    parser.add_argument(
        "--oras", default="oras", help="ORAS executable (default: oras)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        state = registry_ref_state(args.reference, oras=args.oras)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
