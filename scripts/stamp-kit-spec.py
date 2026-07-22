#!/usr/bin/env python3
"""Stamp the kit spec's ``sandbox.image`` with a published, digest-pinned ref.

The repo copy of ``docker/band_python_kit/spec.yaml`` keeps a local image ref
(``band-python-kit:local``) so the local-development flow stays intact. At
release time the publish pipeline copies the spec into a staging directory and
rewrites ``sandbox.image`` to the just-pushed image, pinned by digest, before
``sbx kit push``. This helper performs that rewrite deterministically.

The image job passes the digest it observed, so the published kit is pinned to
exactly the image bytes it was released with — independent of whether
``sbx kit push`` self-pins (undocumented for the pinned CLI version).

Only the ``sandbox.image`` line is edited; every other byte of the spec is
preserved verbatim (comments, ordering, formatting) rather than round-tripped
through a YAML dump. Malformed digests are rejected so a broken ref can never
reach the registry.

Usage:
    scripts/stamp-kit-spec.py \\
        --spec docker/band_python_kit/spec.yaml \\
        --image-ref ghcr.io/band-ai/band-python-kit/image:1.2.0 \\
        --digest sha256:<64-hex> \\
        --output staging/spec.yaml

With no ``--output`` the stamped spec is written to stdout.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# An OCI digest is an algorithm-prefixed lowercase-hex string. We only ever
# stamp what a registry push returned, which is sha256; reject anything else
# (including uppercase hex or a wrong length) rather than pass a malformed ref
# to the published artifact.
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Matches exactly the indented ``image:`` mapping line (not a comment, not a
# substring of another key). The spec has a single such line under sandbox.
_IMAGE_LINE_RE = re.compile(r"(?m)^(?P<indent>[ \t]+)image:[ \t]*(?P<val>\S.*?)[ \t]*$")


def validate_digest(digest: str) -> None:
    """Raise ValueError if ``digest`` is not a well-formed sha256 OCI digest."""
    if not _DIGEST_RE.match(digest):
        raise ValueError(
            f"malformed digest {digest!r}: expected 'sha256:' followed by 64 "
            "lowercase hex characters"
        )


def stamp_spec_text(spec_text: str, image_ref: str, digest: str) -> str:
    """Return ``spec_text`` with ``sandbox.image`` rewritten to a pinned ref.

    ``image_ref`` is the base reference (typically ``name:tag``); the result
    pins it by digest as ``<image_ref>@<digest>``. Every non-image byte of the
    input is preserved. Raises ValueError on a malformed digest, a spec without
    a ``sandbox.image``, or an ambiguous number of ``image:`` lines.
    """
    validate_digest(digest)

    spec = yaml.safe_load(spec_text)
    if not isinstance(spec, dict):
        raise ValueError("spec did not parse to a mapping")
    current = spec.get("sandbox", {}).get("image") if spec.get("sandbox") else None
    if not current:
        raise ValueError("spec has no sandbox.image to stamp")

    pinned_ref = f"{image_ref}@{digest}"

    replaced: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        replaced.append(match.group("val"))
        return f"{match.group('indent')}image: {pinned_ref}"

    stamped = _IMAGE_LINE_RE.sub(_replace, spec_text)

    # Guard against a spec whose textual shape doesn't match the parsed one
    # (e.g. a flow-style mapping): the byte-preserving edit is only safe when a
    # single block-style image line carries the value we parsed.
    if len(replaced) != 1:
        raise ValueError(
            f"expected exactly one 'image:' line to stamp, found {len(replaced)}"
        )
    if replaced[0] != str(current):
        raise ValueError(
            "the 'image:' line found does not match the parsed sandbox.image "
            f"({replaced[0]!r} != {str(current)!r})"
        )

    return stamped


def stamp_spec_file(spec_path: Path, image_ref: str, digest: str) -> str:
    """Read ``spec_path`` and return its stamped text."""
    return stamp_spec_text(spec_path.read_text(encoding="utf-8"), image_ref, digest)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec", required=True, type=Path, help="Path to the source spec.yaml"
    )
    parser.add_argument(
        "--image-ref",
        required=True,
        help="Base image reference to pin (typically name:tag)",
    )
    parser.add_argument(
        "--digest", required=True, help="sha256 digest of the pushed image"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Where to write the stamped spec (default: stdout)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    stamped = stamp_spec_file(args.spec, args.image_ref, args.digest)
    if args.output is not None:
        args.output.write_text(stamped, encoding="utf-8")
    else:
        sys.stdout.write(stamped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
