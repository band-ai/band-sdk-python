"""Describe heterogeneous JSON objects at validated process and journal boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeAlias

JsonObject: TypeAlias = dict[str, Any]
SourcePath: TypeAlias = str | Path
