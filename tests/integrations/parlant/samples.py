"""Field types the Parlant custom tool tests share."""

from __future__ import annotations

import enum

from typing_extensions import TypeAliasType


class Shade(enum.Enum):
    LIGHT = "light"
    DARK = "dark"


# An optional list behind an alias, which pydantic writes into $defs.
MaybeTrays = TypeAliasType("MaybeTrays", list[int] | None)
