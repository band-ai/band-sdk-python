"""Unicode 16.0.0 extended graphemes (UAX #29 rev 45), matching the room engine."""

from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from remote_agent.types import JsonObject


@lru_cache(maxsize=1)
def table_data() -> JsonObject:
    return json.loads(Path(__file__).with_name("unicode16.json").read_text())


@lru_cache(maxsize=1)
def tables() -> dict[str, tuple[tuple[int, ...], list[list[Any]]]]:
    return {
        key: (tuple(row[0] for row in rows), rows)
        for key in ("gcb", "incb", "ep")
        for rows in [table_data()[key]]
    }


def lookup(key: str, cp: int, default: str | bool) -> str | bool:
    starts, ranges = tables()[key]
    index = bisect_right(starts, cp) - 1
    return ranges[index][2] if index >= 0 and cp <= ranges[index][1] else default


def boundary_flags(text: str) -> Iterator[bool]:
    previous, ri, ep_suffix, zwj_ep, indic = None, 0, False, False, 0
    for scalar in text:
        cp = ord(scalar)
        current = lookup("gcb", cp, "Other")
        conjunct = lookup("incb", cp, "None")
        pictograph = lookup("ep", cp, False)
        if previous is None:
            boundary = True
        elif previous == "CR" and current == "LF":
            boundary = False
        elif previous in ("Control", "CR", "LF") or current in ("Control", "CR", "LF"):
            boundary = True
        elif (
            previous == "L"
            and current in ("L", "V", "LV", "LVT")
            or previous in ("LV", "V")
            and current in ("V", "T")
            or previous in ("LVT", "T")
            and current == "T"
            or current in ("Extend", "ZWJ", "SpacingMark")
            or previous == "Prepend"
            or conjunct == "Consonant"
            and indic == 2
            or pictograph
            and zwj_ep
            or previous == current == "Regional_Indicator"
            and ri % 2 == 1
        ):
            boundary = False
        else:
            boundary = True
        next_indic = (
            1
            if conjunct == "Consonant"
            else indic
            if conjunct == "Extend"
            else 2
            if conjunct == "Linker" and indic > 0
            else 0
        )
        previous, ri, ep_suffix, zwj_ep, indic = (
            current,
            ri + 1 if current == "Regional_Indicator" else 0,
            pictograph or (current == "Extend" and ep_suffix),
            current == "ZWJ" and ep_suffix,
            next_indic,
        )
        yield boundary


def count(text: str) -> int:
    return sum(boundary_flags(text))


def boundaries(text: str) -> list[int]:
    return [
        index for index, boundary in enumerate(boundary_flags(text)) if boundary
    ] + [len(text)]
