"""Kit for the codex-acp end-to-end tests: the input of the echo tool they
serve over MCP."""

from __future__ import annotations

from pydantic import BaseModel


class EchoInput(BaseModel):
    """Echo text back to the caller."""

    message: str
