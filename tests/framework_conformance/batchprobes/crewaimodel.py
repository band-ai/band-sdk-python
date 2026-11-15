"""A scripted model for a real CrewAI agent; imports crewai, so only the crewai
probe loads it."""

from __future__ import annotations

from typing import Any

from crewai.llms.base_llm import BaseLLM

from tests.framework_conformance.batchprobes.toolloop import flattened


class RecordingModel(BaseLLM):
    """Ends every call with ``final_text`` and keeps what the crew sent it."""

    def __init__(self, final_text: str) -> None:
        super().__init__(model="batch-probe")
        self.final_text = final_text
        self.received: list[dict[str, Any]] = []

    @property
    def provider_input(self) -> str:
        return flattened(self.received)

    def supports_function_calling(self) -> bool:
        return True

    def call(self, messages: list[dict[str, Any]], *_args: Any, **_kwargs: Any) -> str:
        self.received.extend(messages)
        return self.final_text

    async def acall(
        self, messages: list[dict[str, Any]], *args: Any, **kwargs: Any
    ) -> str:
        return self.call(messages, *args, **kwargs)
