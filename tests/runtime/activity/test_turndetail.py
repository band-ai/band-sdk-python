"""A turn shows its newest running activity, else reasoning; a person's wait overrides both."""

from __future__ import annotations

from band_rest import AgentActivityDetail

from band.runtime.activity import (
    MEMORY,
    PERMISSION,
    REASONING,
    UNKNOWN_ACTIVITY_TOOL_NAME,
    TurnActivity,
    tool_call_activity,
)


class TestTurnActivity:
    def test_a_turn_starts_at_reasoning(self) -> None:
        assert TurnActivity().detail == REASONING

    def test_a_tool_keeps_only_its_kind_and_name(self) -> None:
        activity = TurnActivity()
        activity.start(
            "bash",
            AgentActivityDetail(kind="tool_call", name="Bash", arguments="private"),
        )
        assert activity.detail.model_dump(exclude_unset=True) == {
            "kind": "tool_call",
            "name": "Bash",
        }

    def test_memory_keeps_only_its_kind(self) -> None:
        activity = TurnActivity()
        activity.start("memory", AgentActivityDetail(kind="memory", name="private"))
        assert activity.detail.model_dump(exclude_unset=True) == {"kind": "memory"}

    def test_a_nameless_tool_is_reported_as_unknown(self) -> None:
        activity = TurnActivity()
        activity.start("nameless", AgentActivityDetail(kind="tool_call", name=""))
        assert activity.detail == tool_call_activity(UNKNOWN_ACTIVITY_TOOL_NAME)

    def test_ending_the_only_activity_returns_to_reasoning(self) -> None:
        activity = TurnActivity()
        activity.start("read", tool_call_activity("Read"))
        activity.end("read")
        assert activity.detail == REASONING

    def test_ending_an_unknown_key_changes_nothing(self) -> None:
        activity = TurnActivity()
        activity.start("read", tool_call_activity("Read"))
        activity.end("never-started")
        assert activity.detail == tool_call_activity("Read")

    def test_the_newest_running_activity_is_shown(self) -> None:
        activity = TurnActivity()
        activity.start("read", tool_call_activity("Read"))
        activity.start("bash", tool_call_activity("Bash"))
        assert activity.detail == tool_call_activity("Bash")

    def test_ending_the_newest_shows_the_one_still_running(self) -> None:
        activity = TurnActivity()
        activity.start("read", tool_call_activity("Read"))
        activity.start("bash", tool_call_activity("Bash"))
        activity.end("bash")
        assert activity.detail == tool_call_activity("Read")

    def test_ending_an_older_activity_keeps_the_newest(self) -> None:
        activity = TurnActivity()
        activity.start("read", tool_call_activity("Read"))
        activity.start("bash", tool_call_activity("Bash"))
        activity.end("read")
        assert activity.detail == tool_call_activity("Bash")

    def test_restarting_a_key_renames_it_without_making_it_newest(self) -> None:
        activity = TurnActivity()
        activity.start("tool", tool_call_activity("pending"))
        activity.start("bash", tool_call_activity("Bash"))
        activity.start("tool", tool_call_activity("Read"))
        shown_after_restart = activity.detail
        activity.end("bash")
        assert (shown_after_restart, activity.detail) == (
            tool_call_activity("Bash"),
            tool_call_activity("Read"),
        )

    def test_a_person_wait_overrides_progress_without_losing_it(self) -> None:
        activity = TurnActivity()
        activity.start("read", tool_call_activity("Read"))
        owner = activity.acquire_permission()
        activity.start("memory", MEMORY)
        assert (activity.detail, activity.progress) == (PERMISSION, MEMORY)
        activity.release_permission(owner)
        assert activity.detail == MEMORY
