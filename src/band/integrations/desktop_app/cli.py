"""Console entry for the Claude Desktop room view.

Kept apart from the server so the platform check runs before anything else
is imported: the relay under the server coordinates Desktop's MCP processes
through fcntl file locks and Unix sockets, so on Windows merely importing it
raises a bare ``ModuleNotFoundError`` that names neither the product nor the
reason.
"""

from __future__ import annotations

import sys


def entry_point() -> None:
    if sys.platform == "win32":
        raise SystemExit(
            "band-room-view runs with Claude Desktop on macOS only: it "
            "shares one Band connection between Desktop's MCP processes "
            "through fcntl file locks and Unix sockets, which Windows does "
            "not provide."
        )
    from band.integrations.desktop_app.server import entry_point  # noqa: PLC0415

    entry_point()
