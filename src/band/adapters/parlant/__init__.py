"""Parlant adapter package.

Public API: ``from band.adapters.parlant import ParlantAdapter,
ParlantAdapterConfig``.
"""

from __future__ import annotations

from band.adapters.parlant.adapter import ParlantAdapter
from band.adapters.parlant.config import ConfigureCallback, ParlantAdapterConfig

__all__ = ["ConfigureCallback", "ParlantAdapter", "ParlantAdapterConfig"]
