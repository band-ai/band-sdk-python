"""Field types the Parlant custom tool tests share."""

from __future__ import annotations

import enum

from typing_extensions import TypeAliasType


class Shade(enum.Enum):
    LIGHT = "light"
    DARK = "dark"


class Grade(enum.IntEnum):
    ECONOMY = 1
    PREMIUM = 2


class Color(enum.Enum):
    RED = 1
    BLUE = 2


# An optional list behind an alias, which pydantic writes into $defs.
MaybeTrays = TypeAliasType("MaybeTrays", list[int] | None)
