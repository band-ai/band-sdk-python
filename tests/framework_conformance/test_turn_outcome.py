"""TurnOutcome behavior."""

from __future__ import annotations

import pytest
from band_sdk_core import TurnVerdict

from band.testing import MISSING_REPLY_FAILURE
from band.testing.fake_tools import reported_failures
from tests.framework_configs.adapters import ADAPTER_CONFIGS
from tests.framework_conformance.outcomeprobes import (
    ANSWER,
    COMPLETING_SCRIPTS,
    PINNED_OUTSIDE_CONFIGS,
    REPLY,
    TURN_OUTCOME_PROBES,
    UNJUDGED_FRAMEWORK_IDS,
    TurnResult,
    band_adapters,
    probe_params,
    probe_running,
    relaying_probe_params,
    run_turn,
)
from tests.framework_conformance.turnprobes import (
    TurnScript,
    turn_tools,
)


class TestTurnOutcome:
    @pytest.mark.parametrize("framework_id", probe_params())
    @pytest.mark.parametrize("script_name", sorted(COMPLETING_SCRIPTS))
    async def test_a_turn_that_answered_or_worked_completes(
        self, framework_id: str, script_name: str
    ) -> None:
        result = await run_turn(
            TURN_OUTCOME_PROBES[framework_id], COMPLETING_SCRIPTS[script_name]
        )

        assert result.verdict == TurnVerdict.Complete
        assert result.failures == []

    @pytest.mark.parametrize("framework_id", probe_params())
    async def test_a_turn_that_did_nothing_is_reported_once(
        self, framework_id: str
    ) -> None:
        result = await run_turn(TURN_OUTCOME_PROBES[framework_id], TurnScript())

        assert result.verdict == TurnVerdict.MissingReply
        assert result.failures == [MISSING_REPLY_FAILURE]
        assert result.messages == []

    @pytest.mark.parametrize(
        "framework_id",
        [name for name in probe_params() if TURN_OUTCOME_PROBES[name].settle],
    )
    async def test_an_adapter_settled_turn_completes(self, framework_id: str) -> None:
        settle = TURN_OUTCOME_PROBES[framework_id].settle
        assert settle is not None
        tools = turn_tools()

        await settle(tools)

        assert tools.turn.complete
        assert reported_failures(tools) == []

    @pytest.mark.parametrize("framework_id", relaying_probe_params())
    async def test_a_relaying_adapter_posts_the_final_text_once(
        self, framework_id: str
    ) -> None:
        result = await run_turn(
            TURN_OUTCOME_PROBES[framework_id], TurnScript(final_text=ANSWER)
        )

        assert result == TurnResult(
            TurnVerdict.Complete, messages=[ANSWER], failures=[]
        )

    @pytest.mark.parametrize("framework_id", relaying_probe_params())
    async def test_a_tool_reply_suppresses_the_closing_text(
        self, framework_id: str
    ) -> None:
        result = await run_turn(
            TURN_OUTCOME_PROBES[framework_id],
            TurnScript((REPLY,), final_text="Anything else?"),
        )

        assert result == TurnResult(
            TurnVerdict.Complete, messages=[ANSWER], failures=[]
        )

    @pytest.mark.parametrize("framework_id", ["acp", "cursor_acp", "codex"])
    async def test_native_text_cannot_complete_a_tool_authoritative_turn(
        self,
        framework_id: str,
    ) -> None:
        result = await run_turn(
            TURN_OUTCOME_PROBES[framework_id], TurnScript(final_text=ANSWER)
        )
        assert result == TurnResult(
            TurnVerdict.MissingReply, [], [MISSING_REPLY_FAILURE]
        )

    @pytest.mark.parametrize("framework_id", ["acp", "cursor_acp", "codex"])
    @pytest.mark.parametrize("script_name", sorted(COMPLETING_SCRIPTS))
    async def test_native_text_preserves_successful_tool_outcomes(
        self, framework_id: str, script_name: str
    ) -> None:
        script = COMPLETING_SCRIPTS[script_name]
        result = await run_turn(
            TURN_OUTCOME_PROBES[framework_id],
            TurnScript(script.tool_calls, final_text=ANSWER),
        )
        assert result == TurnResult(
            TurnVerdict.Complete, [ANSWER] if script_name == "reply" else [], []
        )

    def test_only_the_declared_adapters_go_unjudged(self) -> None:
        unjudged = {
            cfg.framework_id
            for cfg in ADAPTER_CONFIGS
            if not cfg.adapter_factory().judges_turns
        }

        assert unjudged == UNJUDGED_FRAMEWORK_IDS

    def test_every_band_adapter_is_registered_probed_or_pinned(self) -> None:
        registered = {type(cfg.adapter_factory()).__name__ for cfg in ADAPTER_CONFIGS}
        adapters = band_adapters()
        unprobed = {
            name
            for name, cls in adapters.items()
            if name not in registered
            and probe_running(cls, adapters) not in TURN_OUTCOME_PROBES
        }

        assert unprobed == set(PINNED_OUTSIDE_CONFIGS)

    def test_every_judged_adapter_has_a_probe(self) -> None:
        unprobed = sorted(
            cfg.framework_id
            for cfg in ADAPTER_CONFIGS
            if cfg.adapter_factory().judges_turns
            and cfg.framework_id not in TURN_OUTCOME_PROBES
        )

        assert unprobed == []
