from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import BaseModel

from band.core.protocols import AgentToolsProtocol
from band.runtime.custom_tools import CustomToolDef, get_custom_tool_name

L1_CUSTOM_PROMPT_MARKER = "SNOLLYGOSTER"
L1_CUSTOM_RETURN_MARKER = "FLIBBERTIGIBBET"


class LogKeywordInput(BaseModel):
    """Log a keyword marker and return the validation keyword."""

    __band_tool_name__: ClassVar[str] = "log_keyword"
    message: str


L1_CUSTOM_TOOL_NAME = get_custom_tool_name(LogKeywordInput)


def make_l1_custom_tool_def(
    handler: Callable[[LogKeywordInput], Any],
) -> CustomToolDef:
    return (LogKeywordInput, handler)


def make_l1_langgraph_structured_tool(
    handler: Callable[[LogKeywordInput], Any],
) -> Any:
    from langchain_core.tools import StructuredTool

    async def log_keyword(message: str) -> Any:
        return await handler(LogKeywordInput(message=message))

    return StructuredTool.from_function(
        coroutine=log_keyword,
        name=L1_CUSTOM_TOOL_NAME,
        description=LogKeywordInput.__doc__ or "",
        args_schema=LogKeywordInput,
    )


def make_l1_pydantic_ai_tool(
    handler: Callable[[LogKeywordInput], Any],
) -> Callable[..., Any]:
    from pydantic_ai import RunContext

    globals()["RunContext"] = RunContext

    async def log_keyword(ctx: RunContext[AgentToolsProtocol], message: str) -> Any:
        del ctx
        return await handler(LogKeywordInput(message=message))

    return log_keyword
