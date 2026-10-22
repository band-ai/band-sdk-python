"""Retain the complete platform source without interpreting or rewriting its YAML."""

from __future__ import annotations

from pathlib import Path

from remote_agent.band.publication import immutable_bytes
from remote_agent.storage import canonical
from remote_agent.types import JsonObject


def materialize_source(
    workspace: Path, source: JsonObject, *, conflict: str
) -> tuple[Path, str]:
    format = source.get("format", "frontmatter")
    if format == "frontmatter":
        # Preserve the exact path, bytes and host format of retained workspaces.
        path, encoded = workspace / "contract.band", source["content"].encode("utf-8")
    elif format == "standard":
        path, encoded = workspace / "contract-source.json", canonical(source) + b"\n"
        format = "file-source"
    else:
        raise ValueError("contract_source_format")
    immutable_bytes(path, encoded, conflict=conflict)
    return path, format
