"""NOOA (NVIDIA-labs Object Oriented Agents) adapter package.

Public import path: ``from band.adapters.nooa import NooaAdapter,
NooaAdapterConfig, NooaRoomAgent``. ``agent`` defines the room agent base class
the model sees, ``tools`` the ``self.room`` tool surface, and ``adapter`` the
configuration and room/turn lifecycle.
"""

from __future__ import annotations

try:
    import nooa  # noqa: F401 -- the one import guard for every module in this package
except ModuleNotFoundError as error:
    # ModuleNotFoundError keeps the missing module's name, so a venv without the
    # extra can still tell "absent optional dependency" from a broken import.
    raise ModuleNotFoundError(
        "NOOA dependencies not installed (NOOA supports Python 3.12 and 3.13 on "
        "Linux and macOS). Install with: uv add band-sdk[nooa]",
        name=error.name,
    ) from error

from band.adapters.nooa.adapter import NooaAdapter, NooaAdapterConfig
from band.adapters.nooa.agent import NooaRoomAgent
from band.adapters.nooa.tools import RoomTools

__all__ = [
    "NooaAdapter",
    "NooaAdapterConfig",
    "NooaRoomAgent",
    "RoomTools",
]
