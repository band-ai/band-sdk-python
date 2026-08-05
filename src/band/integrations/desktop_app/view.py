"""The live room view Claude Desktop mounts, assembled from its own files.

The MCP Apps sandbox runs the view under a CSP that blocks every external
origin, so the resource has to be one self-contained HTML document. Keeping
the markup, styles and script as real files anyway means they can be read,
diffed, linted and syntax-checked like the code they are; this module only
inlines them at read time.
"""

from __future__ import annotations

import hashlib
from functools import cache
from importlib.resources import files

ASSETS = "band.integrations.desktop_app.assets"


@cache
def room_view_html() -> str:
    """The room view as one inlined document."""
    assets = files(ASSETS)
    return (
        (assets / "room-view.html")
        .read_text(encoding="utf-8")
        .format(
            css=(assets / "room-view.css").read_text(encoding="utf-8"),
            js=(assets / "room-view.js").read_text(encoding="utf-8"),
        )
    )


@cache
def room_view_fingerprint() -> str:
    """A short digest of the assembled document.

    Claude Desktop caches the app resource by URI, so the URI must change
    whenever the document does. Deriving it from the content makes that
    automatic — there is no version counter to forget to bump.
    """
    return hashlib.sha256(room_view_html().encode()).hexdigest()[:12]
