"""Observe the real peer's receipt of an ACP initialize request."""

from __future__ import annotations

import asyncio
import logging

from band.integrations.acp.stderr import STDERR_LINE_LOG_TEMPLATE
from tests.integrations.acp.acp_toolkit.peer import INITIALIZE_PENDING_LINE


class InitializationReceipt(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.received = asyncio.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == STDERR_LINE_LOG_TEMPLATE % INITIALIZE_PENDING_LINE:
            self.received.set()
