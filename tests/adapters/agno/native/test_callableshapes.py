"""Released Agno execution preserves native callable shapes and their resources."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from operator import attrgetter
from typing import Any

import pytest
from agno.tools.function import Function

from band.integrations.agno.entrypoints import observe_function
from band.runtime.tools.agent import AgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.agno.native.bodies import BODY_FAILURE, NativeBody
from tests.adapters.agno.native.kit import NATIVE_NAME, NATIVE_RESULT, run_model_call


class TestNativeCallableShapes:
    @pytest.mark.parametrize(
        ("entrypoint", "has_shape"),
        [
            (attrgetter("coroutine"), inspect.iscoroutinefunction),
            (attrgetter("generator"), inspect.isgeneratorfunction),
            (attrgetter("async_generator"), inspect.isasyncgenfunction),
        ],
        ids=["coroutine", "generator", "async-generator"],
    )
    async def test_consumed_body_preserves_shape_and_releases_its_effect_authority(
        self,
        scoped_tools: AgentTools,
        native_body: NativeBody,
        native_recorder: ActivityRecorder,
        entrypoint: Callable[[NativeBody], Callable[..., Any]],
        has_shape: Callable[[Any], bool],
    ) -> None:
        borrowed = Function.from_callable(entrypoint(native_body), name=NATIVE_NAME)
        schema = borrowed.to_dict()
        observed = observe_function(borrowed)
        assert observed.entrypoint is not None and borrowed.entrypoint is not None
        assert has_shape(observed.entrypoint)
        assert inspect.signature(observed.entrypoint) == inspect.signature(
            borrowed.entrypoint
        )
        native_body.release.set()

        call, results = await run_model_call(scoped_tools, observed)

        assert call.error is None
        assert results[-1].content == NATIVE_RESULT
        assert native_body.finished.is_set()
        native_recorder.assert_completed_tool(NATIVE_NAME)
        with pytest.raises(asyncio.CancelledError):
            await native_body.tools.send_notice("stale", mentions=["@Alice"])
        assert borrowed.to_dict() == schema

    @pytest.mark.parametrize(
        "entrypoint",
        [
            attrgetter("failing_function"),
            attrgetter("failing_coroutine"),
            attrgetter("failing_generator"),
            attrgetter("failing_async_generator"),
        ],
        ids=["function", "coroutine", "generator", "async-generator"],
    )
    async def test_body_failure_reaches_agno_and_still_releases_its_resource(
        self,
        scoped_tools: AgentTools,
        native_body: NativeBody,
        native_recorder: ActivityRecorder,
        entrypoint: Callable[[NativeBody], Callable[..., Any]],
    ) -> None:
        function = observe_function(
            Function.from_callable(entrypoint(native_body), name=NATIVE_NAME)
        )

        call, results = await run_model_call(scoped_tools, function)

        assert call.error == BODY_FAILURE
        assert results[-1].tool_call_error is True
        assert native_body.finished.is_set()
        native_recorder.assert_completed_tool(NATIVE_NAME)
        with pytest.raises(asyncio.CancelledError):
            await native_body.tools.send_notice("stale", mentions=["@Alice"])
