"""Configure an ACP client to drive the real stdio peer."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from band.integrations.acp.client_adapter import ACPClientAdapterConfig
from tests.integrations.acp.stdiopeer import (
    CRASH_MARKER_ARG,
    HOLDER_RELEASE_ARG,
    PEER_EXIT_CODE,
    PEER_MODULE,
    ExitStage,
)


def stdio_peer_config(
    stage: ExitStage,
    lines: list[str],
    *,
    crash_marker: Path | None = None,
    holder_release: Path | None = None,
    **settings: Any,
) -> ACPClientAdapterConfig:
    """``holder_release`` makes the peer leave a descendant holding its pipes
    until that file exists."""
    # Windows' venv redirector keeps stdout open until the peer exits.
    command = [
        sys._base_executable,
        "-m",
        PEER_MODULE,
        stage,
        str(PEER_EXIT_CODE),
        *lines,
    ]
    if crash_marker is not None:
        command.extend((CRASH_MARKER_ARG, str(crash_marker)))
    if holder_release is not None:
        command.extend((HOLDER_RELEASE_ARG, str(holder_release)))
    return ACPClientAdapterConfig(
        command=command,
        env={"__PYVENV_LAUNCHER__": sys.executable},
        inject_band_tools=crash_marker is not None,
        **settings,
    )
