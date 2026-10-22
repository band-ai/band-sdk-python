from __future__ import annotations

from typing import Any, TypeAlias

NestedValue: TypeAlias = int | list["NestedValue"] | dict[str, "NestedValue"]


def nested(levels: Any, *, objects: bool = False, empty: bool = False) -> Any:
    value: NestedValue = ({} if objects else []) if empty else 0
    for _ in range(levels - int(empty)):
        value = {"x": value} if objects else [value]
    return value
