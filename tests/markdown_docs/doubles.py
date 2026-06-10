from __future__ import annotations

from tests.markdown_docs.constants import MARKDOWN_AGENT_ID, MARKDOWN_API_KEY


class MarkdownAgentFactory:
    """Doc-test proxy that supplies placeholder credentials for Agent.create."""

    @staticmethod
    def create(**kwargs: object) -> object:
        from band import Agent

        kwargs.setdefault("agent_id", MARKDOWN_AGENT_ID)
        kwargs.setdefault("api_key", MARKDOWN_API_KEY)
        return Agent.create(**kwargs)

    @staticmethod
    def from_config(*args: object, **kwargs: object) -> object:
        from band import Agent

        return Agent.from_config(*args, **kwargs)


class AnyAdapter:
    """Generic adapter placeholder for universal migration snippets."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
