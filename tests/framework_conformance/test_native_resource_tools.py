"""Native tool schemas retain the async shape required by framework dispatch."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable

import pytest
from pydantic import BaseModel

from tests.e2e.baseline.toolkit.tools import ToolSpec
from tests.example_agents.anthropic.kit import EXAMPLE
from tests.example_agents.anthropic.peer import shared_api


class TestNativeResourceTools:
    @pytest.mark.parametrize("style", ["async", "awaitable", "sync"])
    async def test_framework_dispatch_executes_the_resource_handler(
        self, style: str
    ) -> None:
        async with shared_api() as (client, base_url, values):
            model, read = EXAMPLE["shared_tools"](client, base_url)[0]
            handler: Callable[[BaseModel], str | Awaitable[str]]
            match style:
                case "async":
                    handler = read
                case "awaitable":
                    handler = lambda args: read(args)
                case "sync":
                    handler = lambda args: json.dumps(values["todo"])
                case _:
                    raise ValueError(f"Unknown handler style: {style}")
            native = ToolSpec(model, handler).as_callable()
            # Agno dispatches by this shape rather than awaiting arbitrary results.
            assert inspect.iscoroutinefunction(native)
            result = await native(todo_id=1)
            assert json.loads(result) == values["todo"]
            assert not client.is_closed
