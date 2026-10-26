"""Define custom host-tool input and result shapes."""

from __future__ import annotations

from pydantic import BaseModel


class EchoInput(BaseModel):
    """Echo the supplied text."""

    text: str


class EchoDetail(BaseModel):
    text: str
    payload: bytes


class EchoResult(BaseModel):
    detail: EchoDetail
