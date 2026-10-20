from __future__ import annotations

import pytest
from pydantic import BaseModel

from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
)
from band.core.exceptions import BandConfigError
from tests.adapters.crewaiflow.kit import empty_flow


class TestValidation:
    def test_flow_factory_must_be_callable(self) -> None:
        with pytest.raises(BandConfigError):
            CrewAIFlowAdapter(flow_factory="not callable")  # type: ignore[arg-type]

    def test_additional_tools_requires_pydantic_model(self) -> None:
        with pytest.raises(BandConfigError):
            CrewAIFlowAdapter(
                flow_factory=empty_flow,
                additional_tools=[(object, lambda: None)],  # type: ignore[list-item]
            )

    def test_additional_tools_rejects_duplicate_names(self) -> None:
        class EmailsInput(BaseModel):
            pass

        with pytest.raises(BandConfigError, match="Duplicate custom tool name"):
            CrewAIFlowAdapter(
                flow_factory=empty_flow,
                additional_tools=[
                    (EmailsInput, lambda: None),
                    (EmailsInput, lambda: None),
                ],
            )

    def test_state_source_must_have_load_task_events(self) -> None:
        with pytest.raises(BandConfigError):
            CrewAIFlowAdapter(
                flow_factory=empty_flow,
                state_source=object(),  # type: ignore[arg-type]
            )

    def test_state_source_load_task_events_must_be_awaitable(self) -> None:
        class SyncSource:
            def load_task_events(self, *, room_id, metadata_namespace, tools, history):
                return []

        with pytest.raises(BandConfigError):
            CrewAIFlowAdapter(
                flow_factory=empty_flow,
                state_source=SyncSource(),  # type: ignore[arg-type]
            )
