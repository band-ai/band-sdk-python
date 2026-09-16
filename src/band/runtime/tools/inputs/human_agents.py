"""Human-tool input models: agent registration and listing.

These models mirror band-mcp's human tool handler signatures field-for-field
(packages/band-mcp, same repo): the observable tool surface stays identical
to the MCP behavior it was modeled on. Widening to full Fern parity is out
of scope.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ListMyAgentsInput(BaseModel):
    """List agents owned by the user."""

    page: int | None = Field(None, description="Page number (optional).")
    page_size: int | None = Field(None, description="Items per page (optional).")


class RegisterMyAgentInput(BaseModel):
    """Register a new remote agent.

    Returns the agent details including API key. Save the API key - it's only shown once!
    """

    name: str = Field(..., description="Agent name (required).")
    description: str = Field(..., description="Agent description (required).")
