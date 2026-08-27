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
    from band.testing.features import feature_kwargs as feature_kwargs
    from band.testing.phoenix_server import (
        FakePhoenixServer as FakePhoenixServer,
        JoinOutcome as JoinOutcome,
        fake_phoenix_server as fake_phoenix_server,
    )
    from band.testing.platform import (
        platform_connection_stub as platform_connection_stub,
    )
    from band.testing.strands import (
        ErrorTurn as ErrorTurn,
        ScriptedStrandsModel as ScriptedStrandsModel,
        ScriptedTurn as ScriptedTurn,
        TextTurn as TextTurn,
        ToolTurn as ToolTurn,
    )
    from band.testing.transport import (
        force_transport_disconnect as force_transport_disconnect,
    )

__all__, __getattr__ = lazy_exports(
    __name__,
    fake_tools=["FakeAgentTools"],
    features=["feature_kwargs"],
    phoenix_server=["FakePhoenixServer", "JoinOutcome", "fake_phoenix_server"],
    platform=["platform_connection_stub"],
    strands=[
        "ErrorTurn",
        "ScriptedStrandsModel",
        "ScriptedTurn",
        "TextTurn",
        "ToolTurn",
    ],
    transport=["force_transport_disconnect"],
)
