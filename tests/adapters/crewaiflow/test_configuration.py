from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from pydantic import BaseModel

from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    CrewAIFlowAdapterConfig,
    HistoryCrewAIFlowStateSource,
    RestCrewAIFlowStateSource,
)
from tests.adapters.crewaiflow.kit import empty_flow


class TestInit:
    def test_init_defaults(self) -> None:
        adapter = CrewAIFlowAdapter(flow_factory=empty_flow)
        assert isinstance(adapter._state_source, RestCrewAIFlowStateSource)
        assert adapter.metadata_namespace == ""

    def test_default_converter_honors_config_max_run_age(self) -> None:
        adapter = CrewAIFlowAdapter(
            CrewAIFlowAdapterConfig(max_run_age=timedelta(hours=2)),
            flow_factory=empty_flow,
        )
        assert adapter.history_converter.max_run_age == timedelta(hours=2)

    def test_init_does_not_call_flow_factory(self) -> None:
        called = []

        def factory():
            called.append(1)
            return MagicMock()

        CrewAIFlowAdapter(flow_factory=factory)
        assert called == []

    def test_init_accepts_history_state_source(self) -> None:
        adapter = CrewAIFlowAdapter(
            flow_factory=empty_flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        assert isinstance(adapter._state_source, HistoryCrewAIFlowStateSource)

    def test_init_accepts_additional_tools(self) -> None:
        class EmailsInput(BaseModel):
            pass

        def emails() -> str:
            return "inbox"

        adapter = CrewAIFlowAdapter(
            flow_factory=empty_flow,
            additional_tools=[(EmailsInput, emails)],
        )

        assert list(adapter._custom_tools) == ["emails"]
