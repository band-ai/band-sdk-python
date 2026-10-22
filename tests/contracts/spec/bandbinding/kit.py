"""Shared data and helpers for specification checks."""

from __future__ import annotations

import json
from typing import Any

from contracts.spec.tools import checkbandbinding as binding


def fixture() -> dict[str, Any]:
    return json.loads(binding.PATH.read_text())
