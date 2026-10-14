"""Declared wheel-consumer combinations and their expected OpenAI compatibility."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Framework(StrEnum):
    PYDANTIC_AI = "pydantic-ai"
    CREWAI = "crewai"
    STRANDS = "strands"
    NOOA = "nooa"
    LANGGRAPH = "langgraph"
    PARLANT = "parlant"


@dataclass(frozen=True)
class Consumer:
    frameworks: tuple[Framework, ...]
    openai_major: int
    rejects_pydantic_ai: bool = False

    @property
    def extras(self) -> str:
        return ",".join(self.frameworks)


CONSUMERS: dict[str, Consumer] = {
    consumer.extras.replace(",", "-"): consumer
    for consumer in (
        Consumer((Framework.PYDANTIC_AI,), 3),
        Consumer((Framework.CREWAI,), 2, rejects_pydantic_ai=True),
        Consumer((Framework.STRANDS,), 2, rejects_pydantic_ai=True),
        Consumer((Framework.NOOA,), 2, rejects_pydantic_ai=True),
        Consumer((Framework.LANGGRAPH, Framework.STRANDS), 2, rejects_pydantic_ai=True),
        Consumer((Framework.LANGGRAPH,), 3),
        Consumer((Framework.PARLANT,), 3),
    )
}
