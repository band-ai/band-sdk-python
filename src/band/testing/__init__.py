"""Testing utilities.

Framework-specific helpers are lazily imported, so importing this package never
requires an optional extra.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.exports import lazy_exports

# Type-only imports for static analysis (pyrefly, mypy, etc.)
if TYPE_CHECKING:
    from band.testing.fake_tools import FakeAgentTools as FakeAgentTools
    from band.testing.strands import (
        ScriptedStrandsModel as ScriptedStrandsModel,
        TextTurn as TextTurn,
        ToolTurn as ToolTurn,
    )

__all__, __getattr__ = lazy_exports(
    __name__,
    fake_tools=["FakeAgentTools"],
    strands=["ScriptedStrandsModel", "TextTurn", "ToolTurn"],
)
