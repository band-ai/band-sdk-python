"""Unit tests for scripts/stamp-kit-spec.py (the release-time image pinner).

At publish time the pipeline rewrites the kit spec's ``sandbox.image`` to the
just-pushed image, pinned by digest, and pushes the result as the kit OCI
artifact. These tests pin that helper's contract: the stamp is byte-preserving
outside the one image line, malformed digests can never reach the registry,
ambiguous specs are refused rather than mis-stamped, and the CLI surface the
workflow invokes stays stable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.loaders import load_script_module
from tests.paths import KIT_DIR

stamp = load_script_module("scripts/stamp-kit-spec.py", "stamp_kit_spec")

SPEC_PATH = KIT_DIR / "spec.yaml"
IMAGE_REF = "ghcr.io/band-ai/band-python-kit/image:1.2.0"
DIGEST = "sha256:" + "a" * 64


def sole_changed_line(before: str, after: str) -> tuple[str, str]:
    """Assert exactly one line differs between two texts; return (old, new)."""
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines), "line count changed"
    diffs = [(o, n) for o, n in zip(before_lines, after_lines) if o != n]
    assert len(diffs) == 1, f"expected exactly one changed line, got {len(diffs)}"
    return diffs[0]


def test_stamp_pins_sandbox_image_by_digest() -> None:
    stamped = stamp.stamp_spec_file(SPEC_PATH, IMAGE_REF, DIGEST)
    assert yaml.safe_load(stamped)["sandbox"]["image"] == f"{IMAGE_REF}@{DIGEST}"


def test_stamp_keeps_every_non_image_field_byte_identical() -> None:
    original = SPEC_PATH.read_text(encoding="utf-8")
    stamped = stamp.stamp_spec_text(original, IMAGE_REF, DIGEST)

    old_line, new_line = sole_changed_line(original, stamped)
    assert old_line.strip().startswith("image:")
    assert new_line.strip() == f"image: {IMAGE_REF}@{DIGEST}"

    # And the stamped output still parses to the original spec in every field
    # except the image (guards the stamp format itself staying valid YAML).
    original_spec = yaml.safe_load(original)
    stamped_spec = yaml.safe_load(stamped)
    original_spec["sandbox"]["image"] = stamped_spec["sandbox"]["image"]
    assert original_spec == stamped_spec


@pytest.mark.parametrize(
    "bad_digest",
    [
        "deadbeef",  # no algorithm prefix
        "sha256:deadbeef",  # too short
        "sha256:" + "a" * 63,  # off by one
        "sha256:" + "a" * 65,  # off by one the other way
        "sha256:" + "A" * 64,  # uppercase hex
        "sha256:" + "g" * 64,  # non-hex character
        "sha512:" + "a" * 64,  # unsupported algorithm
        "",
    ],
)
def test_stamp_rejects_malformed_digests(bad_digest: str) -> None:
    with pytest.raises(ValueError):
        stamp.validate_digest(bad_digest)
    with pytest.raises(ValueError):
        stamp.stamp_spec_file(SPEC_PATH, IMAGE_REF, bad_digest)


def test_stamp_cli_matches_the_publish_workflow_invocation(tmp_path: Path) -> None:
    """kit-publish.yml runs the script through main() with these exact flags;
    the library-level tests can't catch a renamed flag or broken --output
    writing, so pin the CLI contract the release actually uses."""
    output = tmp_path / "spec.yaml"
    exit_code = stamp.main(
        [
            "--spec",
            str(SPEC_PATH),
            "--image-ref",
            IMAGE_REF,
            "--digest",
            DIGEST,
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == stamp.stamp_spec_file(
        SPEC_PATH, IMAGE_REF, DIGEST
    )


def test_stamp_rejects_a_spec_without_a_sandbox_image() -> None:
    with pytest.raises(ValueError):
        stamp.stamp_spec_text("kind: sandbox\nname: x\n", IMAGE_REF, DIGEST)


def test_stamp_refuses_a_spec_with_ambiguous_image_lines() -> None:
    # If the spec ever grows a second `image:` line, the byte-preserving edit
    # must refuse rather than silently rewrite the wrong one — a mis-stamp here
    # could, e.g., corrupt the network allowlist instead of the image ref.
    ambiguous = (
        "kind: sandbox\n"
        "sandbox:\n"
        "  image: band-python-kit:local\n"
        "somethingElse:\n"
        "  image: unrelated:tag\n"
    )
    with pytest.raises(ValueError, match="exactly one"):
        stamp.stamp_spec_text(ambiguous, IMAGE_REF, DIGEST)
