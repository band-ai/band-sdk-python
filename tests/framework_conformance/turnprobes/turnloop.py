"""Who runs a probed adapter's tool loop."""

from __future__ import annotations

from enum import StrEnum


class TurnLoop(StrEnum):
    """Who runs the model's tool loop, and so how a decline ends the turn.

    The SDK stops its own loop after the decline's round. A provider runs its
    loop past the decline, so the SDK refuses every later tool call instead.
    """

    SDK = "sdk"
    PROVIDER = "provider"
