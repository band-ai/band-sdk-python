"""Unit tests for CrewAI Flow state sources."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from thenvoi.adapters.crewai_flow import (
    HistoryCrewAIFlowStateSource,
    RestCrewAIFlowStateSource,
)
from thenvoi.core.exceptions import ThenvoiConfigError, ThenvoiToolError
from thenvoi.testing.fake_tools import FakeAgentTools

NS = "crewai_flow:agent-1"


def _task_event(
    *,
    id: str,
    inserted_at: datetime,
    namespace: str = NS,
    payload: dict | None = None,
) -> dict:
    return {
        "id": id,
        "message_type": "task",
        "inserted_at": inserted_at.isoformat(),
        "metadata": {namespace: payload or {"run_id": id}},
    }


# ---------------------------------------------------------------------------
# RestCrewAIFlowStateSource
# ---------------------------------------------------------------------------


class TestRestStateSourceFullFetch:
    @pytest.mark.asyncio
    async def test_filters_by_namespace_and_message_type(self) -> None:
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tools = FakeAgentTools(
            room_context=[
                _task_event(id="e1", inserted_at=t),
                {
                    "id": "e2",
                    "message_type": "text",
                    "inserted_at": t.isoformat(),
                    "metadata": {NS: {"x": 1}},
                },
                _task_event(
                    id="e3",
                    inserted_at=t + timedelta(seconds=1),
                    namespace="crewai_flow:other",
                ),
                _task_event(id="e4", inserted_at=t + timedelta(seconds=2)),
            ]
        )
        source = RestCrewAIFlowStateSource(page_size=10)
        events = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        assert [e["id"] for e in events] == ["e1", "e4"]

    @pytest.mark.asyncio
    async def test_paginates_until_short_page(self) -> None:
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tools = FakeAgentTools(
            room_context=[
                _task_event(id=f"e{i}", inserted_at=base + timedelta(seconds=i))
                for i in range(25)
            ]
        )
        source = RestCrewAIFlowStateSource(page_size=10)
        events = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        assert len(events) == 25
        # 3 pages: 10 + 10 + 5; the 3rd page is short so we stop.
        assert len(tools.context_calls) == 3


class TestRestStateSourceCache:
    @pytest.mark.asyncio
    async def test_cache_early_termination_on_second_turn(self) -> None:
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tools = FakeAgentTools(
            room_context=[
                _task_event(id=f"e{i}", inserted_at=base + timedelta(seconds=i))
                for i in range(5)
            ]
        )
        source = RestCrewAIFlowStateSource(page_size=10)
        first = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        first_calls = len(tools.context_calls)
        assert len(first) == 5

        # Append one new event; second turn should return all 6 with one extra
        # fetch (early termination on cached high-water mark).
        tools.append_room_context(
            _task_event(id="e5", inserted_at=base + timedelta(seconds=5))
        )
        second = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        assert [e["id"] for e in second] == ["e0", "e1", "e2", "e3", "e4", "e5"]
        # Incremental fetch should hit only one page (page_size > total).
        assert len(tools.context_calls) - first_calls == 1

    @pytest.mark.asyncio
    async def test_cache_refresh_scans_past_first_full_page(self) -> None:
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tools = FakeAgentTools(
            room_context=[
                _task_event(id=f"e{i}", inserted_at=base + timedelta(seconds=i))
                for i in range(100)
            ]
        )
        source = RestCrewAIFlowStateSource(page_size=50)
        first = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        first_calls = len(tools.context_calls)
        assert len(first) == 100

        tools.append_room_context(
            _task_event(id="e100", inserted_at=base + timedelta(seconds=100))
        )
        second = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )

        assert [e["id"] for e in second][-2:] == ["e99", "e100"]
        assert len(tools.context_calls) - first_calls == 3

    @pytest.mark.asyncio
    async def test_cache_refresh_keeps_same_timestamp_lower_id_event(self) -> None:
        inserted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tools = FakeAgentTools(
            room_context=[_task_event(id="z-reserve", inserted_at=inserted_at)]
        )
        source = RestCrewAIFlowStateSource(page_size=10)
        first = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        assert [e["id"] for e in first] == ["z-reserve"]

        tools.append_room_context(
            _task_event(id="a-finalized", inserted_at=inserted_at)
        )
        second = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )

        assert [e["id"] for e in second] == ["a-finalized", "z-reserve"]

    @pytest.mark.asyncio
    async def test_cache_refresh_keeps_distinct_events_without_ids(self) -> None:
        inserted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tools = FakeAgentTools(
            room_context=[
                {
                    "message_type": "task",
                    "inserted_at": inserted_at.isoformat(),
                    "metadata": {NS: {"run_id": "run-1"}},
                }
            ]
        )
        source = RestCrewAIFlowStateSource(page_size=10)
        first = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        assert [e["metadata"][NS]["run_id"] for e in first] == ["run-1"]

        tools.append_room_context(
            {
                "message_type": "task",
                "inserted_at": inserted_at.isoformat(),
                "metadata": {NS: {"run_id": "run-2"}},
            }
        )
        second = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        assert [e["metadata"][NS]["run_id"] for e in second] == ["run-1", "run-2"]

        third = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        assert [e["metadata"][NS]["run_id"] for e in third] == ["run-1", "run-2"]

    @pytest.mark.asyncio
    async def test_clear_room_invalidates_cached_high_water_mark(self) -> None:
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = t1 + timedelta(seconds=1)
        tools = FakeAgentTools(room_context=[_task_event(id="e2", inserted_at=t2)])
        source = RestCrewAIFlowStateSource(page_size=10)
        first = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        assert [e["id"] for e in first] == ["e2"]

        tools.set_room_context(
            [
                _task_event(id="e1", inserted_at=t1),
                _task_event(id="e2", inserted_at=t2),
            ]
        )
        source.clear_room("room-1", NS)
        second = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,
        )
        assert [e["id"] for e in second] == ["e1", "e2"]


class TestRestStateSourceFailure:
    @pytest.mark.asyncio
    async def test_raises_thenvoi_tool_error_after_retry(self) -> None:
        class FailingTools:
            async def fetch_room_context(self, **_: object) -> dict:
                raise RuntimeError("boom")

        source = RestCrewAIFlowStateSource(retry_attempts=1)
        with pytest.raises(ThenvoiToolError):
            await source.load_task_events(
                room_id="room-1",
                metadata_namespace=NS,
                tools=FailingTools(),  # type: ignore[arg-type]
                history=None,
            )


class TestRestStateSourceNonBootstrapReplay:
    """The whole point: AgentInput.history is empty but state is still
    reconstructed from the platform endpoint."""

    @pytest.mark.asyncio
    async def test_reconstructs_pending_state_with_empty_history(self) -> None:
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tools = FakeAgentTools(
            room_context=[
                _task_event(
                    id="e1",
                    inserted_at=t,
                    payload={
                        "schema_version": 1,
                        "room_id": "room-1",
                        "run_id": "msg-1",
                        "parent_message_id": "msg-1",
                        "status": "delegated_pending",
                        "stage": "delegated",
                    },
                )
            ]
        )
        source = RestCrewAIFlowStateSource()
        events = await source.load_task_events(
            room_id="room-1",
            metadata_namespace=NS,
            tools=tools,
            history=None,  # empty / non-bootstrap
        )
        assert len(events) == 1
        assert events[0]["id"] == "e1"


# ---------------------------------------------------------------------------
# HistoryCrewAIFlowStateSource
# ---------------------------------------------------------------------------


class TestHistoryStateSource:
    def test_constructor_requires_acknowledge(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            HistoryCrewAIFlowStateSource()  # type: ignore[call-arg]
        with pytest.raises(ThenvoiConfigError):
            HistoryCrewAIFlowStateSource(acknowledge_test_only=False)
        # Truthy non-True must also fail per "exactly True" contract.
        with pytest.raises(ThenvoiConfigError):
            HistoryCrewAIFlowStateSource(acknowledge_test_only="yes")  # type: ignore[arg-type]

    def test_constructor_succeeds_with_explicit_true(self) -> None:
        source = HistoryCrewAIFlowStateSource(acknowledge_test_only=True)
        assert source is not None

    @pytest.mark.asyncio
    async def test_filters_history_by_namespace(self) -> None:
        source = HistoryCrewAIFlowStateSource(acknowledge_test_only=True)
        history = [
            {"message_type": "text", "metadata": {NS: {}}},  # not a task
            {"message_type": "task", "metadata": {"other": {}}},  # wrong ns
            {"message_type": "task", "metadata": {NS: {"x": 1}}, "id": "k"},
        ]
        out = await source.load_task_events(
            room_id="r",
            metadata_namespace=NS,
            tools=FakeAgentTools(),
            history=history,
        )
        assert len(out) == 1
        assert out[0]["id"] == "k"

    @pytest.mark.asyncio
    async def test_warns_once_on_empty_history(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        source = HistoryCrewAIFlowStateSource(acknowledge_test_only=True)
        with caplog.at_level("WARNING"):
            await source.load_task_events(
                room_id="r",
                metadata_namespace=NS,
                tools=FakeAgentTools(),
                history=[],
            )
            await source.load_task_events(
                room_id="r",
                metadata_namespace=NS,
                tools=FakeAgentTools(),
                history=[],
            )
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "RestCrewAIFlowStateSource" in warnings[0].message
