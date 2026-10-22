"""Exact current-profile source used by every installed-release drill."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def contract_path(release: Path) -> Path:
    return Path(release) / "contracts/v2/hybrid-assistant.band"


def file_source(path: Path, *, clock: str | None = None) -> dict[str, str]:
    content = Path(path).read_bytes().decode("utf-8")
    if clock is not None:
        if not re.fullmatch(r"[1-9][0-9]*(?:ms|s|m|h)", clock):
            raise ValueError("Invalid qualification clock")
        content, count = re.subn(
            r"(?m)^contract @[1-9][0-9]*(?:ms|s|m|h) => expired$",
            "contract @" + clock + " => expired",
            content,
        )
        if count != 1:
            raise ValueError(
                "Expected exactly one explicit qualification contract clock"
            )
    return {
        "profile": "band-contract/file-source/1",
        "format": "frontmatter",
        "content": content,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


def smoke_contracts(release: Path) -> list[tuple[Path, str]]:
    """Exercise both current-profile header adapters in the copied release."""
    front = contract_path(release)
    marker, header, body = front.read_text().split("---\n", 2)
    if marker:
        raise ValueError("Expected frontmatter qualification contract")
    fields = dict(line.split(": ", 1) for line in header.splitlines() if line)
    if fields["engine"] != "0.2.0-lab.1":
        raise ValueError("Qualification requires the current profile")
    package = Path(release) / "contracts" / fields["name"]
    package.mkdir(exist_ok=True)
    (package / "body.band").write_text(body)
    standard = package / "SKILL.md"
    standard.write_text(
        "---\nname: "
        + json.dumps(fields["name"])
        + "\ndescription: "
        + json.dumps(fields["description"])
        + '\nmetadata:\n  band-engine: "0.2.0-lab.1"\n'
        '  band-roles: "requester, assistant"\n'
        "  band-contract: body.band\n  band-contract-sha256: "
        + hashlib.sha256(body.encode()).hexdigest()
        + "\n---\n"
    )
    return [(front, "frontmatter"), (standard, "standard")]
