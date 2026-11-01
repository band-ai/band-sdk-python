"""Observe validated native entrypoints without changing callable shape."""

from __future__ import annotations

import copy
import functools
import inspect
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from agno.tools import Toolkit
from agno.tools.function import Function

from band.integrations.agno.context import current_activity


def observe_entrypoint(entrypoint: Callable[..., Any], name: str) -> Callable[..., Any]:
    shape = inspect.unwrap(entrypoint)
    if inspect.isasyncgenfunction(shape):

        @functools.wraps(entrypoint)
        async def async_generator(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            run = current_activity.get()
            if run is None:
                async for item in entrypoint(*args, **kwargs):
                    yield item
                return
            body = run.begin(name)
            iterator = entrypoint(*args, **kwargs)
            try:
                while True:
                    try:
                        with body.executing():
                            item = await anext(iterator)
                    except StopAsyncIteration:
                        return
                    yield item
            finally:
                try:
                    with body.executing(closing=True):
                        await iterator.aclose()
                finally:
                    run.end(body)

        return async_generator
    if inspect.isgeneratorfunction(shape):

        @functools.wraps(entrypoint)
        def generator(*args: Any, **kwargs: Any) -> Iterator[Any]:
            run = current_activity.get()
            if run is None:
                yield from entrypoint(*args, **kwargs)
                return
            body = run.begin(name)
            iterator = entrypoint(*args, **kwargs)
            try:
                while True:
                    try:
                        with body.executing():
                            item = next(iterator)
                    except StopIteration:
                        return
                    yield item
            finally:
                try:
                    with body.executing(closing=True):
                        iterator.close()
                finally:
                    run.end(body)

        return generator
    if inspect.iscoroutinefunction(shape):

        @functools.wraps(entrypoint)
        async def coroutine(*args: Any, **kwargs: Any) -> Any:
            run = current_activity.get()
            if run is None:
                return await entrypoint(*args, **kwargs)
            body = run.begin(name)
            try:
                with body.executing():
                    return await entrypoint(*args, **kwargs)
            finally:
                run.end(body)

        return coroutine

    @functools.wraps(entrypoint)
    def function(*args: Any, **kwargs: Any) -> Any:
        run = current_activity.get()
        if run is None:
            return entrypoint(*args, **kwargs)
        body = run.begin(name)
        try:
            with body.executing():
                return entrypoint(*args, **kwargs)
        finally:
            run.end(body)

    return function


def observe_function(function: Function) -> Function:
    owned = function.model_copy()
    owned.process_entrypoint()
    if owned.entrypoint is not None:
        owned.entrypoint = observe_entrypoint(owned.entrypoint, owned.name)
    return owned


def observe_tool(tool: Any) -> Any:
    if isinstance(tool, Function):
        return observe_function(tool)
    if isinstance(tool, Toolkit):
        owned = copy.copy(tool)
        owned.functions = {
            name: observe_function(function)
            for name, function in tool.get_functions().items()
        }
        return owned
    if callable(tool):
        return observe_function(Function.from_callable(tool))
    return tool
