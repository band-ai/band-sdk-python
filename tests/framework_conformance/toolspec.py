"""Input shapes for native callable signature checks."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SampleInput(BaseModel):
    """Sample tool."""

    query: str
    tags: list[str] = Field(default_factory=list)
    limit: int = 5
    note: str | None = None


class OptionalBeforeRequired(BaseModel):
    """Optional field before a required field."""

    opt: int = 0
    req: str


class ContextField(BaseModel):
    """Tool with a context-named argument."""

    ctx: str


def echo(args: SampleInput) -> str:
    return f"q={args.query} tags={args.tags} limit={args.limit} note={args.note}"
