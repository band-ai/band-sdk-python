"""Shared data and helpers for specification checks."""

from __future__ import annotations

import zipfile
from pathlib import Path

from tests.paths import CONTRACTS_ROOT as ROOT


def reference_argv() -> list[str]:
    paths = sorted((ROOT / "contract-lab/_build/dev/lib").glob("*/ebin"))
    return (
        ["elixir", "--erl", "+S 4:4"]
        + [item for path in paths for item in ("-pa", str(path))]
        + [str(ROOT / "spec/tools/reference_adapter.exs")]
    )


def fake_wheel(directory: str | Path, name: str, version: str, *requires: str) -> None:
    dist = f"{name.replace('-', '_')}-{version}"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n" + "".join(
        f"Requires-Dist: {item}\n" for item in requires
    )
    with zipfile.ZipFile(Path(directory) / f"{dist}-py3-none-any.whl", "w") as archive:
        archive.writestr(f"{dist}.dist-info/METADATA", metadata)


RACE_CHECKS = {
    "contribution-id-conflict-changed-cause",
    "contribution-id-conflict-changed-producer",
    "contribution-conflict-keeps-state",
    "contribution-retry-keeps-state",
    "contribution-retry-original-receipt",
    "reply-wins-before-failure",
    "failure-wins-before-reply",
    "deadline-before-exact-report",
    "deadline-before-exact-reply",
    "stale-entry-rejected",
    "wrong-session-rejected",
    "unrelated-member-rejected",
    "settled-member-rejected",
    "scope-rejections-keep-state",
    "contribution-capacity",
    "capacity-rejection-keeps-state",
    "capacity-keeps-retries",
    "capacity-keeps-deadlines",
    "host-retry-keeps-state",
    "host-id-conflict",
    "host-stale-equal-sequence",
    "host-stale-old-sequence",
    "host-rejections-keep-state",
}
