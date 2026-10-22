"""Write machine-readable CLI records without adding logging prefixes."""

from __future__ import annotations

import sys
from typing import TextIO


def write_line(value: str, *, file: TextIO | None = None, flush: bool = False) -> None:
    stream = sys.stdout if file is None else file
    stream.write(value + "\n")
    if flush:
        stream.flush()
