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

Handler = Callable[[BaseModel], str | Awaitable[str]]


def async_handler(read: Handler, values: dict[str, dict[str, object]]) -> Handler:
    return read


def awaitable_handler(read: Handler, values: dict[str, dict[str, object]]) -> Handler:
    def handler(args: BaseModel) -> str | Awaitable[str]:
        return read(args)

    return handler


def sync_handler(read: Handler, values: dict[str, dict[str, object]]) -> Handler:
    def handler(args: BaseModel) -> str:
        return json.dumps(values["todo"])

    return handler


class TestNativeResourceTools:
    @pytest.mark.parametrize(
        "make_handler",
        [async_handler, awaitable_handler, sync_handler],
        ids=["async", "awaitable", "sync"],
    )
    async def test_framework_dispatch_executes_the_resource_handler(
        self, make_handler: Callable[..., Handler]
    ) -> None:
        async with shared_api() as (client, base_url, values):
            model, read = EXAMPLE["shared_tools"](client, base_url)[0]
            native = ToolSpec(model, make_handler(read, values)).as_callable()
            # Agno dispatches by this shape rather than awaiting arbitrary results.
            assert inspect.iscoroutinefunction(native)
            result = await native(todo_id=1)
            assert json.loads(result) == values["todo"]
            assert not client.is_closed
