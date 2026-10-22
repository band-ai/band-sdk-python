"""OS-table rows that run everywhere and skip only on the named platforms."""

from __future__ import annotations

import os

import pytest


def skip_unless_os(accepted_on: tuple[str, ...]) -> None:
    if os.name not in accepted_on:
        pytest.skip(f"this row runs on {', '.join(accepted_on)}")
