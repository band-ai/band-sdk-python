"""A2A adapter - re-exports from integrations module."""

from band.integrations.a2a.adapter import A2AAdapter
from band.integrations.a2a.types import A2AAdapterConfig, A2AAuth

__all__ = ["A2AAdapter", "A2AAdapterConfig", "A2AAuth"]
