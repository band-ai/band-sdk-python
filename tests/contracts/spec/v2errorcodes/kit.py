"""Shared data and helpers for specification checks."""

from __future__ import annotations

import re
from typing import Any

import jsonschema

from tests.paths import CONTRACT_SPEC_ROOT as SPEC

GUIDE = (SPEC / "implementation-guide.md").read_text()


def codes(text: str) -> set[str]:
    return set(re.findall(r"`([a-z][a-z0-9_]*)`", text))


def table_row(boundary: str) -> set[str]:
    rows = [
        line for line in GUIDE.splitlines() if line.startswith("| " + boundary + " |")
    ]
    assert len(rows) == 1, boundary
    return codes(rows[0].split(" | ")[1])


def validator(root: dict[str, Any], name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({**root, "$ref": "#/$defs/" + name})


def enum(root: dict[str, Any], name: str, *path: str) -> set[str]:
    node = root["$defs"][name]
    for key in path:
        node = node[key]
    return set(node["enum"])


TIME = {"invalid_time", "clock_regression"}
