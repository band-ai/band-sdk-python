"""Codex adapter: parse_plan_steps tolerance for malformed payloads."""

from __future__ import annotations

from band.integrations.codex.types import parse_plan_steps


class TestPlanStepsRobustness:
    """parse_plan_steps tolerance for malformed payloads."""

    def test_parse_plan_steps_handles_non_dict_plan(self) -> None:
        """parse_plan_steps must not crash when `plan` is not a dict."""

        assert parse_plan_steps({"plan": "not-a-dict"}) == []
        assert parse_plan_steps({"plan": ["also", "not", "a", "dict"]}) == []
        assert parse_plan_steps({"plan": None}) == []

    def test_parse_plan_steps_reads_top_level_when_plan_absent(self) -> None:
        """When there's no 'plan' key, parse_plan_steps looks at top-level steps."""

        steps = parse_plan_steps({"steps": [{"text": "A", "status": "pending"}]})
        assert len(steps) == 1
        assert steps[0].step == "A"
