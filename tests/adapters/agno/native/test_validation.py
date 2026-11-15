"""Rejected synchronous-generator arguments never admit a native execution body."""

from __future__ import annotations

from agno.tools.function import Function

from band.integrations.agno.entrypoints import observe_function
from band.runtime.tools.agent import AgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.agno.native.bodies import NativeBody
from tests.adapters.agno.native.kit import NATIVE_NAME, run_model_call


class TestNativeGeneratorValidation:
    async def test_invalid_arguments_return_an_error_without_starting_native_work(
        self,
        scoped_tools: AgentTools,
        native_body: NativeBody,
        native_recorder: ActivityRecorder,
    ) -> None:
        function = observe_function(
            Function.from_callable(native_body.counted_generator, name=NATIVE_NAME)
        )

        call, results = await run_model_call(
            scoped_tools, function, {"count": "invalid-integer"}
        )

        assert results[-1].tool_call_error is True
        assert call.error is not None
        assert not native_body.entered.is_set()
        assert native_recorder.events == []

    async def test_coerced_arguments_enter_and_complete_the_actual_body(
        self,
        scoped_tools: AgentTools,
        native_body: NativeBody,
        native_recorder: ActivityRecorder,
    ) -> None:
        function = observe_function(
            Function.from_callable(native_body.counted_generator, name=NATIVE_NAME)
        )

        call, results = await run_model_call(scoped_tools, function, {"count": "2"})

        assert call.error is None
        assert results[-1].content == "2"
        assert native_body.finished.is_set()
        native_recorder.assert_completed_tool(NATIVE_NAME)
