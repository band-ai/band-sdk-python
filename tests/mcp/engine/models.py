"""MCP engine models support."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EchoInput(BaseModel):
    """Echo a message back."""

    message: str = Field(..., description="Message to echo")


class BootstrapInput(BaseModel):
    """Add a preset participant as part of custom session bootstrap."""

    identifier: str = Field(..., description="Participant identifier to add")
