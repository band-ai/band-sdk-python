"""Minimal real adapter for contact wiring tests."""

from __future__ import annotations

from typing import Any

from band.core.simple_adapter import SimpleAdapter


class ContactAdapter(SimpleAdapter[Any]):
    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        return None
