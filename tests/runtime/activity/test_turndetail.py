from __future__ import annotations

from band_rest import AgentActivityDetail

from band.runtime.activity import TurnActivity


class TestTurnActivity:
    def test_a_turn_starts_at_reasoning(self) -> None:
        assert TurnActivity().detail == AgentActivityDetail(kind="reasoning")

    def test_only_the_kind_and_tool_name_are_kept(self) -> None:
        activity = TurnActivity()
        activity.apply(
            activity=AgentActivityDetail(
                kind="tool_call", name="Bash", arguments="private"
            )
        )
        assert activity.detail.model_dump(exclude_unset=True) == {
            "kind": "tool_call",
            "name": "Bash",
        }
        activity.apply(activity=AgentActivityDetail(kind="memory", name="private"))
        assert activity.detail.model_dump(exclude_unset=True) == {"kind": "memory"}

    def test_a_nameless_tool_is_reported_as_unknown(self) -> None:
        activity = TurnActivity()
        activity.apply(activity=AgentActivityDetail(kind="tool_call", name=""))
        assert activity.detail == AgentActivityDetail(kind="tool_call", name="unknown")

    def test_a_person_wait_overrides_progress_without_losing_it(self) -> None:
        activity = TurnActivity()
        activity.apply(activity=AgentActivityDetail(kind="tool_call", name="Read"))
        activity.apply(waiting=True)
        activity.apply(activity=AgentActivityDetail(kind="memory"))
        assert activity.detail == AgentActivityDetail(kind="permission")
        activity.apply(waiting=False)
        assert activity.detail == AgentActivityDetail(kind="memory")
