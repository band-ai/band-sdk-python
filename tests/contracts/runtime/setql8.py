from __future__ import annotations

from typing import Any, TypeAlias

NestedValue: TypeAlias = int | list["NestedValue"] | dict[str, "NestedValue"]


def nested(levels: Any) -> dict[str, Any]:
    value: NestedValue = 0
    for _ in range(levels):
        value = [value]
    return {"nested": value}
